from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image

from ..config import project_path, settings
from ..models import CharacterSuggestion


logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()


def _index_path() -> Path:
    return project_path(settings.scene_cache_index_path)


def _cache_image_root() -> Path:
    return project_path(settings.scene_cache_dir)


def ensure_scene_cache_paths() -> None:
    image_root = _cache_image_root()
    index_path = _index_path()
    image_root.mkdir(parents=True, exist_ok=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if not index_path.exists():
        index_path.write_text(json.dumps({"entries": []}, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_index_unlocked() -> dict:
    path = _index_path()
    if not path.exists():
        return {"entries": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read scene cache index, resetting")
        return {"entries": []}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return {"entries": []}
    return {"entries": entries}


def _save_index_unlocked(data: dict) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _tokenize(text: str) -> set[str]:
    raw = _normalize_text(text).lower()
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", raw)
    return {token for token in cleaned.split() if len(token) >= 2}


def _descriptor_to_text(descriptor: dict) -> str:
    parts = [
        descriptor.get("character_name", ""),
        descriptor.get("character_role", ""),
        descriptor.get("character_appearance", ""),
        descriptor.get("character_personality", ""),
        descriptor.get("character_prompt", ""),
        descriptor.get("action_hint", ""),
        descriptor.get("location_hint", ""),
        descriptor.get("segment_text", ""),
    ]
    return " ".join(_normalize_text(part) for part in parts if _normalize_text(part))


def _parse_json_object(text: str) -> dict | None:
    content = (text or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _score_overlap(target: dict, candidate: dict) -> float:
    target_text = _descriptor_to_text(target)
    candidate_text = _descriptor_to_text(candidate.get("descriptor") or {})
    target_tokens = _tokenize(target_text)
    candidate_tokens = _tokenize(candidate_text)
    if not target_tokens or not candidate_tokens:
        return 0.0
    intersection = len(target_tokens & candidate_tokens)
    union = len(target_tokens | candidate_tokens)
    jaccard = intersection / max(1, union)

    target_name = _normalize_text(target.get("character_name", "")).lower()
    cand_name = _normalize_text((candidate.get("descriptor") or {}).get("character_name", "")).lower()
    name_bonus = 0.15 if target_name and cand_name and target_name == cand_name else 0.0
    return min(1.0, jaccard + name_bonus)


def build_scene_descriptor(character: CharacterSuggestion, segment_text: str, prompt: str) -> dict:
    sentence = _normalize_text(segment_text)
    parts = [
        part.strip()
        for part in re.split(r"[\u3002\uff01\uff1f\uff1b\uff0c,!?;]", sentence)
        if part.strip()
    ]
    action_hint = parts[0] if parts else sentence

    location_hint = ""
    location_markers = [
        "\u5728",      # at/in
        "\u4e8e",      # in/at
        "\u5230",      # to
        "\u6765\u5230",  # arrive at
        "\u8fdb\u5165",  # enter
        "\u623f\u95f4",  # room
        "\u8857",      # street
        "\u5b66\u6821",  # school
        "\u516c\u56ed",  # park
        "\u68ee\u6797",  # forest
        "\u529e\u516c\u5ba4",  # office
        "\u5bb6",      # home
    ]
    for part in parts[1:]:
        if any(marker in part for marker in location_markers):
            location_hint = part
            break

    return {
        "character_name": _normalize_text(character.name),
        "character_role": _normalize_text(character.role),
        "character_appearance": _normalize_text(character.appearance),
        "character_personality": _normalize_text(character.personality),
        "character_prompt": _normalize_text(character.base_prompt),
        "action_hint": _normalize_text(action_hint)[:180],
        "location_hint": _normalize_text(location_hint)[:180],
        "segment_text": sentence[:600],
        "reference_image_path": _normalize_text(character.reference_image_path or ""),
        "prompt": _normalize_text(prompt)[:900],
    }


async def _llm_match_candidate(
    target_descriptor: dict,
    candidates: list[dict],
    model_id: str | None,
) -> tuple[str | None, float, str]:
    if not settings.llm_api_key or not candidates:
        return None, 0.0, "llm disabled or no candidates"

    url = f"{settings.llm_api_base_url.rstrip('/')}/chat/completions"
    model = model_id or settings.llm_default_model
    prompt = {
        "task": "select_reusable_scene_image",
        "rule": [
            "Only compare textual scene descriptions, no image input.",
            "Reuse only when same character + similar action + similar scene context.",
            "Return strict JSON only.",
        ],
        "target": target_descriptor,
        "candidates": [
            {
                "id": item["id"],
                "descriptor": item.get("descriptor") or {},
                "prompt": item.get("prompt") or "",
                "heuristic_score": item.get("heuristic_score", 0.0),
            }
            for item in candidates
        ],
        "output_schema": {
            "should_reuse": True,
            "selected_id": "candidate-id-or-null",
            "confidence": 0.0,
            "reason": "short reason",
        },
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict JSON selector for scene-image reuse. Output JSON only.",
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
    except Exception:
        logger.exception("LLM scene cache matching failed")
        return None, 0.0, "llm request failed"

    parsed = _parse_json_object(content)
    if not parsed:
        return None, 0.0, "llm response unparsable"

    selected_id = parsed.get("selected_id")
    should_reuse = bool(parsed.get("should_reuse", False))
    confidence = float(parsed.get("confidence", 0.0))
    reason = str(parsed.get("reason", ""))[:240]
    if not should_reuse or not selected_id:
        return None, confidence, reason or "llm says no match"
    return str(selected_id), confidence, reason or "llm matched"


async def find_reusable_scene_image(
    scene_descriptor: dict,
    model_id: str | None = None,
) -> dict | None:
    ensure_scene_cache_paths()

    with _CACHE_LOCK:
        payload = _load_index_unlocked()
        entries = [item for item in payload.get("entries", []) if isinstance(item, dict)]

    viable: list[dict] = []
    for entry in entries:
        image_path = Path(entry.get("image_path") or "")
        if not image_path.exists():
            continue
        score = _score_overlap(scene_descriptor, entry)
        enriched = {**entry, "heuristic_score": score}
        viable.append(enriched)

    if not viable:
        return None

    ranked = sorted(viable, key=lambda item: float(item.get("heuristic_score", 0.0)), reverse=True)
    top = ranked[:8]
    best = top[0]
    best_score = float(best.get("heuristic_score", 0.0))

    if best_score >= 0.92:
        return {
            "image_path": str(best["image_path"]),
            "match_type": "heuristic",
            "confidence": best_score,
            "reason": "high heuristic overlap",
            "entry_id": best.get("id"),
        }

    selected_id, confidence, reason = await _llm_match_candidate(scene_descriptor, top, model_id=model_id)
    if selected_id and confidence >= 0.62:
        selected = next((item for item in top if str(item.get("id")) == selected_id), None)
        if selected:
            return {
                "image_path": str(selected["image_path"]),
                "match_type": "llm",
                "confidence": confidence,
                "reason": reason,
                "entry_id": selected.get("id"),
            }

    if best_score >= 0.78:
        return {
            "image_path": str(best["image_path"]),
            "match_type": "heuristic-fallback",
            "confidence": best_score,
            "reason": "fallback heuristic threshold",
            "entry_id": best.get("id"),
        }
    return None


def save_scene_image_cache_entry(
    scene_descriptor: dict,
    source_image_path: str | Path,
    prompt: str,
) -> dict | None:
    ensure_scene_cache_paths()
    source = Path(source_image_path)
    if not source.exists():
        return None

    image_root = _cache_image_root()
    image_root.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() or ".png"
    filename = f"scene_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}{suffix}"
    target = image_root / filename

    shutil.copy2(source, target)

    entry = {
        "id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "image_path": target.as_posix(),
        "prompt": _normalize_text(prompt)[:1200],
        "descriptor": scene_descriptor,
    }

    with _CACHE_LOCK:
        payload = _load_index_unlocked()
        entries = [item for item in payload.get("entries", []) if isinstance(item, dict)]
        entries.append(entry)
        payload["entries"] = entries[-3000:]
        _save_index_unlocked(payload)
    return entry


def render_cached_image_to_output(
    cached_image_path: str | Path,
    output_path: str | Path,
    resolution: tuple[int, int],
) -> Path:
    src = Path(cached_image_path)
    dst = Path(output_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    image = Image.open(src).convert("RGB")
    image = image.resize(resolution)
    image.save(dst)
    return dst
