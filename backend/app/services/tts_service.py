from __future__ import annotations

import asyncio
import logging
import wave
from pathlib import Path

import httpx
from edge_tts import Communicate
from mutagen import File as MutagenFile

from ..config import settings


logger = logging.getLogger(__name__)


def _estimate_duration_by_text(text: str) -> float:
    chars = max(len(text), 1)
    return max(chars * 0.22, 1.5)


def _create_silent_wav(path: Path, duration_sec: float, sample_rate: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_count = int(duration_sec * sample_rate)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        silence = b"\x00\x00" * frame_count
        handle.writeframes(silence)
    return path


async def synthesize_tts(text: str, voice: str, output_path: Path) -> tuple[Path, float]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    text_content = str(text or "").strip()
    if not text_content:
        fallback_path = output_path.with_suffix(".wav")
        duration = 1.5
        _create_silent_wav(fallback_path, duration)
        logger.warning("TTS received empty text, generated silent wav: %s", fallback_path)
        return fallback_path, duration

    if settings.tts_api_url:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    settings.tts_api_url,
                    json={"text": text_content, "voice": voice},
                )
                response.raise_for_status()
                content_type = str(response.headers.get("content-type") or "").lower()
                if not response.content:
                    raise RuntimeError("remote TTS returned empty response body")
                if "audio" not in content_type and "application/octet-stream" not in content_type:
                    raise RuntimeError(f"remote TTS returned unexpected content-type: {content_type}")
                output_path.write_bytes(response.content)
                duration = get_audio_duration(output_path)
                if output_path.exists() and output_path.stat().st_size > 0 and duration <= 0:
                    duration = _estimate_duration_by_text(text_content)
                if duration <= 0:
                    raise RuntimeError("remote TTS wrote invalid audio file")
                return output_path, duration
        except Exception as exc:
            logger.warning("Remote TTS failed, fallback to edge-tts: voice=%s error=%s", voice, exc)

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            communicator = Communicate(text=text_content, voice=voice)
            await asyncio.wait_for(communicator.save(str(output_path)), timeout=45)
            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError("edge-tts wrote empty file")
            duration = get_audio_duration(output_path)
            if duration <= 0:
                duration = _estimate_duration_by_text(text_content)
            return output_path, duration
        except Exception as exc:
            last_error = exc
            if attempt < 1:
                await asyncio.sleep(0.35)

    fallback_path = output_path.with_suffix(".wav")
    duration = _estimate_duration_by_text(text_content)
    _create_silent_wav(fallback_path, duration)
    logger.warning(
        "Edge TTS failed after retries, generated silent wav: voice=%s error=%s path=%s",
        voice,
        last_error,
        fallback_path,
    )
    return fallback_path, duration


def get_audio_duration(path: Path) -> float:
    try:
        parsed = MutagenFile(path)
        if parsed is not None and parsed.info is not None:
            duration = float(getattr(parsed.info, "length", 0.0))
            return max(duration, 0.0)
    except Exception:
        pass

    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
                if rate > 0:
                    return frames / float(rate)
        except Exception:
            return 0.0
    return 0.0
