from __future__ import annotations

import asyncio
import base64
import re
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from ..config import settings


def _extract_first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s\]\)]+", text)
    if match:
        return match.group(0)
    return None


async def _placeholder_image(prompt: str, output_path: Path, size: tuple[int, int]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size=size, color=(24, 28, 40))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 32)
    except Exception:
        font = ImageFont.load_default()
    draw.text((50, 50), "Generated Placeholder", fill=(230, 230, 240), font=font)
    wrapped = prompt[:180]
    draw.text((50, 130), wrapped, fill=(170, 180, 200), font=font)
    img.save(output_path)
    return output_path


def _build_messages(prompt: str, reference_image_path: str | None = None) -> list[dict]:
    if reference_image_path:
        ref = Path(reference_image_path)
        if ref.exists() and ref.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            mime = "image/png" if ref.suffix.lower() == ".png" else "image/jpeg"
            encoded = base64.b64encode(ref.read_bytes()).decode("utf-8")
            return [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                    ],
                }
            ]
    return [{"role": "user", "content": prompt}]


async def generate_image(
    prompt: str,
    output_path: Path,
    resolution: tuple[int, int],
    reference_image_path: str | None = None,
) -> Path:
    if not settings.image_api_key:
        return await _placeholder_image(prompt, output_path, resolution)

    headers = {
        "Authorization": f"Bearer {settings.image_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.image_model,
        "messages": _build_messages(prompt, reference_image_path=reference_image_path),
        "stream": True,
    }
    url = f"{settings.image_api_url.rstrip('/')}/chat/completions"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    async def _remote_generate() -> Path:
        image_url: str | None = None
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[len("data:") :].strip()
                    if line == "[DONE]":
                        break
                    if not line.startswith("{"):
                        continue
                    try:
                        chunk = httpx.Response(200, content=line).json()
                    except Exception:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if not content:
                        continue
                    maybe_url = _extract_first_url(content)
                    if maybe_url:
                        image_url = maybe_url

            if not image_url:
                return await _placeholder_image(prompt, output_path, resolution)

            image_response = await client.get(image_url)
            image_response.raise_for_status()
            img = Image.open(BytesIO(image_response.content)).convert("RGB")
            img = img.resize(resolution)
            img.save(output_path)
            return output_path

    try:
        return await asyncio.wait_for(_remote_generate(), timeout=45)
    except Exception:
        return await _placeholder_image(prompt, output_path, resolution)


async def use_reference_or_generate(
    prompt: str,
    output_path: Path,
    resolution: tuple[int, int],
    reference_image_path: str | None,
) -> Path:
    try:
        return await generate_image(
            prompt=prompt,
            output_path=output_path,
            resolution=resolution,
            reference_image_path=reference_image_path,
        )
    except Exception:
        if reference_image_path:
            ref = Path(reference_image_path)
            if ref.exists() and ref.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                img = Image.open(ref).convert("RGB").resize(resolution)
                img.save(output_path)
                return output_path
        return await _placeholder_image(prompt, output_path, resolution)
