from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from ..config import project_path, settings
from ..models import CharacterImageItem
from .image_service import generate_image


logger = logging.getLogger(__name__)


def _build_character_reference_prompt(prompt: str) -> str:
    base = (prompt or "").strip()
    full_body_rules = (
        "Character reference sheet style. "
        "Show only one character (the target character) in the image. "
        "No other people, no crowd, no background characters, no extra faces. "
        "Must show full body from head to toe in frame. "
        "Do NOT crop to half body, portrait, or close-up. "
        "Include key props/weapons/accessories in full view. "
        "Keep anatomy complete and visible."
    )
    if not base:
        return full_body_rules
    return f"{base}. {full_body_rules}"


def list_character_reference_images(base_url: str) -> list[CharacterImageItem]:
    root = project_path(settings.character_ref_dir)
    root.mkdir(parents=True, exist_ok=True)
    result: list[CharacterImageItem] = []
    for path in sorted(root.glob("*")):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        rel = path.as_posix()
        encoded_name = quote(path.name)
        result.append(
            CharacterImageItem(
                path=rel,
                url=f"{base_url}/assets/character_refs/{encoded_name}",
                filename=path.name,
            )
        )
    return result


async def create_character_reference_image(
    character_name: str,
    prompt: str,
    resolution: tuple[int, int],
    base_url: str,
) -> CharacterImageItem:
    root = project_path(settings.character_ref_dir)
    root.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch for ch in character_name if ch.isalnum() or ch in {"_", "-"}) or "character"
    filename = f"{safe_name}_{uuid4().hex[:8]}.png"
    output_path = root / filename

    final_prompt = _build_character_reference_prompt(prompt)
    await generate_image(prompt=final_prompt, output_path=output_path, resolution=resolution)

    rel = output_path.as_posix()
    encoded_name = quote(filename)
    return CharacterImageItem(
        path=rel,
        url=f"{base_url}/assets/character_refs/{encoded_name}",
        filename=filename,
    )
