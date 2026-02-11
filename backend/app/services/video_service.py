from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
import random
from pathlib import Path
from collections import deque
from threading import Thread
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool
from moviepy import AudioFileClip, CompositeAudioClip, CompositeVideoClip, ImageClip, TextClip, afx, concatenate_videoclips

from ..config import project_path, settings
from ..models import CharacterSuggestion, GenerateVideoRequest, JobStatus
from ..state import job_store
from ..voice_catalog import VOICE_INFOS, recommend_voice
from .image_service import ImageGenerationError, use_reference_or_generate
from .llm_service import (
    build_segment_image_bundle,
    group_sentences,
    segment_by_fixed,
    segment_by_smart,
    split_sentences,
)
from .scene_cache_service import (
    build_scene_descriptor,
    ensure_scene_cache_paths,
    find_reusable_scene_image,
    force_llm_select_scene_image,
    list_scene_cache_entries,
    render_cached_image_to_output,
    save_scene_image_cache_entry,
)
from .tts_service import get_audio_duration, synthesize_tts


logger = logging.getLogger(__name__)

_SUBTITLE_FONT_RESOLVED = False
_SUBTITLE_FONT_PATH: str | None = None

_VIDEO_AUDIO_BITRATE = "96k"
_TTS_GAIN = 1.15
_FINAL_AUDIO_GAIN = 3.0
_NARRATOR_VOICE_ID = "zh-CN-YunxiNeural"
_DIALOG_QUOTE_PAIRS = {
    '"': '"',
    "\u201c": "\u201d",  # “ ”
}


def _resolve_render_profile(mode: str | None) -> dict:
    key = (mode or "").strip().lower()
    if key == "quality":
        return {
            "clip_preset": "slow",
            "clip_crf": "20",
            "final_preset": "medium",
            "final_crf": "21",
            "clip_fps": None,
            "bgm_video_copy": False,
        }
    if key == "balanced":
        return {
            "clip_preset": "veryfast",
            "clip_crf": "23",
            "final_preset": "veryfast",
            "final_crf": "24",
            "clip_fps": None,
            "bgm_video_copy": True,
        }
    return {
        "clip_preset": "ultrafast",
        "clip_crf": "29",
        "final_preset": "veryfast",
        "final_crf": "30",
        "clip_fps": None,
        "bgm_video_copy": True,
    }


def _parse_resolution(value: str) -> tuple[int, int]:
    try:
        width_raw, height_raw = value.lower().split("x")
        return max(320, int(width_raw)), max(320, int(height_raw))
    except Exception:
        return 1080, 1920


def _pick_character(characters: list[CharacterSuggestion], text: str) -> CharacterSuggestion:
    if not characters:
        return CharacterSuggestion(name="narrator", role="narrator", voice_id="zh-CN-YunxiNeural")
    for item in characters:
        if item.name and item.name in text:
            return item
    return characters[0]


def _sanitize_character_voices(characters: list[CharacterSuggestion], narrator_voice: str = _NARRATOR_VOICE_ID) -> list[CharacterSuggestion]:
    if not characters:
        return []

    available = [voice.id for voice in VOICE_INFOS]
    if narrator_voice not in available:
        narrator_voice = available[0] if available else _NARRATOR_VOICE_ID

    used: set[str] = {narrator_voice}
    prioritized = sorted(
        characters,
        key=lambda item: int(item.importance or 0),
        reverse=True,
    )

    for character in prioritized:
        current = (character.voice_id or "").strip()
        if current and current not in used and current in available:
            used.add(current)
            continue

        recommended = recommend_voice(character.role or "", character.personality or "")
        if recommended and recommended in available and recommended not in used:
            character.voice_id = recommended
            used.add(recommended)
            continue

        fallback = next((voice_id for voice_id in available if voice_id not in used), None)
        if fallback:
            character.voice_id = fallback
            used.add(fallback)
            continue

        character.voice_id = current if current in available else narrator_voice

    return characters


def _extract_quote_blocks(text: str) -> list[tuple[str, int, int]]:
    content = (text or "").strip()
    if not content:
        return []

    blocks: list[tuple[str, int, int]] = []
    index = 0
    length = len(content)
    while index < length:
        opener = content[index]
        closer = _DIALOG_QUOTE_PAIRS.get(opener)
        if not closer:
            index += 1
            continue

        end = content.find(closer, index + 1)
        if end <= index + 1:
            index += 1
            continue

        quote_text = content[index + 1 : end].strip()
        if quote_text:
            blocks.append((quote_text, index, end))
        index = end + 1
    return blocks


def _pick_dialogue_voice(characters: list[CharacterSuggestion], dialog_index: int, narrator_voice: str) -> str:
    available = [item for item in characters if (item.voice_id or "").strip() and item.voice_id != narrator_voice]
    if not available:
        return narrator_voice
    return available[dialog_index % len(available)].voice_id


def _merge_tts_pieces(pieces: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    for text, voice in pieces:
        current_text = (text or "").strip()
        if not current_text:
            continue
        current_voice = (voice or _NARRATOR_VOICE_ID).strip() or _NARRATOR_VOICE_ID
        if merged and merged[-1][1] == current_voice:
            merged[-1] = (merged[-1][0] + current_text, current_voice)
        else:
            merged.append((current_text, current_voice))
    return merged


def _build_tts_pieces(text: str, characters: list[CharacterSuggestion], narrator_voice: str) -> list[tuple[str, str]]:
    clean = (text or "").strip()
    if not clean:
        return []

    quotes = _extract_quote_blocks(clean)
    if not quotes:
        return [(clean, narrator_voice)]

    pieces: list[tuple[str, str]] = []
    cursor = 0
    dialog_index = 0
    for quoted, quote_start, quote_end in quotes:
        if quote_start < cursor:
            continue
        if quote_end <= quote_start:
            continue

        narration = clean[cursor:quote_start].strip()
        if narration:
            pieces.append((narration, narrator_voice))

        pieces.append((quoted, _pick_dialogue_voice(characters, dialog_index, narrator_voice)))
        dialog_index += 1
        cursor = quote_end + 1

    tail = clean[cursor:].strip()
    if tail:
        pieces.append((tail, narrator_voice))

    if not pieces:
        return [(clean, narrator_voice)]
    return _merge_tts_pieces(pieces)


async def _synthesize_segment_tts(
    text: str,
    characters: list[CharacterSuggestion],
    output_path: Path,
    narrator_voice: str = _NARRATOR_VOICE_ID,
) -> tuple[Path, float]:
    parts = _build_tts_pieces(text, characters, narrator_voice)
    if not parts:
        return await synthesize_tts(text=text, voice=narrator_voice, output_path=output_path)

    if len(parts) == 1:
        only_text, only_voice = parts[0]
        return await synthesize_tts(text=only_text, voice=only_voice, output_path=output_path)

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return await synthesize_tts(text=text, voice=narrator_voice, output_path=output_path)

    temp_parts = output_path.parent / f"{output_path.stem}_tts_parts"
    temp_parts.mkdir(parents=True, exist_ok=True)
    part_files: list[Path] = []
    total_duration = 0.0

    try:
        for idx, (piece_text, piece_voice) in enumerate(parts):
            part_path = temp_parts / f"part_{idx:03d}.mp3"
            generated_path, piece_duration = await synthesize_tts(text=piece_text, voice=piece_voice, output_path=part_path)
            part_files.append(generated_path)
            total_duration += max(piece_duration, 0.0)

        concat_file = temp_parts / "concat_list.txt"
        concat_lines = []
        for path in part_files:
            escaped = str(path.resolve()).replace("'", "'\\''")
            concat_lines.append(f"file '{escaped}'")
        concat_file.write_text("\n".join(concat_lines), encoding="utf-8")

        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(output_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and output_path.exists():
            duration = get_audio_duration(output_path)
            return output_path, (duration if duration > 0 else total_duration)

        logger.warning("TTS concat failed, fallback to narrator voice: %s", (proc.stderr or "")[:400])
        return await synthesize_tts(text=text, voice=narrator_voice, output_path=output_path)
    finally:
        for file in part_files:
            try:
                file.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            (temp_parts / "concat_list.txt").unlink(missing_ok=True)
        except Exception:
            pass
        try:
            temp_parts.rmdir()
        except Exception:
            pass


def _resolve_subtitle_font_path() -> str | None:
    configured = (settings.subtitle_font_path or "").strip()
    candidates: list[Path] = []
    if configured:
        raw = Path(configured)
        candidates.append(raw if raw.is_absolute() else project_path(configured))

    candidates.extend(
        [
            project_path("assets/fonts/NotoSansSC-Regular.otf"),
            project_path("assets/fonts/NotoSansCJKsc-Regular.otf"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyhbd.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _subtitle_font_path() -> str | None:
    global _SUBTITLE_FONT_RESOLVED, _SUBTITLE_FONT_PATH
    if _SUBTITLE_FONT_RESOLVED:
        return _SUBTITLE_FONT_PATH

    _SUBTITLE_FONT_PATH = _resolve_subtitle_font_path()
    _SUBTITLE_FONT_RESOLVED = True
    if _SUBTITLE_FONT_PATH:
        logger.info("Subtitle font: %s", _SUBTITLE_FONT_PATH)
    else:
        logger.warning("No subtitle font found for CJK text; set SUBTITLE_FONT_PATH if subtitle is garbled")
    return _SUBTITLE_FONT_PATH


def _split_subtitle_sentences(text: str) -> list[str]:
    clean = (text or "").replace("\r\n", "").replace("\n", "").replace("\r", "")
    clean = re.sub(r"[ \t\f\v]+", " ", clean).strip()
    if not clean:
        return []

    delimiters = {
        ",",
        ".",
        "!",
        "?",
        ";",
        "，",  # ?
        "、",  # ?
        "。",  # ?
        "！",  # ?
        "？",  # ?
        "；",  # ?
    }
    units: list[str] = []
    current_chars: list[str] = []
    length = len(clean)

    for index, char in enumerate(clean):
        current_chars.append(char)
        if char not in delimiters:
            continue

        next_char = clean[index + 1] if index + 1 < length else ""
        prev_char = clean[index - 1] if index - 1 >= 0 else ""
        if next_char in delimiters:
            continue
        if char == "?" and prev_char == "?":
            continue

        sentence = "".join(current_chars).strip()
        if sentence:
            units.append(sentence)
        current_chars = []

    tail = "".join(current_chars).strip()
    if tail:
        units.append(tail)

    return units or [clean]


def _subtitle_timeline(text: str, duration: float) -> list[tuple[str, float, float]]:
    units = _split_subtitle_sentences(text)
    if not units:
        return []

    safe_duration = max(duration, 0.1)
    weights = [max(1, len(re.sub(r"\s+", "", item))) for item in units]
    total_weight = sum(weights)

    timeline: list[tuple[str, float, float]] = []
    cursor = 0.0
    for index, unit in enumerate(units):
        if index == len(units) - 1:
            end_time = safe_duration
        else:
            end_time = min(safe_duration, cursor + (safe_duration * weights[index] / total_weight))
        if end_time <= cursor:
            end_time = min(safe_duration, cursor + 0.05)
        timeline.append((unit, cursor, end_time))
        cursor = end_time

    return timeline


def _subtitle_clips(text: str, duration: float, resolution: tuple[int, int], style: str) -> list[TextClip]:
    width, height = resolution
    fontsize = 46
    color = "#FFFFFF"
    stroke_color = "#111111"
    if style == "center":
        fontsize = 56
    elif style == "danmaku":
        fontsize = 38

    if style in {"highlight", "yellow_black"}:
        color = "#F9E96A"
        stroke_color = "#111111"
    elif style == "black_white":
        color = "#111111"
        stroke_color = "#FFFFFF"
    elif style in {"basic", "white_black"}:
        color = "#FFFFFF"
        stroke_color = "#111111"

    font_path = _subtitle_font_path()
    subtitles: list[TextClip] = []

    safe_top = max(12, int(height * 0.03))
    safe_bottom = max(24, int(height * 0.06))

    if style == "center":
        subtitle_box_h = max(120, int(height * 0.30))
    elif style == "danmaku":
        subtitle_box_h = max(90, int(height * 0.16))
    else:
        subtitle_box_h = max(110, int(height * 0.20))

    def _resolve_y(clip_h: int) -> int:
        clip_height = max(1, int(clip_h or 1))
        if style == "center":
            preferred = int((height - clip_height) * 0.5)
        elif style == "danmaku":
            preferred = int(height * 0.18)
        else:
            preferred = int(height * 0.78)

        max_y = max(safe_top, height - clip_height - safe_bottom)
        return min(max(preferred, safe_top), max_y)

    for sentence, start_at, end_at in _subtitle_timeline(text, duration):
        text_kwargs = {
            "text": sentence,
            "font_size": fontsize,
            "color": color,
            "stroke_color": stroke_color,
            "stroke_width": 2,
            "method": "caption",
            "size": (width - 120, subtitle_box_h),
            "margin": (10, 18),
            "interline": max(6, int(fontsize * 0.22)),
            "text_align": "center",
            "horizontal_align": "center",
            "vertical_align": "center",
        }
        if font_path:
            text_kwargs["font"] = font_path

        try:
            clip = TextClip(**text_kwargs)
        except Exception:
            if not font_path:
                logger.exception("Subtitle render failed")
                continue
            logger.warning("Subtitle render failed with font '%s', retrying default font", font_path)
            text_kwargs.pop("font", None)
            try:
                clip = TextClip(**text_kwargs)
            except Exception:
                logger.exception("Subtitle render failed after retry")
                continue

        y_pos = _resolve_y(getattr(clip, "h", 0))
        subtitles.append(clip.with_start(start_at).with_duration(max(0.05, end_at - start_at)).with_position(("center", y_pos)))

    return subtitles


def _build_motion_image_clip(
    image_path: str,
    duration: float,
    resolution: tuple[int, int],
    motion: str,
):
    target_w, target_h = resolution
    safe_duration = max(duration, 0.1)

    base_clip = ImageClip(image_path).with_duration(safe_duration)
    source_w = max(1.0, float(base_clip.w))
    source_h = max(1.0, float(base_clip.h))

    # Cover fit: fill entire frame without distortion.
    cover_scale = max(target_w / source_w, target_h / source_h)
    scaled_w = source_w * cover_scale
    scaled_h = source_h * cover_scale

    # Prefer top-to-bottom movement. If there is no vertical overflow,
    # apply a tiny extra zoom to create vertical travel.
    min_vertical_pan = max(24.0, target_h * 0.08)
    extra_zoom = 1.0
    if (scaled_h - target_h) < min_vertical_pan:
        extra_zoom = (target_h + min_vertical_pan) / max(1.0, scaled_h)

    final_w = int(round(scaled_w * extra_zoom))
    final_h = int(round(scaled_h * extra_zoom))
    motion_clip = base_clip.resized(new_size=(final_w, final_h))

    overflow_x = max(0.0, float(final_w - target_w))
    overflow_y = max(0.0, float(final_h - target_h))

    vertical_possible = overflow_y > 1.0
    horizontal_possible = overflow_x > 1.0

    target_motion = motion or "vertical"
    if target_motion not in {"vertical", "horizontal", "auto"}:
        target_motion = "vertical"

    if target_motion == "vertical":
        motion_axis = "vertical" if vertical_possible else ("horizontal" if horizontal_possible else "none")
    elif target_motion == "horizontal":
        motion_axis = "horizontal" if horizontal_possible else ("vertical" if vertical_possible else "none")
    else:
        motion_axis = "vertical" if vertical_possible else ("horizontal" if horizontal_possible else "none")

    if motion_axis == "vertical":
        start_x, end_x = -overflow_x / 2.0, -overflow_x / 2.0
        start_y, end_y = 0.0, -overflow_y
    elif motion_axis == "horizontal":
        start_x, end_x = 0.0, -overflow_x
        start_y, end_y = -overflow_y / 2.0, -overflow_y / 2.0
    else:
        return motion_clip.with_position(("center", "center"))

    def position_at(t: float) -> tuple[float, float]:
        progress = min(max((t or 0.0) / safe_duration, 0.0), 1.0)
        x = start_x + (end_x - start_x) * progress
        y = start_y + (end_y - start_y) * progress
        return x, y

    return motion_clip.with_position(position_at)


def _render_clip_sync(
    image_path: str,
    audio_path: str,
    text: str,
    duration: float,
    output_path: Path,
    fps: int,
    resolution: tuple[int, int],
    subtitle_style: str,
    camera_motion: str,
    render_mode: str,
) -> None:
    profile = _resolve_render_profile(render_mode)
    clip_fps = int(profile.get("clip_fps") or fps)
    image_clip = _build_motion_image_clip(
        image_path=image_path,
        duration=duration,
        resolution=resolution,
        motion=camera_motion,
    )
    audio_clip = AudioFileClip(audio_path).with_volume_scaled(_TTS_GAIN)
    base = image_clip.with_audio(audio_clip)
    subtitle_clips = _subtitle_clips(text, duration, resolution, subtitle_style)
    composed = CompositeVideoClip([base, *subtitle_clips], size=resolution).with_duration(duration)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    composed.write_videofile(
        str(output_path),
        fps=clip_fps,
        audio_codec="aac",
        codec="libx264",
        preset=str(profile.get("clip_preset") or "veryfast"),
        ffmpeg_params=["-crf", str(profile.get("clip_crf") or "27"), "-movflags", "+faststart", "-b:a", _VIDEO_AUDIO_BITRATE],
        logger=None,
    )

    composed.close()
    for subtitle in subtitle_clips:
        subtitle.close()
    base.close()
    audio_clip.close()
    image_clip.close()


def _render_final_sync(
    clip_paths: list[str],
    output_path: Path,
    fps: int,
    bgm_enabled: bool,
    bgm_volume: float,
    render_mode: str,
) -> None:
    profile = _resolve_render_profile(render_mode)
    final_preset = str(profile.get("final_preset") or "veryfast")
    final_crf = str(profile.get("final_crf") or "28")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin and clip_paths:
        with tempfile.TemporaryDirectory(prefix="genvideo_concat_") as tmp_dir:
            concat_file = Path(tmp_dir) / "concat_list.txt"
            concat_lines = []
            for clip_path in clip_paths:
                escaped = str(Path(clip_path).resolve()).replace("'", "'\\''")
                concat_lines.append(f"file '{escaped}'")
            concat_file.write_text("\n".join(concat_lines), encoding="utf-8")

            merged_no_bgm = Path(tmp_dir) / "merged_no_bgm.mp4"
            concat_cmd = [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(merged_no_bgm),
            ]
            concat_proc = subprocess.run(concat_cmd, capture_output=True, text=True)
            if concat_proc.returncode == 0 and merged_no_bgm.exists():
                bgm_enabled = bool(bgm_enabled)
                bgm_volume = max(0.0, min(float(bgm_volume), 1.0))
                bgm_path = project_path("assets/bgm.mp3")
                if not bgm_path.exists():
                    bgm_path = project_path("assets/bgm/happinessinmusic-rock-trailer-417598.mp3")

                if bgm_enabled and bgm_volume > 0 and bgm_path.exists():
                    if bool(profile.get("bgm_video_copy", True)):
                        mix_cmd = [
                            ffmpeg_bin,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            str(merged_no_bgm),
                            "-stream_loop",
                            "-1",
                            "-i",
                            str(bgm_path),
                            "-filter_complex",
                            f"[1:a]volume={bgm_volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0[tmp];[tmp]volume={_FINAL_AUDIO_GAIN}[mix]",
                            "-map",
                            "0:v:0",
                            "-map",
                            "[mix]",
                            "-c:v",
                            "copy",
                            "-c:a",
                            "aac",
                            "-b:a",
                            _VIDEO_AUDIO_BITRATE,
                            "-movflags",
                            "+faststart",
                            str(output_path),
                        ]
                    else:
                        mix_cmd = [
                            ffmpeg_bin,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            str(merged_no_bgm),
                            "-stream_loop",
                            "-1",
                            "-i",
                            str(bgm_path),
                            "-filter_complex",
                            f"[1:a]volume={bgm_volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0[tmp];[tmp]volume={_FINAL_AUDIO_GAIN}[mix]",
                            "-map",
                            "0:v:0",
                            "-map",
                            "[mix]",
                            "-c:v",
                            "libx264",
                            "-preset",
                            final_preset,
                            "-crf",
                            final_crf,
                            "-c:a",
                            "aac",
                            "-b:a",
                            _VIDEO_AUDIO_BITRATE,
                            "-movflags",
                            "+faststart",
                            str(output_path),
                        ]
                    mix_proc = subprocess.run(mix_cmd, capture_output=True, text=True)
                    if mix_proc.returncode == 0 and output_path.exists():
                        logger.info("Final compose via ffmpeg concat+bgm mix")
                        return
                    logger.warning("ffmpeg bgm mix failed, fallback to python compose: %s", (mix_proc.stderr or "")[:400])
                else:
                    boost_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(merged_no_bgm),
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0",
                        "-c:v",
                        "copy",
                        "-filter:a",
                        f"volume={_FINAL_AUDIO_GAIN}",
                        "-c:a",
                        "aac",
                        "-b:a",
                        _VIDEO_AUDIO_BITRATE,
                        "-movflags",
                        "+faststart",
                        str(output_path),
                    ]
                    boost_proc = subprocess.run(boost_cmd, capture_output=True, text=True)
                    if boost_proc.returncode == 0 and output_path.exists():
                        logger.info("Final compose via ffmpeg concat + final gain")
                        return
                    logger.warning("ffmpeg final gain failed, fallback to concat copy: %s", (boost_proc.stderr or "")[:400])
                    shutil.copyfile(merged_no_bgm, output_path)
                    logger.info("Final compose via ffmpeg concat copy")
                    return
            else:
                logger.warning("ffmpeg concat copy failed, fallback to python compose: %s", (concat_proc.stderr or "")[:400])

    video_clips = []
    bgm_clips = []
    final = None
    final_with_audio = None
    try:
        from moviepy import VideoFileClip

        for clip_path in clip_paths:
            video_clips.append(VideoFileClip(clip_path))

        final = concatenate_videoclips(video_clips, method="compose")

        bgm_enabled = bool(bgm_enabled)
        bgm_volume = max(0.0, min(float(bgm_volume), 1.0))
        bgm_path = project_path("assets/bgm.mp3")
        if not bgm_path.exists():
            bgm_path = project_path("assets/bgm/happinessinmusic-rock-trailer-417598.mp3")
        if bgm_enabled and bgm_volume > 0:
            if bgm_path.exists():
                final_duration = max(float(final.duration or 0.0), 0.0)
                if final_duration > 0:
                    bgm_source = AudioFileClip(str(bgm_path))
                    bgm_loop = (
                        bgm_source.with_effects([afx.AudioLoop(duration=final_duration)])
                        .with_duration(final_duration)
                        .with_volume_scaled(bgm_volume)
                    )
                    bgm_clips.extend([bgm_loop, bgm_source])

                    if final.audio is not None:
                        mixed_audio = CompositeAudioClip([final.audio, bgm_loop]).with_duration(final_duration)
                        final_with_audio = final.with_audio(mixed_audio)
                    else:
                        final_with_audio = final.with_audio(bgm_loop)
                    logger.info("BGM mixed into final video: path=%s volume=%.3f", bgm_path, bgm_volume)
            else:
                logger.warning("BGM file does not exist, skip mixing: %s", bgm_path)

        final_output = final_with_audio or final
        boosted_output = final_output
        if final_output.audio is not None:
            boosted_output = final_output.with_audio(final_output.audio.with_volume_scaled(_FINAL_AUDIO_GAIN))

        boosted_output.write_videofile(
            str(output_path),
            fps=fps,
            audio_codec="aac",
            codec="libx264",
            preset=final_preset,
            ffmpeg_params=["-crf", final_crf, "-movflags", "+faststart", "-b:a", _VIDEO_AUDIO_BITRATE],
            logger=None,
        )

        if boosted_output is not final_output:
            boosted_output.close()
    finally:
        if final_with_audio is not None:
            final_with_audio.close()
        if final is not None:
            final.close()
        for clip in bgm_clips:
            clip.close()
        for clip in video_clips:
            clip.close()


async def _segment_text(payload: GenerateVideoRequest) -> tuple[list[str], int]:
    if payload.segment_method == "fixed":
        segments = segment_by_fixed(payload.text)
        return segments, 0

    if payload.segment_method == "smart":
        segments = await segment_by_smart(payload.text, payload.model_id)
        return segments, 0

    sentences = split_sentences(payload.text)
    segments = group_sentences(sentences, payload.sentences_per_segment)
    return segments, len(sentences)


def _update_job(
    job_id: str,
    base_url: str,
    status: str,
    progress: float,
    step: str,
    message: str,
    output_video_path: str | None = None,
    clip_paths: list[str] | None = None,
) -> None:
    previews: list[str] = []
    if clip_paths:
        previews = [f"{base_url}/api/jobs/{job_id}/clips/{index}" for index, _ in enumerate(clip_paths)]
    job_store.set(
        JobStatus(
            job_id=job_id,
            status=status,
            progress=progress,
            step=step,
            message=message,
            output_video_url=f"{base_url}/api/jobs/{job_id}/video" if output_video_path else None,
            output_video_path=output_video_path,
            clip_count=len(clip_paths or []),
            clip_preview_urls=previews,
        )
    )


async def _resolve_segment_image(
    payload: GenerateVideoRequest,
    character: CharacterSuggestion,
    segment_text: str,
    prompt: str,
    scene_metadata: dict,
    image_path: Path,
    resolution: tuple[int, int],
    recent_reuse_entry_ids: set[str] | None = None,
) -> tuple[Path, str, str | None]:
    descriptor = build_scene_descriptor(
        character=character,
        segment_text=segment_text,
        prompt=prompt,
        metadata=scene_metadata,
    )

    if payload.enable_scene_image_reuse:
        matched = await find_reusable_scene_image(
            scene_descriptor=descriptor,
            model_id=payload.model_id,
            disallow_entry_ids=recent_reuse_entry_ids,
        )
        if matched and matched.get("image_path"):
            reused = await run_in_threadpool(
                render_cached_image_to_output,
                matched["image_path"],
                image_path,
                resolution,
            )
            logger.info(
                "Scene cache hit (%s): confidence=%.3f reason=%s",
                matched.get("match_type"),
                float(matched.get("confidence") or 0.0),
                matched.get("reason") or "",
            )
            return reused, "cache", str(matched.get("entry_id") or "") or None

    try:
        generated = await use_reference_or_generate(
            prompt=prompt,
            output_path=image_path,
            resolution=resolution,
            reference_image_path=character.reference_image_path,
            aspect_ratio=payload.image_aspect_ratio,
        )
    except ImageGenerationError as generation_error:
        logger.warning("Image generation failed for current scene, trying fallback selection")

        forced_llm_pick = await force_llm_select_scene_image(
            scene_descriptor=descriptor,
            model_id=payload.model_id,
            disallow_entry_ids=recent_reuse_entry_ids,
        )
        if forced_llm_pick and forced_llm_pick.get("image_path"):
            reused = await run_in_threadpool(
                render_cached_image_to_output,
                forced_llm_pick["image_path"],
                image_path,
                resolution,
            )
            logger.info(
                "Scene fallback forced-LLM pick (%s): reason=%s",
                forced_llm_pick.get("match_type"),
                forced_llm_pick.get("reason") or "",
            )
            return reused, "fallback-llm", str(forced_llm_pick.get("entry_id") or "") or None

        fallback_matched = await find_reusable_scene_image(
            scene_descriptor=descriptor,
            model_id=payload.model_id,
            disallow_entry_ids=recent_reuse_entry_ids,
        )
        if fallback_matched and fallback_matched.get("image_path"):
            reused = await run_in_threadpool(
                render_cached_image_to_output,
                fallback_matched["image_path"],
                image_path,
                resolution,
            )
            logger.info(
                "Scene fallback cache hit (%s): reason=%s",
                fallback_matched.get("match_type"),
                fallback_matched.get("reason") or "",
            )
            return reused, "fallback-cache", str(fallback_matched.get("entry_id") or "") or None

        ref_path = Path(character.reference_image_path or "") if character.reference_image_path else None
        if ref_path and ref_path.exists() and ref_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            reused = await run_in_threadpool(
                render_cached_image_to_output,
                ref_path,
                image_path,
                resolution,
            )
            logger.warning("Scene fallback used character reference image")
            return reused, "fallback-reference", None

        candidates = await run_in_threadpool(list_scene_cache_entries, recent_reuse_entry_ids)
        if candidates:
            selected = random.choice(candidates)
            reused = await run_in_threadpool(
                render_cached_image_to_output,
                selected["image_path"],
                image_path,
                resolution,
            )
            logger.warning("Scene fallback used random cached image: entry_id=%s", selected.get("id"))
            return reused, "fallback-random-cache", str(selected.get("id") or "") or None

        raise ImageGenerationError("image generation failed and no fallback scene/reference image available") from generation_error

    cache_entry_id: str | None = None
    if payload.enable_scene_image_reuse:
        try:
            saved_entry = await run_in_threadpool(save_scene_image_cache_entry, descriptor, generated, prompt)
            cache_entry_id = str((saved_entry or {}).get("id") or "") or None
        except Exception:
            logger.exception("Failed to persist generated image into scene cache")

    return generated, "generated", cache_entry_id


async def run_video_job(job_id: str, payload: GenerateVideoRequest, base_url: str) -> None:
    temp_root = project_path(settings.temp_dir) / job_id
    clip_root = temp_root / "clips"
    clip_root.mkdir(parents=True, exist_ok=True)

    if payload.enable_scene_image_reuse:
        ensure_scene_cache_paths()

    try:
        _update_job(job_id, base_url, "running", 0.05, "segment", "Segmenting text")
        segments, sentence_count = await _segment_text(payload)
        if payload.max_segment_groups > 0:
            segments = segments[: payload.max_segment_groups]
        if not segments:
            raise ValueError("No segment groups produced")

        resolution = _parse_resolution(payload.resolution)
        characters = _sanitize_character_voices(list(payload.characters), narrator_voice=_NARRATOR_VOICE_ID)
        clip_paths: list[str] = []
        total = len(segments)
        no_repeat_window = max(0, int(payload.scene_reuse_no_repeat_window or 0))
        lookback_scenes = no_repeat_window
        recent_scene_entry_ids = deque(maxlen=lookback_scenes if lookback_scenes > 0 else None)

        for index, segment_text in enumerate(segments):
            if job_store.is_cancelled(job_id):
                _update_job(job_id, base_url, "cancelled", 1.0, "cancelled", "Job cancelled", clip_paths=clip_paths)
                return

            progress = 0.1 + (index / total) * 0.75
            _update_job(
                job_id,
                base_url,
                "running",
                progress,
                "render-segment",
                f"Rendering segment {index + 1}/{total} (sentences: {sentence_count or '-'})",
                clip_paths=clip_paths,
            )

            character = _pick_character(characters, segment_text)

            image_path = temp_root / f"segment_{index:04d}.png"
            audio_path = temp_root / f"segment_{index:04d}.mp3"
            clip_path = clip_root / f"clip_{index:04d}.mp4"

            prompt_task = asyncio.create_task(
                build_segment_image_bundle(
                    character=character,
                    segment_text=segment_text,
                    model_id=payload.model_id,
                )
            )
            audio_task = asyncio.create_task(
                _synthesize_segment_tts(
                    text=segment_text,
                    characters=characters,
                    output_path=audio_path,
                    narrator_voice=_NARRATOR_VOICE_ID,
                )
            )

            prompt_bundle = await prompt_task
            prompt = str(prompt_bundle.get("prompt") or "").strip()
            scene_metadata = prompt_bundle.get("metadata") or {}

            image_task = asyncio.create_task(
                _resolve_segment_image(
                    payload=payload,
                    character=character,
                    segment_text=segment_text,
                    prompt=prompt,
                    scene_metadata=scene_metadata,
                    image_path=image_path,
                    resolution=resolution,
                    recent_reuse_entry_ids=set(recent_scene_entry_ids),
                )
            )

            image_bundle, audio_bundle = await asyncio.gather(image_task, audio_task)
            image_result, image_source, reused_entry_id = image_bundle

            if lookback_scenes > 0 and reused_entry_id:
                recent_scene_entry_ids.append(str(reused_entry_id))

            audio_result_path, duration = audio_bundle
            logger.info("Segment %s image source: %s", index + 1, image_source)

            await run_in_threadpool(
                _render_clip_sync,
                str(image_result),
                str(audio_result_path),
                segment_text,
                max(duration, 1.0),
                clip_path,
                payload.fps,
                resolution,
                payload.subtitle_style,
                payload.camera_motion,
                payload.render_mode,
            )
            clip_paths.append(str(clip_path))

        if job_store.is_cancelled(job_id):
            _update_job(job_id, base_url, "cancelled", 1.0, "cancelled", "Job cancelled", clip_paths=clip_paths)
            return

        _update_job(job_id, base_url, "running", 0.9, "compose", "Composing final video", clip_paths=clip_paths)
        output_root = project_path(settings.output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        final_path = output_root / f"{job_id}.mp4"
        await run_in_threadpool(
            _render_final_sync,
            clip_paths,
            final_path,
            payload.fps,
            payload.bgm_enabled,
            payload.bgm_volume,
            payload.render_mode,
        )

        if job_store.is_cancelled(job_id):
            _update_job(
                job_id,
                base_url,
                "cancelled",
                1.0,
                "cancelled",
                "Job cancelled during compose stage",
                clip_paths=clip_paths,
            )
            return

        _update_job(
            job_id,
            base_url,
            "completed",
            1.0,
            "done",
            "Video generation completed",
            output_video_path=str(final_path),
            clip_paths=clip_paths,
        )
    except Exception as exc:
        logger.exception("Video job failed: %s", job_id)
        _update_job(job_id, base_url, "failed", 1.0, "error", f"Video generation failed: {exc}")
    finally:
        job_store.clear_cancel(job_id)


def create_job(payload: GenerateVideoRequest, base_url: str) -> str:
    job_id = uuid4().hex
    _update_job(job_id, base_url, "queued", 0.0, "queued", "Job queued")

    def runner() -> None:
        asyncio.run(run_video_job(job_id=job_id, payload=payload, base_url=base_url))

    thread = Thread(target=runner, daemon=True)
    thread.start()
    return job_id


def cancel_job(job_id: str, base_url: str) -> bool:
    if not job_store.cancel(job_id):
        return False
    current = job_store.get(job_id)
    if current and current.status in {"queued", "running"}:
        job_store.set(
            JobStatus(
                job_id=current.job_id,
                status="cancelled",
                progress=current.progress,
                step="cancelled",
                message="Cancel request accepted, stopping",
                output_video_url=current.output_video_url,
                output_video_path=current.output_video_path,
                clip_count=current.clip_count,
                clip_preview_urls=current.clip_preview_urls,
            )
        )
    return True
