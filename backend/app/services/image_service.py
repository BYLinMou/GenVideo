from __future__ import annotations

import asyncio
import base64
import logging
import re
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from ..config import settings
from .prompt_templates import DEFAULT_IMAGE_PROMPT, build_image_retry_prompt


logger = logging.getLogger(__name__)


class ImageGenerationError(RuntimeError):
    pass


def _extract_first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s\]\)]+", text)
    if match:
        return match.group(0)
    return None


def _build_messages(
    prompt: str,
    reference_image_path: str | None = None,
    extra_reference_image_paths: list[str] | None = None,
) -> list[dict]:
    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        prompt_text = DEFAULT_IMAGE_PROMPT

    candidate_paths: list[str] = []
    if reference_image_path:
        candidate_paths.append(str(reference_image_path))
    for raw in (extra_reference_image_paths or []):
        if raw:
            candidate_paths.append(str(raw))

    dedup_paths: list[str] = []
    seen: set[str] = set()
    for raw in candidate_paths:
        key = str(raw).replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        dedup_paths.append(raw)

    image_parts: list[dict] = []
    for raw in dedup_paths[:2]:
        ref = Path(raw)
        if ref.exists() and ref.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            mime = "image/png" if ref.suffix.lower() == ".png" else "image/jpeg"
            encoded = base64.b64encode(ref.read_bytes()).decode("utf-8")
            image_parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})

    if image_parts:
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    *image_parts,
                ],
            }
        ]
    return [{"role": "user", "content": prompt_text}]


async def generate_image(
    prompt: str,
    output_path: Path,
    resolution: tuple[int, int],
    reference_image_path: str | None = None,
    extra_reference_image_paths: list[str] | None = None,
    aspect_ratio: str | None = None,
) -> Path:
    if not settings.image_api_key:
        raise ImageGenerationError("image_api_key is not configured")

    headers = {
        "Authorization": f"Bearer {settings.image_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.image_model,
        "messages": _build_messages(
            prompt,
            reference_image_path=reference_image_path,
            extra_reference_image_paths=extra_reference_image_paths,
        ),
        "stream": True,
    }
    if aspect_ratio:
        payload["extra_body"] = {"aspect_ratio": aspect_ratio}
    url = f"{settings.image_api_url.rstrip('/')}/chat/completions"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    async def _remote_generate(req_payload: dict) -> Path:
        image_url: str | None = None
        seen_content = False
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, headers=headers, json=req_payload) as response:
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
                    seen_content = True
                    maybe_url = _extract_first_url(content)
                    if maybe_url:
                        logger.info("Image stream URL candidate: %s", maybe_url[:500])
                        image_url = maybe_url

            if not image_url:
                detail = "no content" if not seen_content else "content without image url"
                raise ImageGenerationError(f"image stream finished but {detail}")

            image_response = await client.get(image_url)
            image_response.raise_for_status()
            img = Image.open(BytesIO(image_response.content)).convert("RGB")
            logger.info("Image upstream size=%sx%s, target frame=%sx%s", img.width, img.height, resolution[0], resolution[1])
            img.save(output_path)
            return output_path

    try:
        return await asyncio.wait_for(_remote_generate(payload), timeout=45)
    except Exception as first_error:
        logger.warning("Primary image generation failed: %s", first_error)
        # Some proxy/image backends may return HTTP 200 but no image URL for pure CJK prompts.
        # Retry with an English wrapper before failing hard.
        retry_prompt = build_image_retry_prompt(prompt)
        retry_payload = {
            "model": settings.image_model,
            "messages": _build_messages(
                retry_prompt,
                reference_image_path=reference_image_path,
                extra_reference_image_paths=extra_reference_image_paths,
            ),
            "stream": True,
        }
        if aspect_ratio:
            retry_payload["extra_body"] = {"aspect_ratio": aspect_ratio}
        try:
            return await asyncio.wait_for(_remote_generate(retry_payload), timeout=45)
        except Exception as retry_error:
            logger.exception("Retry image generation failed: %s", retry_error)
            raise ImageGenerationError(f"image generation failed after retry: {retry_error}") from retry_error


async def use_reference_or_generate(
    prompt: str,
    output_path: Path,
    resolution: tuple[int, int],
    reference_image_path: str | None,
    extra_reference_image_paths: list[str] | None = None,
    aspect_ratio: str | None = None,
) -> Path:
    return await generate_image(
        prompt=prompt,
        output_path=output_path,
        resolution=resolution,
        reference_image_path=reference_image_path,
        extra_reference_image_paths=extra_reference_image_paths,
        aspect_ratio=aspect_ratio,
    )

