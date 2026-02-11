from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Thread
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool
from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, TextClip, concatenate_videoclips

from ..config import settings
from ..models import CharacterSuggestion, GenerateVideoRequest, JobStatus
from ..state import job_store
from .image_service import generate_image
from .llm_service import segment_by_fixed, segment_by_sentence, segment_by_smart
from .tts_service import synthesize_tts


def _parse_resolution(value: str) -> tuple[int, int]:
    try:
        width_raw, height_raw = value.lower().split("x")
        width = max(320, int(width_raw))
        height = max(320, int(height_raw))
        return width, height
    except Exception:
        return 1080, 1920


def _pick_character(characters: list[CharacterSuggestion], text: str) -> CharacterSuggestion:
    if not characters:
        return CharacterSuggestion(name="旁白", role="旁白", suggested_voice="zh-CN-YunxiNeural")
    for item in characters:
        if item.name and item.name in text:
            return item
    return characters[0]


def _subtitle_clip(text: str, duration: float, resolution: tuple[int, int], style: str):
    width, height = resolution
    fontsize = 56 if style == "center" else 46
    color = "#FFFFFF"
    stroke_color = "#111111"
    stroke_width = 2
    method = "caption"
    size = (width - 120, None)
    y_pos = int(height * 0.78)
    if style == "center":
        y_pos = int(height * 0.45)
    if style == "danmaku":
        y_pos = int(height * 0.15)
        fontsize = 38
    if style == "highlight":
        color = "#F9E96A"

    clip = TextClip(
        text=text,
        font_size=fontsize,
        color=color,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        method=method,
        size=size,
    )
    return clip.with_duration(duration).with_position(("center", y_pos))


def _render_video_sync(clip_infos: list[dict], output_path: Path, fps: int, resolution: tuple[int, int], subtitle_style: str) -> None:
    clips = []
    for info in clip_infos:
        image_clip = ImageClip(info["image_path"]).with_duration(info["duration"]).resized(new_size=resolution)
        audio_clip = AudioFileClip(info["audio_path"])
        base = image_clip.with_audio(audio_clip)
        subtitle = _subtitle_clip(info["text"], info["duration"], resolution, subtitle_style)
        composed = CompositeVideoClip([base, subtitle], size=resolution)
        clips.append(composed)

    final = concatenate_videoclips(clips, method="compose")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    final.write_videofile(
        str(output_path),
        fps=fps,
        audio_codec="aac",
        codec="libx264",
        logger=None,
    )
    final.close()
    for clip in clips:
        clip.close()


async def _segment_text(text: str, method: str, model_id: str | None) -> list[str]:
    if method == "sentence":
        return segment_by_sentence(text)
    if method == "fixed":
        return segment_by_fixed(text)
    return await segment_by_smart(text, model_id)


async def run_video_job(job_id: str, payload: GenerateVideoRequest, base_url: str) -> None:
    output_root = Path(settings.output_dir)
    temp_root = Path(settings.temp_dir) / job_id
    temp_root.mkdir(parents=True, exist_ok=True)

    def update(status: str, progress: float, step: str, message: str, output_video_path: str | None = None):
        job_store.set(
            JobStatus(
                job_id=job_id,
                status=status,
                progress=progress,
                step=step,
                message=message,
                output_video_url=f"{base_url}/api/jobs/{job_id}/video" if output_video_path else None,
                output_video_path=output_video_path,
            )
        )

    try:
        update("running", 0.05, "segment", "正在分段文本")
        segments = await _segment_text(payload.text, payload.segment_method, payload.model_id)
        segments = segments[: settings.max_segments]
        if not segments:
            raise ValueError("No text segments produced")

        resolution = _parse_resolution(payload.resolution)
        clip_infos: list[dict] = []
        total = len(segments)

        for index, segment_text in enumerate(segments):
            current_progress = 0.08 + (index / total) * 0.78
            update("running", current_progress, "render-segment", f"生成第 {index + 1}/{total} 段素材")

            character = _pick_character(payload.characters, segment_text)
            prompt = f"{character.base_prompt or character.appearance or character.name}, {character.suggested_style}, {segment_text}"

            image_path = temp_root / f"segment_{index:03d}.png"
            audio_path = temp_root / f"segment_{index:03d}.mp3"

            image_task = asyncio.create_task(generate_image(prompt=prompt, output_path=image_path, resolution=resolution))
            audio_task = asyncio.create_task(
                synthesize_tts(text=segment_text, voice=character.suggested_voice, output_path=audio_path)
            )

            image_result, audio_bundle = await asyncio.gather(image_task, audio_task)
            audio_result_path, duration = audio_bundle

            clip_infos.append(
                {
                    "image_path": str(image_result),
                    "audio_path": str(audio_result_path),
                    "duration": max(duration, 1.0),
                    "text": segment_text,
                }
            )

        update("running", 0.9, "compose", "正在合成最終視頻")
        output_root.mkdir(parents=True, exist_ok=True)
        output_path = output_root / f"{job_id}.mp4"

        await run_in_threadpool(
            _render_video_sync,
            clip_infos,
            output_path,
            payload.fps,
            resolution,
            payload.subtitle_style,
        )

        update("completed", 1.0, "done", "視頻生成完成", output_video_path=str(output_path))
    except Exception as exc:
        update("failed", 1.0, "error", f"視頻生成失敗: {exc}")


def create_job(payload: GenerateVideoRequest, base_url: str) -> str:
    job_id = uuid4().hex
    job_store.set(JobStatus(job_id=job_id, status="queued", progress=0.0, step="queued", message="任務已排隊"))

    def _runner() -> None:
        asyncio.run(run_video_job(job_id=job_id, payload=payload, base_url=base_url))

    thread = Thread(target=_runner, daemon=True)
    thread.start()
    return job_id
