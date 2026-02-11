from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import httpx
from edge_tts import Communicate
from mutagen import File as MutagenFile

from ..config import settings


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

    if settings.tts_api_url:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    settings.tts_api_url,
                    json={"text": text, "voice": voice},
                )
                response.raise_for_status()
                output_path.write_bytes(response.content)
                duration = get_audio_duration(output_path)
                if duration <= 0:
                    duration = _estimate_duration_by_text(text)
                return output_path, duration
        except Exception:
            pass

    try:
        communicator = Communicate(text=text, voice=voice)
        await asyncio.wait_for(communicator.save(str(output_path)), timeout=45)
        duration = get_audio_duration(output_path)
        if duration <= 0:
            duration = _estimate_duration_by_text(text)
        return output_path, duration
    except Exception:
        fallback_path = output_path.with_suffix(".wav")
        duration = _estimate_duration_by_text(text)
        _create_silent_wav(fallback_path, duration)
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
