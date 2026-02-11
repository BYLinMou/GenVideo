from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from threading import Thread
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool
from moviepy import AudioFileClip, CompositeAudioClip, CompositeVideoClip, ImageClip, TextClip, afx, concatenate_videoclips

from ..config import project_path, settings
from ..models import CharacterSuggestion, GenerateVideoRequest, JobStatus
from ..state import job_store
from .image_service import use_reference_or_generate
from .llm_service import (
    build_segment_image_prompt,
    group_sentences,
    segment_by_fixed,
    segment_by_smart,
    split_sentences,
)
from .scene_cache_service import (
    build_scene_descriptor,
    ensure_scene_cache_paths,
    find_reusable_scene_image,
    render_cached_image_to_output,
    save_scene_image_cache_entry,
)
from .tts_service import synthesize_tts


logger = logging.getLogger(__name__)

_SUBTITLE_FONT_RESOLVED = False
_SUBTITLE_FONT_PATH: str | None = None


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
    fontsize = 56 if style == "center" else 46
    color = "#FFFFFF"
    stroke_color = "#111111"
    y_pos = int(height * 0.78)

    if style == "center":
        y_pos = int(height * 0.45)
    elif style == "danmaku":
        y_pos = int(height * 0.15)
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
    for sentence, start_at, end_at in _subtitle_timeline(text, duration):
        text_kwargs = {
            "text": sentence,
            "font_size": fontsize,
            "color": color,
            "stroke_color": stroke_color,
            "stroke_width": 2,
            "method": "caption",
            "size": (width - 120, None),
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

        subtitles.append(
            clip.with_start(start_at).with_duration(max(0.05, end_at - start_at)).with_position(("center", y_pos))
        )

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
) -> None:
    image_clip = _build_motion_image_clip(
        image_path=image_path,
        duration=duration,
        resolution=resolution,
        motion=camera_motion,
    )
    audio_clip = AudioFileClip(audio_path)
    base = image_clip.with_audio(audio_clip)
    subtitle_clips = _subtitle_clips(text, duration, resolution, subtitle_style)
    composed = CompositeVideoClip([base, *subtitle_clips], size=resolution).with_duration(duration)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    composed.write_videofile(str(output_path), fps=fps, audio_codec="aac", codec="libx264", logger=None)

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
) -> None:
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
        output_path.parent.mkdir(parents=True, exist_ok=True)
        final_output.write_videofile(str(output_path), fps=fps, audio_codec="aac", codec="libx264", logger=None)
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
    image_path: Path,
    resolution: tuple[int, int],
) -> tuple[Path, str]:
    descriptor = build_scene_descriptor(character=character, segment_text=segment_text, prompt=prompt)

    if payload.enable_scene_image_reuse:
        matched = await find_reusable_scene_image(scene_descriptor=descriptor, model_id=payload.model_id)
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
            return reused, "cache"

    generated = await use_reference_or_generate(
        prompt=prompt,
        output_path=image_path,
        resolution=resolution,
        reference_image_path=character.reference_image_path,
        aspect_ratio=payload.image_aspect_ratio,
    )

    if payload.enable_scene_image_reuse:
        try:
            await run_in_threadpool(save_scene_image_cache_entry, descriptor, generated, prompt)
        except Exception:
            logger.exception("Failed to persist generated image into scene cache")

    return generated, "generated"


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
        clip_paths: list[str] = []
        total = len(segments)

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

            character = _pick_character(payload.characters, segment_text)

            image_path = temp_root / f"segment_{index:04d}.png"
            audio_path = temp_root / f"segment_{index:04d}.mp3"
            clip_path = clip_root / f"clip_{index:04d}.mp4"

            prompt_task = asyncio.create_task(
                build_segment_image_prompt(
                    character=character,
                    segment_text=segment_text,
                    model_id=payload.model_id,
                )
            )
            audio_task = asyncio.create_task(
                synthesize_tts(text=segment_text, voice=character.voice_id, output_path=audio_path)
            )

            prompt = await prompt_task

            image_task = asyncio.create_task(
                _resolve_segment_image(
                    payload=payload,
                    character=character,
                    segment_text=segment_text,
                    prompt=prompt,
                    image_path=image_path,
                    resolution=resolution,
                )
            )

            image_bundle, audio_bundle = await asyncio.gather(image_task, audio_task)
            image_result, image_source = image_bundle
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
