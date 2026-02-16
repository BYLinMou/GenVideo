from __future__ import annotations

import asyncio
import gc
import json
import logging
import re
import shutil
import subprocess
import tempfile
import random
from pathlib import Path
from collections import deque
from threading import Lock, Thread
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
    summarize_story_world_context,
)
from .segmentation_service import build_segment_plan, resolve_precomputed_segments, select_segments_by_range
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

_RUNNER_LOCK = Lock()
_ACTIVE_RUNNERS: set[str] = set()

_SUBTITLE_FONT_RESOLVED = False
_SUBTITLE_FONT_PATH: str | None = None

_VIDEO_AUDIO_BITRATE = "96k"
_TTS_GAIN = 1.15
_FINAL_AUDIO_GAIN = 5.0
_NARRATOR_VOICE_ID = "zh-CN-YunxiNeural"
_OVERLAY_FONT_SIZE = 58
_WATERMARK_TRAVEL_SECONDS = 22.0
_DIALOG_QUOTE_PAIRS = {
    '"': '"',
    "\u201c": "\u201d",  # “ ”
}


def _ffmpeg_escape_text(text: str) -> str:
    value = (text or "").replace("\\", "\\\\")
    value = value.replace(":", "\\:").replace("'", "\\'")
    value = value.replace("\n", " ").replace("\r", " ")
    value = value.replace("%", "\\%")
    return value


def _probe_video_size(path: Path) -> tuple[int, int]:
    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin:
        cmd = [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            try:
                parsed = json.loads(proc.stdout or "{}")
                streams = parsed.get("streams") or []
                if streams:
                    width = int(streams[0].get("width") or 0)
                    height = int(streams[0].get("height") or 0)
                    if width > 0 and height > 0:
                        return width, height
            except Exception:
                logger.exception("Failed to parse ffprobe output for video size")

    try:
        from moviepy import VideoFileClip

        clip = VideoFileClip(str(path))
        try:
            return int(clip.w), int(clip.h)
        finally:
            clip.close()
    except Exception:
        logger.exception("Failed to probe output size, fallback to 1920x1080")
    return 1920, 1080


def _compose_overlay_filter(
    width: int,
    height: int,
    subtitle_font: str | None,
    novel_alias: str | None,
    watermark_enabled: bool,
    watermark_type: str,
    watermark_text: str | None,
    watermark_opacity: float,
) -> tuple[str, bool]:
    filters: list[str] = []
    has_image_input = False
    current_video = "0:v"

    alias_value = (novel_alias or "").strip()
    if alias_value:
        alias_font_size = max(54, int(_OVERLAY_FONT_SIZE) - 3)
        alias_line_h = int(alias_font_size * 1.5)
        alias_font = _ffmpeg_escape_text(str(subtitle_font or "C:/Windows/Fonts/msyh.ttc"))
        alias_text = _ffmpeg_escape_text(alias_value)
        filters.append(
            f"[{current_video}]drawbox=x=0:y=0:w={width}:h={alias_line_h}:color=black@0.35:t=fill[vbox0]"
        )
        filters.append(
            "[vbox0]drawtext="
            f"fontfile='{alias_font}':"
            f"text='{alias_text}':"
            f"fontcolor=white:fontsize={alias_font_size}:"
            f"x=(w-text_w)/2:y=max(12\\,({alias_line_h}-text_h)/2)[vtitle0]"
        )
        current_video = "vtitle0"

    if watermark_enabled:
        opacity = max(0.05, min(float(watermark_opacity), 1.0))
        wm_w = max(140, int(width * 0.22))
        wm_h = max(60, int(height * 0.12))
        travel_time = _WATERMARK_TRAVEL_SECONDS
        if watermark_type == "image":
            has_image_input = True
            filters.append(
                "[1:v]"
                f"scale=w={wm_w}:h=-1,"
                f"format=rgba,colorchannelmixer=aa={opacity}[wmimg0]"
            )
            filters.append(
                f"[{current_video}][wmimg0]overlay="
                f"x='if(lt(mod(t\\,{travel_time})\\,{travel_time/2})\\,20+(W-w-40)*mod(t\\,{travel_time/2})/{travel_time/2}\\,W-w-20-(W-w-40)*mod(t-{travel_time/2}\\,{travel_time/2})/{travel_time/2})':"
                f"y='if(lt(mod(t\\,{travel_time})\\,{travel_time/2})\\,20+(H-h-40)*mod(t\\,{travel_time/2})/{travel_time/2}\\,H-h-20-(H-h-40)*mod(t-{travel_time/2}\\,{travel_time/2})/{travel_time/2})':"
                "shortest=1[vwm0]"
            )
        else:
            wm_text = _ffmpeg_escape_text((watermark_text or "").strip() or "WATERMARK")
            wm_font = _ffmpeg_escape_text(str(subtitle_font or "C:/Windows/Fonts/msyh.ttc"))
            wm_size = max(48, int(_OVERLAY_FONT_SIZE * 1.1))
            filters.append(
                f"[{current_video}]drawtext="
                f"fontfile='{wm_font}':"
                f"text='{wm_text}':"
                f"fontcolor=white@{opacity}:fontsize={wm_size}:"
                f"x='if(lt(mod(t\\,{travel_time})\\,{travel_time/2})\\,20+(w-text_w-40)*mod(t\\,{travel_time/2})/{travel_time/2}\\,w-text_w-20-(w-text_w-40)*mod(t-{travel_time/2}\\,{travel_time/2})/{travel_time/2})':"
                f"y='if(lt(mod(t\\,{travel_time})\\,{travel_time/2})\\,20+(h-text_h-40)*mod(t\\,{travel_time/2})/{travel_time/2}\\,h-text_h-20-(h-text_h-40)*mod(t-{travel_time/2}\\,{travel_time/2})/{travel_time/2})'[vwm0]"
            )
        current_video = "vwm0"

    if current_video != "0:v":
        filters.append(f"[{current_video}]format=yuv420p[vout]")

    return ";".join(filters), has_image_input


def _apply_final_overlays_ffmpeg(
    ffmpeg_bin: str,
    input_video: Path,
    output_video: Path,
    novel_alias: str | None,
    watermark_enabled: bool,
    watermark_type: str,
    watermark_text: str | None,
    watermark_image_path: str | None,
    watermark_opacity: float,
    preset: str,
    crf: str,
) -> Path:
    overlay_needed = bool((novel_alias or "").strip()) or bool(watermark_enabled)
    if not overlay_needed:
        return input_video

    width, height = _probe_video_size(input_video)
    subtitle_font = _subtitle_font_path()
    filter_complex, has_image_input = _compose_overlay_filter(
        width=width,
        height=height,
        subtitle_font=subtitle_font,
        novel_alias=novel_alias,
        watermark_enabled=bool(watermark_enabled),
        watermark_type=(watermark_type or "text").strip().lower(),
        watermark_text=watermark_text,
        watermark_opacity=watermark_opacity,
    )
    if not filter_complex:
        return input_video

    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_video),
    ]

    if has_image_input:
        wm_path = Path(watermark_image_path or "")
        if wm_path.exists() and wm_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            cmd.extend(["-stream_loop", "-1", "-i", str(wm_path)])
        else:
            fallback_filter, _ = _compose_overlay_filter(
                width=width,
                height=height,
                subtitle_font=subtitle_font,
                novel_alias=novel_alias,
                watermark_enabled=bool(watermark_enabled),
                watermark_type="text",
                watermark_text=watermark_text,
                watermark_opacity=watermark_opacity,
            )
            filter_complex = fallback_filter

    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "0:a:0",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            crf,
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and output_video.exists():
        return output_video
    logger.warning("ffmpeg final overlay failed, skip overlay: %s", (proc.stderr or "")[:400])
    return input_video


def _build_moviepy_overlay_clips(
    final_output,
    novel_alias: str | None,
    watermark_enabled: bool,
    watermark_type: str,
    watermark_text: str | None,
    watermark_image_path: str | None,
    watermark_opacity: float,
) -> list:
    clips: list = []
    duration = max(0.1, float(final_output.duration or 0.1))
    width = int(final_output.w)
    height = int(final_output.h)
    overlay_font = _subtitle_font_path()

    alias_value = (novel_alias or "").strip()
    if alias_value:
        title_clip = TextClip(
            text=alias_value,
            font=overlay_font if overlay_font else None,
            font_size=max(54, int(_OVERLAY_FONT_SIZE) - 3),
            color="#FFFFFF",
            stroke_color="#111111",
            stroke_width=2,
            method="caption",
            size=(max(320, width - 120), 120),
            text_align="center",
            horizontal_align="center",
            vertical_align="center",
        ).with_position(("center", 10)).with_duration(duration)
        clips.append(title_clip)

    if not watermark_enabled:
        return clips

    opacity = max(0.05, min(float(watermark_opacity), 1.0))
    travel = _WATERMARK_TRAVEL_SECONDS

    if (watermark_type or "text").strip().lower() == "image":
        wm_path = Path(watermark_image_path or "")
        if wm_path.exists() and wm_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            wm = ImageClip(str(wm_path)).with_duration(duration)
            wm = wm.resized(width=max(140, int(width * 0.22))).with_opacity(opacity)

            def wm_pos(t: float) -> tuple[float, float]:
                safe_t = float(t or 0.0)
                phase = (safe_t % travel) / travel
                if phase < 0.25:
                    ratio = phase / 0.25
                    x = 20 + (width - wm.w - 40) * ratio
                    y = 20
                elif phase < 0.5:
                    ratio = (phase - 0.25) / 0.25
                    x = width - wm.w - 20
                    y = 20 + (height - wm.h - 40) * ratio
                elif phase < 0.75:
                    ratio = (phase - 0.5) / 0.25
                    x = width - wm.w - 20 - (width - wm.w - 40) * ratio
                    y = height - wm.h - 20
                else:
                    ratio = (phase - 0.75) / 0.25
                    x = 20
                    y = height - wm.h - 20 - (height - wm.h - 40) * ratio
                return max(20.0, x), max(20.0, y)

            clips.append(wm.with_position(wm_pos))
            return clips

    wm_text = (watermark_text or "").strip() or "WATERMARK"
    wm_text_clip = TextClip(
        text=wm_text,
        font=overlay_font if overlay_font else None,
        font_size=max(48, int(_OVERLAY_FONT_SIZE * 1.1)),
        color="#FFFFFF",
        stroke_color="#111111",
        stroke_width=2,
        method="label",
    ).with_duration(duration).with_opacity(opacity)

    def wm_text_pos(t: float) -> tuple[float, float]:
        safe_t = float(t or 0.0)
        phase = (safe_t % travel) / travel
        if phase < 0.25:
            ratio = phase / 0.25
            x = 20 + (width - wm_text_clip.w - 40) * ratio
            y = 20
        elif phase < 0.5:
            ratio = (phase - 0.25) / 0.25
            x = width - wm_text_clip.w - 20
            y = 20 + (height - wm_text_clip.h - 40) * ratio
        elif phase < 0.75:
            ratio = (phase - 0.5) / 0.25
            x = width - wm_text_clip.w - 20 - (width - wm_text_clip.w - 40) * ratio
            y = height - wm_text_clip.h - 20
        else:
            ratio = (phase - 0.75) / 0.25
            x = 20
            y = height - wm_text_clip.h - 20 - (height - wm_text_clip.h - 40) * ratio
        return max(20.0, x), max(20.0, y)

    clips.append(wm_text_clip.with_position(wm_text_pos))
    return clips


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


_SPEECH_VERBS = (
    "说",
    "说道",
    "道",
    "问",
    "回答",
    "答",
    "喊",
    "叫",
    "嘀咕",
    "喃喃",
    "开口",
    "提醒",
    "反问",
    "怒喝",
    "笑道",
)


def _mentions_in_text(characters: list[CharacterSuggestion], text: str) -> list[tuple[int, int, CharacterSuggestion]]:
    content = text or ""
    hits: list[tuple[int, int, CharacterSuggestion]] = []
    for item in characters:
        name = (item.name or "").strip()
        if not name:
            continue
        pos = content.find(name)
        if pos >= 0:
            hits.append((pos, -int(item.importance or 0), item))
    hits.sort(key=lambda row: (row[0], row[1]))
    return hits


def _detect_speaker_character(characters: list[CharacterSuggestion], text: str) -> CharacterSuggestion | None:
    content = text or ""
    if not content:
        return None

    best: tuple[int, int, CharacterSuggestion] | None = None
    for item in characters:
        name = (item.name or "").strip()
        if not name:
            continue
        escaped = re.escape(name)
        patterns = [
            rf"{escaped}\s*(?:[，,：: ]\s*)?(?:{'|'.join(_SPEECH_VERBS)})",
            rf"(?:{'|'.join(_SPEECH_VERBS)})\s*(?:[，,：: ]\s*)?{escaped}",
            rf"{escaped}\s*[：:]",
        ]
        for pattern in patterns:
            matched = re.search(pattern, content)
            if not matched:
                continue
            candidate = (matched.start(), -int(item.importance or 0), item)
            if best is None or candidate < best:
                best = candidate
            break

    return best[2] if best else None


def _contains_first_person_pronoun(text: str) -> bool:
    content = str(text or "")
    if not content:
        return False
    narration_only = _strip_quoted_dialogue(content)
    if not narration_only:
        return False

    if any(token in narration_only for token in ("我", "我们", "咱", "咱们")):
        return True

    lowered = narration_only.lower()
    return bool(re.search(r"\b(i|i'm|i’ve|i'd|me|my|mine|we|our|ours)\b", lowered))


def _strip_quoted_dialogue(text: str) -> str:
    content = str(text or "")
    if not content:
        return ""

    quotes = _extract_quote_blocks(content)
    if not quotes:
        return content

    slices: list[str] = []
    cursor = 0
    for _, start, end in quotes:
        if start > cursor:
            slices.append(content[cursor:start])
        cursor = max(cursor, end + 1)

    if cursor < len(content):
        slices.append(content[cursor:])

    return " ".join(part.strip() for part in slices if part and part.strip())


def _pick_story_self_character(characters: list[CharacterSuggestion]) -> CharacterSuggestion | None:
    marked = [item for item in characters if bool(getattr(item, "is_story_self", False))]
    if marked:
        marked.sort(key=lambda item: int(item.importance or 0), reverse=True)
        return marked[0]

    mains = [item for item in characters if bool(getattr(item, "is_main_character", False))]
    if mains:
        mains.sort(key=lambda item: int(item.importance or 0), reverse=True)
        return mains[0]
    return None


def _normalize_runtime_identity_flags(characters: list[CharacterSuggestion]) -> list[CharacterSuggestion]:
    if not characters:
        return characters

    main_indexes = [index for index, item in enumerate(characters) if bool(item.is_main_character)]
    self_indexes = [index for index, item in enumerate(characters) if bool(item.is_story_self)]

    if len(main_indexes) > 1:
        keep = main_indexes[0]
        for index in main_indexes[1:]:
            characters[index].is_main_character = False
        main_indexes = [keep]

    if len(self_indexes) > 1:
        keep = self_indexes[0]
        for index in self_indexes[1:]:
            characters[index].is_story_self = False
        self_indexes = [keep]

    if not main_indexes:
        best_index = max(range(len(characters)), key=lambda idx: int(characters[idx].importance or 0))
        characters[best_index].is_main_character = True

    return characters


def _pick_character(
    characters: list[CharacterSuggestion],
    text: str,
    previous_character: CharacterSuggestion | None = None,
    previous_segment_text: str = "",
    next_segment_text: str = "",
) -> CharacterSuggestion:
    if not characters:
        return CharacterSuggestion(name="narrator", role="narrator", voice_id="zh-CN-YunxiNeural")

    current_text = text or ""
    hits = _mentions_in_text(characters, current_text)
    if hits:
        return hits[0][2]

    speaker = _detect_speaker_character(characters, current_text)
    if speaker is not None:
        return speaker

    if _contains_first_person_pronoun(current_text):
        self_character = _pick_story_self_character(characters)
        if self_character is not None:
            return self_character

    has_dialogue = any(mark in current_text for mark in _DIALOG_QUOTE_PAIRS.keys())
    if has_dialogue and previous_segment_text:
        previous_speaker = _detect_speaker_character(characters, previous_segment_text)
        if previous_speaker is not None:
            return previous_speaker

    weighted_scores: dict[int, tuple[float, CharacterSuggestion]] = {}
    for item in characters:
        marker = id(item)
        name = (item.name or "").strip()
        if not name:
            continue
        score = 0.0
        score += float(current_text.count(name)) * 6.0
        score += float((previous_segment_text or "").count(name)) * 2.5
        score += float((next_segment_text or "").count(name)) * 1.5
        score += float(int(item.importance or 0)) * 0.08
        if previous_character is item and has_dialogue:
            score += 1.25
        weighted_scores[marker] = (score, item)

    ranked = sorted(weighted_scores.values(), key=lambda row: row[0], reverse=True)
    if ranked and ranked[0][0] > 0:
        return ranked[0][1]

    if previous_character is not None:
        return previous_character
    return characters[0]


def _pick_related_characters(
    characters: list[CharacterSuggestion],
    text: str,
    primary: CharacterSuggestion,
    previous_segment_text: str = "",
    next_segment_text: str = "",
) -> list[CharacterSuggestion]:
    if not characters:
        return []

    normalized_current = (text or "").strip()
    normalized_previous = (previous_segment_text or "").strip()
    normalized_next = (next_segment_text or "").strip()
    selected: list[CharacterSuggestion] = []

    current_hits = _mentions_in_text(characters, normalized_current)
    for _, _, item in current_hits:
        selected.append(item)

    if _contains_first_person_pronoun(normalized_current):
        self_character = _pick_story_self_character(characters)
        if self_character is not None:
            selected.append(self_character)

    for item in characters:
        name = (item.name or "").strip()
        if not name:
            continue
        if name in normalized_previous or name in normalized_next:
            selected.append(item)

    if primary not in selected:
        selected.insert(0, primary)

    dedup: list[CharacterSuggestion] = []
    seen: set[int] = set()
    for item in selected:
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        dedup.append(item)
    return dedup


def _pick_characters_by_indexes(
    characters: list[CharacterSuggestion],
    primary_index: int | None,
    related_indexes: list[int],
) -> tuple[CharacterSuggestion | None, list[CharacterSuggestion]]:
    if not characters:
        return None, []

    safe_primary = primary_index if isinstance(primary_index, int) and 0 <= primary_index < len(characters) else None
    selected: list[CharacterSuggestion] = []
    seen: set[int] = set()

    if safe_primary is not None:
        selected.append(characters[safe_primary])
        seen.add(safe_primary)

    for raw in related_indexes:
        if not isinstance(raw, int):
            continue
        if raw < 0 or raw >= len(characters):
            continue
        if raw in seen:
            continue
        selected.append(characters[raw])
        seen.add(raw)

    primary_character = characters[safe_primary] if safe_primary is not None else None
    return primary_character, selected


def _coerce_character_index(value: object, size: int) -> int | None:
    if size <= 0:
        return None
    try:
        index = int(value)
    except Exception:
        return None
    if 0 <= index < size:
        return index
    return None


def _coerce_character_indexes(values: object, size: int, limit: int = 4) -> list[int]:
    if isinstance(values, list):
        raw_items = values
    elif values is None:
        raw_items = []
    else:
        raw_items = [values]

    output: list[int] = []
    seen: set[int] = set()
    for raw in raw_items:
        index = _coerce_character_index(raw, size)
        if index is None or index in seen:
            continue
        seen.add(index)
        output.append(index)
        if len(output) >= max(1, int(limit)):
            break
    return output


def _collect_reference_paths(characters: list[CharacterSuggestion], limit: int = 2) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in characters:
        raw = (item.reference_image_path or "").strip()
        if not raw:
            continue
        key = raw.replace("\\", "/").lower()
        if key in seen:
            continue
        path = Path(raw)
        if not path.exists() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        seen.add(key)
        output.append(raw)
        if len(output) >= max(1, int(limit)):
            break
    return output


def _normalize_character_name_key(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _normalize_path_key(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def _extract_entry_character_profile(entry: dict) -> tuple[str, set[str]]:
    match_profile = entry.get("match_profile") if isinstance(entry.get("match_profile"), dict) else {}
    descriptor = entry.get("descriptor") if isinstance(entry.get("descriptor"), dict) else {}

    character_name = _normalize_character_name_key(
        str(match_profile.get("character_name") or descriptor.get("character_name") or "")
    )

    reference_paths: set[str] = set()
    for source in (match_profile, descriptor):
        paths = source.get("reference_image_paths") if isinstance(source, dict) else []
        if isinstance(paths, list):
            for raw in paths:
                key = _normalize_path_key(str(raw or ""))
                if key:
                    reference_paths.add(key)
        single = _normalize_path_key(str((source.get("reference_image_path") if isinstance(source, dict) else "") or ""))
        if single:
            reference_paths.add(single)

    return character_name, reference_paths


def _pick_random_current_character_cache_entry(
    character: CharacterSuggestion,
    disallow_entry_ids: set[str] | None = None,
) -> dict | None:
    candidates = list_scene_cache_entries(disallow_entry_ids)
    if not candidates:
        return None

    target_name = _normalize_character_name_key(character.name or "")
    target_ref_path = _normalize_path_key(character.reference_image_path or "")

    matched: list[dict] = []
    for item in candidates:
        image_path = Path(str(item.get("image_path") or ""))
        if not image_path.exists():
            continue
        entry_name, entry_ref_paths = _extract_entry_character_profile(item)
        name_match = bool(target_name) and entry_name == target_name
        ref_match = bool(target_ref_path) and target_ref_path in entry_ref_paths
        if name_match or ref_match:
            matched.append(item)

    if not matched:
        return None
    return random.choice(matched)


def _entry_is_scene_only(entry: dict) -> bool:
    match_profile = entry.get("match_profile") if isinstance(entry.get("match_profile"), dict) else {}
    descriptor = entry.get("descriptor") if isinstance(entry.get("descriptor"), dict) else {}

    raw = match_profile.get("is_scene_only", descriptor.get("is_scene_only", False))
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _pick_random_scene_only_cache_entry(
    disallow_entry_ids: set[str] | None = None,
) -> dict | None:
    candidates = list_scene_cache_entries(disallow_entry_ids)
    if not candidates:
        return None

    matched: list[dict] = []
    for item in candidates:
        image_path = Path(str(item.get("image_path") or ""))
        if not image_path.exists():
            continue
        if _entry_is_scene_only(item):
            matched.append(item)

    if not matched:
        return None
    return random.choice(matched)


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
    if configured:
        raw = Path(configured)
        configured_path = raw if raw.is_absolute() else project_path(configured)
        if configured_path.exists():
            return str(configured_path)
        logger.warning("Configured SUBTITLE_FONT_PATH not found: %s", configured_path)

    bundled = project_path("backend/app/fonts/NotoSansCJKsc-Regular.otf")
    if bundled.exists():
        return str(bundled)

    candidates: list[Path] = []

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

    image_clip: ImageClip | None = None
    audio_clip: AudioFileClip | None = None
    base: ImageClip | CompositeVideoClip | None = None
    subtitle_clips: list[TextClip] = []
    composed: CompositeVideoClip | None = None

    try:
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
    finally:
        if composed is not None:
            composed.close()
        for subtitle in subtitle_clips:
            subtitle.close()
        if base is not None:
            base.close()
        if audio_clip is not None:
            audio_clip.close()
        if image_clip is not None:
            image_clip.close()


def _render_final_sync(
    clip_paths: list[str],
    output_path: Path,
    fps: int,
    bgm_enabled: bool,
    bgm_volume: float,
    render_mode: str,
    novel_alias: str | None = None,
    watermark_enabled: bool = False,
    watermark_type: str = "text",
    watermark_text: str | None = None,
    watermark_image_path: str | None = None,
    watermark_opacity: float = 0.6,
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

                merged_input = _apply_final_overlays_ffmpeg(
                    ffmpeg_bin=ffmpeg_bin,
                    input_video=merged_no_bgm,
                    output_video=Path(tmp_dir) / "merged_with_overlay.mp4",
                    novel_alias=novel_alias,
                    watermark_enabled=watermark_enabled,
                    watermark_type=watermark_type,
                    watermark_text=watermark_text,
                    watermark_image_path=watermark_image_path,
                    watermark_opacity=watermark_opacity,
                    preset=final_preset,
                    crf=final_crf,
                )

                if bgm_enabled and bgm_volume > 0 and bgm_path.exists():
                    if bool(profile.get("bgm_video_copy", True)):
                        mix_cmd = [
                            ffmpeg_bin,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-i",
                            str(merged_input),
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
                            str(merged_input),
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
                        str(merged_input),
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
                    shutil.copyfile(merged_input, output_path)
                    logger.info("Final compose via ffmpeg concat copy")
                    return
            else:
                logger.warning("ffmpeg concat copy failed, fallback to python compose: %s", (concat_proc.stderr or "")[:400])

    video_clips = []
    bgm_clips = []
    overlay_clips = []
    final = None
    final_with_audio = None
    with_overlay = None
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
        overlay_clips = _build_moviepy_overlay_clips(
            final_output=final_output,
            novel_alias=novel_alias,
            watermark_enabled=watermark_enabled,
            watermark_type=watermark_type,
            watermark_text=watermark_text,
            watermark_image_path=watermark_image_path,
            watermark_opacity=watermark_opacity,
        )
        with_overlay = (
            CompositeVideoClip([final_output, *overlay_clips], size=(int(final_output.w), int(final_output.h))).with_duration(
                max(0.1, float(final_output.duration or 0.1))
            )
            if overlay_clips
            else final_output
        )

        boosted_output = with_overlay
        if with_overlay.audio is not None:
            boosted_output = with_overlay.with_audio(with_overlay.audio.with_volume_scaled(_FINAL_AUDIO_GAIN))

        boosted_output.write_videofile(
            str(output_path),
            fps=fps,
            audio_codec="aac",
            codec="libx264",
            preset=final_preset,
            ffmpeg_params=["-crf", final_crf, "-movflags", "+faststart", "-b:a", _VIDEO_AUDIO_BITRATE],
            logger=None,
        )

        if boosted_output is not with_overlay:
            boosted_output.close()
        if with_overlay is not None and with_overlay is not final_output:
            with_overlay.close()
        for clip in overlay_clips:
            clip.close()
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
    precomputed = resolve_precomputed_segments(
        text=payload.text,
        method=payload.segment_method,
        sentences_per_segment=payload.sentences_per_segment,
        fixed_size=120,
        model_id=payload.model_id,
        request_signature=payload.segment_request_signature,
        precomputed_segments=payload.precomputed_segments,
    )
    if precomputed:
        total_sentences = 0
        if payload.segment_method == "sentence":
            plan = await build_segment_plan(
                text=payload.text,
                method="sentence",
                sentences_per_segment=payload.sentences_per_segment,
                fixed_size=120,
                model_id=payload.model_id,
            )
            total_sentences = plan.total_sentences
        return precomputed, total_sentences

    plan = await build_segment_plan(
        text=payload.text,
        method=payload.segment_method,
        sentences_per_segment=payload.sentences_per_segment,
        fixed_size=120,
        model_id=payload.model_id,
    )
    return plan.segments, plan.total_sentences


def _cleanup_segment_artifacts(temp_root: Path, segment_index: int) -> None:
    stem = f"segment_{segment_index:04d}"
    for suffix in (".png", ".mp3", ".wav"):
        target = temp_root / f"{stem}{suffix}"
        try:
            if target.exists():
                target.unlink()
        except Exception:
            logger.debug("Failed to cleanup artifact: %s", target)


def _collect_clip_paths_for_compose(clip_root: Path, total_segments: int) -> list[str]:
    clip_paths: list[str] = []
    total = max(0, int(total_segments or 0))
    for index in range(total):
        clip_path = clip_root / f"clip_{index:04d}.mp4"
        if not clip_path.exists() or not clip_path.is_file():
            raise FileNotFoundError(f"Missing segment clip for compose: {clip_path}")
        clip_paths.append(str(clip_path))
    return clip_paths
    try:
        return int(path.stat().st_size) >= int(min_bytes)
    except Exception:
        return False


def _build_image_source_report(source_counts: dict[str, int]) -> dict[str, object] | None:
    normalized = {str(key): max(0, int(value or 0)) for key, value in (source_counts or {}).items()}
    total_images = sum(normalized.values())
    if total_images <= 0:
        return None

    cache_images = (
        normalized.get("cache", 0)
        + normalized.get("fallback_llm", 0)
        + normalized.get("fallback_cache", 0)
        + normalized.get("fallback_character_cache", 0)
        + normalized.get("fallback_scene_only_cache", 0)
        + normalized.get("fallback_random_cache", 0)
    )
    generated_images = normalized.get("generated", 0)
    reference_images = normalized.get("fallback_reference", 0)
    other_images = max(0, total_images - cache_images - generated_images - reference_images)

    def _ratio(value: int) -> float:
        return round(float(value) / float(total_images), 4) if total_images > 0 else 0.0

    return {
        "total_images": total_images,
        "cache_images": cache_images,
        "generated_images": generated_images,
        "reference_images": reference_images,
        "other_images": other_images,
        "cache_ratio": _ratio(cache_images),
        "generate_ratio": _ratio(generated_images),
        "reference_ratio": _ratio(reference_images),
        "other_ratio": _ratio(other_images),
        "source_counts": normalized,
    }


def _extract_source_counts_from_report(report: dict[str, object] | None) -> dict[str, int]:
    if not isinstance(report, dict):
        return {}
    raw_counts = report.get("source_counts")
    if not isinstance(raw_counts, dict):
        return {}
    output: dict[str, int] = {}
    for key, value in raw_counts.items():
        try:
            output[str(key)] = max(0, int(value or 0))
        except Exception:
            continue
    return output


def _restore_image_source_counts(job_id: str, source_counts: dict[str, int]) -> dict[str, int]:
    current = job_store.get(job_id)
    if not current:
        return source_counts
    persisted = _extract_source_counts_from_report(current.image_source_report)
    if not persisted:
        return source_counts
    merged = dict(source_counts)
    for key, value in persisted.items():
        merged[str(key)] = max(0, int(value or 0))
    return merged


def _update_job(
    job_id: str,
    base_url: str,
    status: str,
    progress: float,
    step: str,
    message: str,
    current_segment: int = 0,
    total_segments: int = 0,
    output_video_path: str | None = None,
    clip_count: int | None = None,
    image_source_report: dict[str, object] | None = None,
) -> None:
    current = job_store.get(job_id)
    resolved_clip_count = max(0, int(clip_count if clip_count is not None else (current.clip_count if current else 0)))
    resolved_image_source_report = image_source_report
    if resolved_image_source_report is None and current is not None:
        resolved_image_source_report = current.image_source_report
    job_store.set(
        JobStatus(
            job_id=job_id,
            status=status,
            progress=progress,
            step=step,
            message=message,
            current_segment=max(0, int(current_segment or 0)),
            total_segments=max(0, int(total_segments or 0)),
            output_video_url=f"/api/jobs/{job_id}/video" if output_video_path else None,
            output_video_path=output_video_path,
            clip_count=resolved_clip_count,
            clip_preview_urls=[],
            image_source_report=resolved_image_source_report,
        )
    )


async def _resolve_segment_image(
    payload: GenerateVideoRequest,
    character: CharacterSuggestion,
    related_reference_image_paths: list[str],
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
        related_reference_image_paths=related_reference_image_paths,
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
            extra_reference_image_paths=related_reference_image_paths,
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

        character_random_entry = await run_in_threadpool(
            _pick_random_current_character_cache_entry,
            character,
            recent_reuse_entry_ids,
        )
        if character_random_entry and character_random_entry.get("image_path"):
            reused = await run_in_threadpool(
                render_cached_image_to_output,
                character_random_entry["image_path"],
                image_path,
                resolution,
            )
            logger.warning(
                "Scene fallback used random cache image for current character: entry_id=%s character=%s",
                character_random_entry.get("id"),
                character.name,
            )
            return reused, "fallback-character-cache", str(character_random_entry.get("id") or "") or None

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

        scene_only_entry = await run_in_threadpool(
            _pick_random_scene_only_cache_entry,
            recent_reuse_entry_ids,
        )
        if scene_only_entry and scene_only_entry.get("image_path"):
            reused = await run_in_threadpool(
                render_cached_image_to_output,
                scene_only_entry["image_path"],
                image_path,
                resolution,
            )
            logger.warning("Scene fallback used random scene-only cached image: entry_id=%s", scene_only_entry.get("id"))
            return reused, "fallback-scene-only-cache", str(scene_only_entry.get("id") or "") or None

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
    rendered_clip_count = 0
    total = 0
    image_source_counts: dict[str, int] = {
        "cache": 0,
        "generated": 0,
        "fallback_llm": 0,
        "fallback_cache": 0,
        "fallback_character_cache": 0,
        "fallback_scene_only_cache": 0,
        "fallback_reference": 0,
        "fallback_random_cache": 0,
        "other": 0,
    }
    image_source_counts = _restore_image_source_counts(job_id, image_source_counts)
    restored_image_count = sum(max(0, int(value or 0)) for value in image_source_counts.values())
    if restored_image_count > 0:
        logger.info("Restored persisted image source counts for job %s: total=%s", job_id, restored_image_count)

    if payload.enable_scene_image_reuse:
        ensure_scene_cache_paths()

    try:
        _update_job(job_id, base_url, "running", 0.05, "segment", "Segmenting text", current_segment=0, total_segments=0)
        segments, sentence_count = await _segment_text(payload)
        if str(payload.segment_groups_range or "").strip():
            segments = select_segments_by_range(segments, payload.segment_groups_range)
            if not segments:
                raise ValueError("segment_groups_range selected no segments (range is 1-based)")
        elif payload.max_segment_groups > 0:
            segments = segments[: payload.max_segment_groups]
        if not segments:
            raise ValueError("No segment groups produced")

        resolution = _parse_resolution(payload.resolution)
        characters = _sanitize_character_voices(list(payload.characters), narrator_voice=_NARRATOR_VOICE_ID)
        characters = _normalize_runtime_identity_flags(characters)
        story_world_context = await summarize_story_world_context(payload.text, payload.model_id)
        if story_world_context:
            logger.info("Story world context summary: %s", story_world_context)
        total = len(segments)
        no_repeat_window = max(0, int(payload.scene_reuse_no_repeat_window or 0))
        lookback_scenes = no_repeat_window
        recent_scene_entry_ids = deque(maxlen=lookback_scenes if lookback_scenes > 0 else None)
        previous_primary_character: CharacterSuggestion | None = None

        for index, segment_text in enumerate(segments):
            if job_store.is_cancelled(job_id):
                _update_job(
                    job_id,
                    base_url,
                    "cancelled",
                    1.0,
                    "cancelled",
                    "Job cancelled",
                    current_segment=index,
                    total_segments=total,
                    clip_count=rendered_clip_count,
                )
                return

            stage_start = 0.1
            stage_span = 0.75
            segment_progress = index / max(total, 1)
            progress = stage_start + segment_progress * stage_span
            _update_job(
                job_id,
                base_url,
                "running",
                progress,
                "render-segment",
                f"Rendering scene {index + 1}/{total} (sentences: {sentence_count or '-'})",
                current_segment=index + 1,
                total_segments=total,
                clip_count=rendered_clip_count,
            )

            previous_segment_text = segments[index - 1] if index > 0 else ""
            next_segment_text = segments[index + 1] if index + 1 < total else ""

            default_character = _pick_character(
                characters,
                segment_text,
                previous_character=previous_primary_character,
                previous_segment_text=previous_segment_text,
                next_segment_text=next_segment_text,
            )
            default_related_characters = _pick_related_characters(
                characters,
                segment_text,
                default_character,
                previous_segment_text=previous_segment_text,
                next_segment_text=next_segment_text,
            )
            if default_character not in default_related_characters:
                default_related_characters.insert(0, default_character)

            default_primary_index = next(
                (idx for idx, item in enumerate(characters) if item is default_character),
                0,
            )
            default_related_indexes = [
                idx
                for idx, item in enumerate(characters)
                if any(item is selected for selected in default_related_characters)
            ]

            character = default_character
            related_characters = list(default_related_characters)
            related_reference_paths = _collect_reference_paths(related_characters, limit=2)

            image_path = temp_root / f"segment_{index:04d}.png"
            audio_path = temp_root / f"segment_{index:04d}.mp3"
            clip_path = clip_root / f"clip_{index:04d}.mp4"

            if clip_path.exists() and clip_path.is_file():
                rendered_clip_count += 1
                completed_ratio = (index + 1) / max(total, 1)
                _update_job(
                    job_id,
                    base_url,
                    "running",
                    stage_start + completed_ratio * stage_span,
                    "render-segment",
                    f"Scene {index + 1}/{total} resumed from checkpoint",
                    current_segment=index + 1,
                    total_segments=total,
                    clip_count=rendered_clip_count,
                )
                _cleanup_segment_artifacts(temp_root, index)
                gc.collect()
                continue

            prompt_task = asyncio.create_task(
                build_segment_image_bundle(
                    character=character,
                    segment_text=segment_text,
                    model_id=payload.model_id,
                    related_reference_image_paths=related_reference_paths,
                    story_world_context=story_world_context,
                    previous_segment_text=previous_segment_text,
                    next_segment_text=next_segment_text,
                    character_candidates=characters,
                    default_primary_index=default_primary_index,
                    default_related_indexes=default_related_indexes,
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

            assignment = prompt_bundle.get("character_assignment") if isinstance(prompt_bundle.get("character_assignment"), dict) else {}
            resolved_primary_index = _coerce_character_index(assignment.get("primary_index"), len(characters))
            resolved_related_indexes = _coerce_character_indexes(assignment.get("related_indexes"), len(characters), limit=3)

            if resolved_primary_index is None:
                resolved_primary_index = default_primary_index
            if resolved_primary_index is not None and resolved_primary_index not in resolved_related_indexes:
                resolved_related_indexes.insert(0, resolved_primary_index)
            if not resolved_related_indexes:
                resolved_related_indexes = list(default_related_indexes)

            selected_character, selected_related = _pick_characters_by_indexes(
                characters,
                resolved_primary_index,
                resolved_related_indexes,
            )
            if selected_character is not None:
                character = selected_character
                related_characters = selected_related or [selected_character]
                if character not in related_characters:
                    related_characters.insert(0, character)
                related_reference_paths = _collect_reference_paths(related_characters, limit=2)
                logger.info(
                    "Segment %s character assignment from prompt call: primary=%s confidence=%.2f reason=%s",
                    index + 1,
                    character.name,
                    float(assignment.get("confidence") or 0.0),
                    str(assignment.get("reason") or ""),
                )

            previous_primary_character = character

            image_task = asyncio.create_task(
                _resolve_segment_image(
                    payload=payload,
                    character=character,
                    related_reference_image_paths=related_reference_paths,
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
            source_key_map = {
                "cache": "cache",
                "generated": "generated",
                "fallback-llm": "fallback_llm",
                "fallback-cache": "fallback_cache",
                "fallback-character-cache": "fallback_character_cache",
                "fallback-scene-only-cache": "fallback_scene_only_cache",
                "fallback-reference": "fallback_reference",
                "fallback-random-cache": "fallback_random_cache",
            }
            source_key = source_key_map.get(str(image_source or ""), "other")
            image_source_counts[source_key] = int(image_source_counts.get(source_key, 0) or 0) + 1

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
            rendered_clip_count += 1
            _cleanup_segment_artifacts(temp_root, index)
            gc.collect()
            completed_ratio = (index + 1) / max(total, 1)
            _update_job(
                job_id,
                base_url,
                "running",
                stage_start + completed_ratio * stage_span,
                "render-segment",
                f"Scene {index + 1}/{total} rendered",
                current_segment=index + 1,
                total_segments=total,
                clip_count=rendered_clip_count,
                image_source_report=_build_image_source_report(image_source_counts),
            )

        if job_store.is_cancelled(job_id):
            _update_job(
                job_id,
                base_url,
                "cancelled",
                1.0,
                "cancelled",
                "Job cancelled",
                current_segment=total,
                total_segments=total,
                clip_count=rendered_clip_count,
                image_source_report=_build_image_source_report(image_source_counts),
            )
            return

        output_root = project_path(settings.output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        final_path = output_root / f"{job_id}.mp4"

        final_exists = final_path.exists() and final_path.is_file()
        final_size_ok = False
        if final_exists:
            try:
                final_size_ok = int(final_path.stat().st_size) >= 16384
            except Exception:
                final_size_ok = False

        if final_exists and final_size_ok:
            _update_job(
                job_id,
                base_url,
                "completed",
                1.0,
                "done",
                "Video already exists, job recovered",
                current_segment=total,
                total_segments=total,
                output_video_path=str(final_path),
                clip_count=rendered_clip_count,
                image_source_report=_build_image_source_report(image_source_counts),
            )
            return

        _update_job(
            job_id,
            base_url,
            "running",
            0.9,
            "compose",
            f"Composing final video ({total}/{total} scenes ready)",
            current_segment=total,
            total_segments=total,
            clip_count=rendered_clip_count,
        )

        clip_paths_for_compose = _collect_clip_paths_for_compose(clip_root=clip_root, total_segments=total)

        await run_in_threadpool(
            _render_final_sync,
            clip_paths_for_compose,
            final_path,
            payload.fps,
            payload.bgm_enabled,
            payload.bgm_volume,
            payload.render_mode,
            payload.novel_alias,
            payload.watermark_enabled,
            payload.watermark_type,
            payload.watermark_text,
            payload.watermark_image_path,
            payload.watermark_opacity,
        )

        if job_store.is_cancelled(job_id):
            _update_job(
                job_id,
                base_url,
                "cancelled",
                1.0,
                "cancelled",
                "Job cancelled during compose stage",
                current_segment=total,
                total_segments=total,
                clip_count=rendered_clip_count,
                image_source_report=_build_image_source_report(image_source_counts),
            )
            return

        _update_job(
            job_id,
            base_url,
            "completed",
            1.0,
            "done",
            "Video generation completed",
            current_segment=total,
            total_segments=total,
            output_video_path=str(final_path),
            clip_count=rendered_clip_count,
            image_source_report=_build_image_source_report(image_source_counts),
        )
    except Exception as exc:
        logger.exception("Video job failed: %s", job_id)
        _update_job(
            job_id,
            base_url,
            "failed",
            1.0,
            "error",
            f"Video generation failed: {exc}",
            clip_count=rendered_clip_count,
            image_source_report=_build_image_source_report(image_source_counts),
        )
    finally:
        job_store.clear_cancel(job_id)
        gc.collect()


def _start_job_runner(job_id: str, payload: GenerateVideoRequest, base_url: str) -> bool:
    with _RUNNER_LOCK:
        if job_id in _ACTIVE_RUNNERS:
            return False
        _ACTIVE_RUNNERS.add(job_id)

    def runner() -> None:
        try:
            asyncio.run(run_video_job(job_id=job_id, payload=payload, base_url=base_url))
        except Exception as exc:
            logger.exception("Job runner crashed before async job handler completed: %s", job_id)
            _update_job(job_id, base_url, "failed", 1.0, "error", f"Video generation failed: {exc}")
        finally:
            with _RUNNER_LOCK:
                _ACTIVE_RUNNERS.discard(job_id)

    thread = Thread(target=runner, daemon=True)
    thread.start()
    return True


def create_job(payload: GenerateVideoRequest, base_url: str) -> str:
    job_id = uuid4().hex
    job_store.save_payload(job_id, payload, base_url)
    _update_job(job_id, base_url, "queued", 0.0, "queued", "Job queued")
    _start_job_runner(job_id=job_id, payload=payload, base_url=base_url)
    return job_id


def resume_interrupted_jobs() -> list[str]:
    resumed: list[str] = []
    for job_id in job_store.list_incomplete_job_ids():
        loaded = job_store.load_payload(job_id)
        if not loaded:
            _update_job(job_id, "", "failed", 1.0, "error", "Job payload missing, cannot resume")
            continue

        payload, stored_base_url = loaded
        current = job_store.get(job_id)
        if current and current.status == "running":
            _update_job(
                job_id,
                stored_base_url,
                "queued",
                max(0.0, min(float(current.progress or 0.0), 0.95)),
                "resume",
                "Detected interrupted job, resuming from checkpoint",
                current_segment=current.current_segment,
                total_segments=current.total_segments,
                output_video_path=current.output_video_path,
                clip_count=current.clip_count,
            )

        started = _start_job_runner(job_id=job_id, payload=payload, base_url=stored_base_url)
        if started:
            resumed.append(job_id)
    return resumed


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
                current_segment=current.current_segment,
                total_segments=current.total_segments,
                output_video_url=current.output_video_url,
                output_video_path=current.output_video_path,
                clip_count=current.clip_count,
                clip_preview_urls=current.clip_preview_urls,
            )
        )
    return True


def resume_job(job_id: str, base_url: str) -> tuple[bool, str]:
    current = job_store.get(job_id)
    if not current:
        return False, "not_found"

    if current.status == "completed":
        return False, "already_completed"

    loaded = job_store.load_payload(job_id)
    if not loaded:
        _update_job(
            job_id,
            base_url,
            "failed",
            1.0,
            "error",
            "Job payload missing, cannot resume",
            current_segment=current.current_segment,
            total_segments=current.total_segments,
            output_video_path=current.output_video_path,
            clip_count=current.clip_count,
        )
        return False, "payload_missing"

    payload, stored_base_url = loaded
    effective_base_url = base_url or stored_base_url
    job_store.save_payload(job_id, payload, effective_base_url)
    job_store.clear_cancel(job_id)

    _update_job(
        job_id,
        effective_base_url,
        "queued",
        max(0.0, min(float(current.progress or 0.0), 0.95)),
        "resume",
        "Resume requested, continue from checkpoint",
        current_segment=current.current_segment,
        total_segments=current.total_segments,
        output_video_path=current.output_video_path,
        clip_count=current.clip_count,
    )

    started = _start_job_runner(job_id=job_id, payload=payload, base_url=effective_base_url)
    if not started:
        return True, "already_running"
    return True, "resume_requested"
