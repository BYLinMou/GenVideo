from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from threading import Thread
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool
from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, TextClip, concatenate_videoclips

from ..config import project_path, settings
from ..models import CharacterSuggestion, GenerateVideoRequest, JobStatus
from ..state import job_store
from .image_service import use_reference_or_generate
from .llm_service import (
    group_sentences,
    segment_by_fixed,
    segment_by_smart,
    split_sentences,
)
from .tts_service import synthesize_tts


logger = logging.getLogger(__name__)


def _parse_resolution(value: str) -> tuple[int, int]:
    try:
        width_raw, height_raw = value.lower().split("x")
        return max(320, int(width_raw)), max(320, int(height_raw))
    except Exception:
        return 1080, 1920


def _pick_character(characters: list[CharacterSuggestion], text: str) -> CharacterSuggestion:
    if not characters:
        return CharacterSuggestion(name="旁白", role="旁白", voice_id="zh-CN-YunxiNeural")
    for item in characters:
        if item.name and item.name in text:
            return item
    return characters[0]


def _subtitle_clip(text: str, duration: float, resolution: tuple[int, int], style: str):
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
    elif style == "highlight":
        color = "#F9E96A"

    clip = TextClip(
        text=text,
        font_size=fontsize,
        color=color,
        stroke_color=stroke_color,
        stroke_width=2,
        method="caption",
        size=(width - 120, None),
    )
    return clip.with_duration(duration).with_position(("center", y_pos))


def _render_clip_sync(
    image_path: str,
    audio_path: str,
    text: str,
    duration: float,
    output_path: Path,
    fps: int,
    resolution: tuple[int, int],
    subtitle_style: str,
) -> None:
    image_clip = ImageClip(image_path).with_duration(duration).resized(new_size=resolution)
    audio_clip = AudioFileClip(audio_path)
    base = image_clip.with_audio(audio_clip)
    subtitle = _subtitle_clip(text, duration, resolution, subtitle_style)
    composed = CompositeVideoClip([base, subtitle], size=resolution)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    composed.write_videofile(str(output_path), fps=fps, audio_codec="aac", codec="libx264", logger=None)

    composed.close()
    subtitle.close()
    base.close()
    audio_clip.close()
    image_clip.close()


def _render_final_sync(clip_paths: list[str], output_path: Path, fps: int) -> None:
    video_clips = []
    try:
        from moviepy import VideoFileClip

        for clip_path in clip_paths:
            video_clips.append(VideoFileClip(clip_path))
        final = concatenate_videoclips(video_clips, method="compose")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        final.write_videofile(str(output_path), fps=fps, audio_codec="aac", codec="libx264", logger=None)
        final.close()
    finally:
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
    previews = []
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


async def run_video_job(job_id: str, payload: GenerateVideoRequest, base_url: str) -> None:
    temp_root = project_path(settings.temp_dir) / job_id
    clip_root = temp_root / "clips"
    clip_root.mkdir(parents=True, exist_ok=True)

    try:
        _update_job(job_id, base_url, "running", 0.05, "segment", "正在分段文本")
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
                _update_job(job_id, base_url, "cancelled", 1.0, "cancelled", "任務已取消", clip_paths=clip_paths)
                return

            progress = 0.1 + (index / total) * 0.75
            _update_job(
                job_id,
                base_url,
                "running",
                progress,
                "render-segment",
                f"正在生成第 {index + 1}/{total} 段（共 {sentence_count or '-'} 句）",
                clip_paths=clip_paths,
            )

            character = _pick_character(payload.characters, segment_text)
            prompt = f"{character.base_prompt or character.appearance or character.name}，{segment_text}"

            image_path = temp_root / f"segment_{index:04d}.png"
            audio_path = temp_root / f"segment_{index:04d}.mp3"
            clip_path = clip_root / f"clip_{index:04d}.mp4"

            image_task = asyncio.create_task(
                use_reference_or_generate(
                    prompt=prompt,
                    output_path=image_path,
                    resolution=resolution,
                    reference_image_path=character.reference_image_path,
                )
            )
            audio_task = asyncio.create_task(
                synthesize_tts(text=segment_text, voice=character.voice_id, output_path=audio_path)
            )

            image_result, audio_bundle = await asyncio.gather(image_task, audio_task)
            audio_result_path, duration = audio_bundle

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
            )
            clip_paths.append(str(clip_path))

        if job_store.is_cancelled(job_id):
            _update_job(job_id, base_url, "cancelled", 1.0, "cancelled", "任務已取消", clip_paths=clip_paths)
            return

        _update_job(job_id, base_url, "running", 0.9, "compose", "正在合成最終視頻", clip_paths=clip_paths)
        output_root = project_path(settings.output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        final_path = output_root / f"{job_id}.mp4"
        await run_in_threadpool(_render_final_sync, clip_paths, final_path, payload.fps)

        if job_store.is_cancelled(job_id):
            _update_job(job_id, base_url, "cancelled", 1.0, "cancelled", "任务在合成阶段被取消", clip_paths=clip_paths)
            return

        _update_job(
            job_id,
            base_url,
            "completed",
            1.0,
            "done",
            "視頻生成完成",
            output_video_path=str(final_path),
            clip_paths=clip_paths,
        )
    except Exception as exc:
        logger.exception("Video job failed: %s", job_id)
        _update_job(job_id, base_url, "failed", 1.0, "error", f"視頻生成失敗: {exc}")
    finally:
        job_store.clear_cancel(job_id)


def create_job(payload: GenerateVideoRequest, base_url: str) -> str:
    job_id = uuid4().hex
    _update_job(job_id, base_url, "queued", 0.0, "queued", "任務已排隊")

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
                message="已请求取消，稍后停止",
                output_video_url=current.output_video_url,
                output_video_path=current.output_video_path,
                clip_count=current.clip_count,
                clip_preview_urls=current.clip_preview_urls,
            )
        )
    return True
