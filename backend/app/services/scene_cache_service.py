from __future__ import annotations

import json
import hashlib
import logging
import re
import shutil
import sqlite3
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


def _db_path() -> Path:
    return project_path(settings.scene_cache_db_path)


def _connect_db() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _ensure_db_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_entries (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            image_path TEXT NOT NULL,
            prompt TEXT NOT NULL,
            descriptor_json TEXT NOT NULL,
            match_profile_json TEXT NOT NULL,
            reference_image_path TEXT NOT NULL,
            character_name TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scene_entries_created_at ON scene_entries(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scene_entries_ref_path ON scene_entries(reference_image_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scene_entries_char_name ON scene_entries(character_name)")
    conn.commit()


def _entry_to_db_tuple(entry: dict) -> tuple[str, str, str, str, str, str, str, str]:
    descriptor = entry.get("descriptor") or {}
    profile = entry.get("match_profile") or {}
    ref_path = _normalize_text(str((descriptor.get("reference_image_path") or profile.get("reference_image_path") or "")))
    char_name = _normalize_text(str((descriptor.get("character_name") or profile.get("character_name") or "")))
    return (
        str(entry.get("id") or uuid4().hex),
        str(entry.get("created_at") or datetime.now(timezone.utc).isoformat()),
        _normalize_text(str(entry.get("image_path") or "")),
        _normalize_text(str(entry.get("prompt") or ""))[:220],
        json.dumps(descriptor, ensure_ascii=False),
        json.dumps(profile, ensure_ascii=False),
        ref_path,
        char_name,
    )


def _db_row_to_entry(row: sqlite3.Row) -> dict:
    descriptor = {}
    match_profile = {}
    try:
        descriptor = json.loads(row["descriptor_json"] or "{}")
    except Exception:
        descriptor = {}
    try:
        match_profile = json.loads(row["match_profile_json"] or "{}")
    except Exception:
        match_profile = {}
    return {
        "id": str(row["id"]),
        "created_at": str(row["created_at"]),
        "image_path": str(row["image_path"]),
        "prompt": str(row["prompt"]),
        "descriptor": descriptor,
        "match_profile": match_profile,
    }


def _load_entries_from_db(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, created_at, image_path, prompt, descriptor_json, match_profile_json
        FROM scene_entries
        ORDER BY created_at ASC
        """
    ).fetchall()
    return [_db_row_to_entry(row) for row in rows]


def _insert_entry_to_db(conn: sqlite3.Connection, entry: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO scene_entries(
            id, created_at, image_path, prompt, descriptor_json, match_profile_json, reference_image_path, character_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _entry_to_db_tuple(entry),
    )


def _prune_db_entries(conn: sqlite3.Connection, keep: int = 3000) -> None:
    conn.execute(
        """
        DELETE FROM scene_entries
        WHERE id IN (
            SELECT id FROM scene_entries
            ORDER BY created_at DESC
            LIMIT -1 OFFSET ?
        )
        """,
        (int(max(1, keep)),),
    )


def _migrate_index_to_db_if_needed(conn: sqlite3.Connection) -> None:
    count_row = conn.execute("SELECT COUNT(1) AS cnt FROM scene_entries").fetchone()
    existing = int((count_row["cnt"] if count_row and "cnt" in count_row.keys() else 0) or 0)
    if existing > 0:
        return

    index_path = _index_path()
    if not index_path.exists():
        return

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read scene cache index during db migration")
        return

    entries = payload.get("entries") if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        return

    migrated = 0
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        row = _migrate_entry_schema(raw)
        if not row:
            continue
        image_path = Path(str(row.get("image_path") or ""))
        if not image_path.exists():
            continue
        _insert_entry_to_db(conn, row)
        migrated += 1

    if migrated:
        _prune_db_entries(conn, keep=3000)
        conn.commit()
        logger.info("Migrated %s scene-cache entries from index.json to sqlite", migrated)


def _index_path() -> Path:
    return project_path(settings.scene_cache_index_path)


def _cache_image_root() -> Path:
    return project_path(settings.scene_cache_dir)


def ensure_scene_cache_paths() -> None:
    image_root = _cache_image_root()
    index_path = _index_path()
    db_path = _db_path()
    image_root.mkdir(parents=True, exist_ok=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not index_path.exists():
        index_path.write_text(json.dumps({"schema_version": 2, "entries": []}, ensure_ascii=False, indent=2), encoding="utf-8")

    with _CACHE_LOCK:
        conn = _connect_db()
        try:
            _ensure_db_schema(conn)
            _migrate_index_to_db_if_needed(conn)
        finally:
            conn.close()


def _load_index_unlocked() -> dict:
    path = _index_path()
    if not path.exists():
        return {"schema_version": 2, "entries": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read scene cache index, resetting")
        return {"schema_version": 2, "entries": []}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return {"schema_version": 2, "entries": []}

    migrated_entries: list[dict] = []
    dirty = bool(payload.get("schema_version") != 2)
    for raw in entries:
        if not isinstance(raw, dict):
            dirty = True
            continue
        migrated = _migrate_entry_schema(raw)
        if not migrated:
            dirty = True
            continue
        if migrated != raw:
            dirty = True
        migrated_entries.append(migrated)

    data = {"schema_version": 2, "entries": migrated_entries}
    if dirty:
        _save_index_unlocked(data)
    return data


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


def _tokenize_ordered(text: str, limit: int) -> list[str]:
    raw = _normalize_text(text).lower()
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", raw)
    output: list[str] = []
    seen: set[str] = set()
    for token in cleaned.split():
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        output.append(token)
        if len(output) >= limit:
            break
    return output


def _common_token_count(tokens_a: set[str], tokens_b: set[str]) -> int:
    if not tokens_a or not tokens_b:
        return 0
    return len(tokens_a & tokens_b)


def _normalize_scene_descriptor(descriptor: dict) -> dict:
    character_name = _normalize_text(str(descriptor.get("character_name", "")))
    character_role = _normalize_text(str(descriptor.get("character_role", "")))
    reference_image_path = _normalize_text(str(descriptor.get("reference_image_path", "")))
    action_hint = _normalize_text(str(descriptor.get("action_hint", "")))[:220]
    location_hint = _normalize_text(str(descriptor.get("location_hint", "")))[:220]
    segment_text = _normalize_text(str(descriptor.get("segment_text", "")))[:700]

    if not action_hint:
        action_hint = segment_text[:180]

    def _normalize_list(values: object, limit: int) -> list[str]:
        if isinstance(values, str):
            parts = re.split(r"[,，;；|/]", values)
        elif isinstance(values, list):
            parts = [str(item) for item in values]
        else:
            parts = []
        output: list[str] = []
        seen: set[str] = set()
        for raw in parts:
            item = _normalize_text(raw)[:80]
            if not item:
                continue
            lowered = item.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            output.append(item)
            if len(output) >= limit:
                break
        return output

    scene_elements = _normalize_list(descriptor.get("scene_elements") or descriptor.get("visual_elements") or [], 12)
    action_keywords = _normalize_list(descriptor.get("action_keywords") or [], 10)
    location_keywords = _normalize_list(descriptor.get("location_keywords") or [], 8)
    mood = _normalize_text(str(descriptor.get("mood", "")))[:80]
    shot_type = _normalize_text(str(descriptor.get("shot_type", "")))[:80]

    return {
        "character_name": character_name,
        "character_role": character_role,
        "reference_image_path": reference_image_path,
        "action_hint": action_hint,
        "location_hint": location_hint,
        "segment_text": segment_text,
        "scene_elements": scene_elements,
        "action_keywords": action_keywords,
        "location_keywords": location_keywords,
        "mood": mood,
        "shot_type": shot_type,
    }


def _character_key_from_descriptor(descriptor: dict) -> str:
    reference_image_path = _normalize_text(descriptor.get("reference_image_path", "")).lower()
    character_name = _normalize_text(descriptor.get("character_name", "")).lower()
    seed = reference_image_path or character_name
    if not seed:
        return ""
    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:16]


def _build_match_profile(descriptor: dict) -> dict:
    normalized = _normalize_scene_descriptor(descriptor)
    action_hint = normalized.get("action_hint", "")
    location_hint = normalized.get("location_hint", "")
    segment_text = normalized.get("segment_text", "")
    scene_elements = normalized.get("scene_elements") or []
    action_keywords = normalized.get("action_keywords") or []
    location_keywords = normalized.get("location_keywords") or []

    scene_summary = " | ".join([piece for piece in [location_hint, action_hint] if piece]) or segment_text[:220]
    return {
        "schema_version": 2,
        "character_key": _character_key_from_descriptor(normalized),
        "character_name": normalized.get("character_name", ""),
        "character_role": normalized.get("character_role", ""),
        "reference_image_path": normalized.get("reference_image_path", ""),
        "action_hint": action_hint,
        "location_hint": location_hint,
        "segment_text": segment_text,
        "scene_elements": scene_elements,
        "action_keywords": action_keywords,
        "location_keywords": location_keywords,
        "mood": normalized.get("mood", ""),
        "shot_type": normalized.get("shot_type", ""),
        "action_tokens": _tokenize_ordered(
            f"{action_hint} {' '.join(action_keywords)} {segment_text}",
            limit=24,
        ),
        "location_tokens": _tokenize_ordered(
            f"{location_hint} {' '.join(location_keywords)}",
            limit=16,
        ),
        "scene_tokens": _tokenize_ordered(
            f"{segment_text} {' '.join(scene_elements)} {normalized.get('mood', '')} {normalized.get('shot_type', '')}",
            limit=40,
        ),
        "scene_summary": scene_summary[:220],
    }


def _migrate_entry_schema(entry: dict) -> dict | None:
    image_path = _normalize_text(entry.get("image_path", ""))
    if not image_path:
        return None

    descriptor = _normalize_scene_descriptor(entry.get("descriptor") or {})
    if not any(descriptor.values()):
        prompt = _normalize_text(entry.get("prompt", ""))
        descriptor = _normalize_scene_descriptor({
            "action_hint": prompt[:220],
            "segment_text": prompt[:700],
        })

    raw_profile = entry.get("match_profile") if isinstance(entry.get("match_profile"), dict) else {}
    profile = _build_match_profile({**descriptor, **raw_profile})

    return {
        "id": str(entry.get("id") or uuid4().hex),
        "created_at": str(entry.get("created_at") or datetime.now(timezone.utc).isoformat()),
        "image_path": image_path,
        "prompt": profile.get("scene_summary", ""),
        "descriptor": descriptor,
        "match_profile": profile,
    }


def _character_match(target_profile: dict, candidate_profile: dict) -> bool:
    target_key = _normalize_text(target_profile.get("character_key", ""))
    candidate_key = _normalize_text(candidate_profile.get("character_key", ""))
    if target_key and candidate_key and target_key == candidate_key:
        return True

    target_ref = _normalize_text(target_profile.get("reference_image_path", "")).lower()
    candidate_ref = _normalize_text(candidate_profile.get("reference_image_path", "")).lower()
    if target_ref and candidate_ref and target_ref == candidate_ref:
        return True

    target_name = _normalize_text(target_profile.get("character_name", "")).lower()
    candidate_name = _normalize_text(candidate_profile.get("character_name", "")).lower()
    return bool(target_name and candidate_name and target_name == candidate_name)


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


def _entry_match_profile(entry: dict) -> dict:
    raw_profile = entry.get("match_profile")
    if isinstance(raw_profile, dict) and int(raw_profile.get("schema_version") or 0) >= 2:
        return raw_profile
    return _build_match_profile(entry.get("descriptor") or {})


def _text_match_verdict(target_profile: dict, candidate_profile: dict) -> tuple[bool, dict]:

    if not _character_match(target_profile, candidate_profile):
        return False, {"reject": "character_mismatch"}

    target_action_tokens = set(target_profile.get("action_tokens") or [])
    candidate_action_tokens = set(candidate_profile.get("action_tokens") or [])
    target_location_tokens = set(target_profile.get("location_tokens") or [])
    candidate_location_tokens = set(candidate_profile.get("location_tokens") or [])
    target_scene_tokens = set(target_profile.get("scene_tokens") or [])
    candidate_scene_tokens = set(candidate_profile.get("scene_tokens") or [])

    action_common = _common_token_count(target_action_tokens, candidate_action_tokens)
    location_common = _common_token_count(target_location_tokens, candidate_location_tokens)
    scene_common = _common_token_count(target_scene_tokens, candidate_scene_tokens)

    target_scene_elements = set(token.lower() for token in (target_profile.get("scene_elements") or []) if token)
    candidate_scene_elements = set(token.lower() for token in (candidate_profile.get("scene_elements") or []) if token)
    scene_element_common = _common_token_count(target_scene_elements, candidate_scene_elements)

    target_action = _normalize_text(target_profile.get("action_hint", ""))
    candidate_action = _normalize_text(candidate_profile.get("action_hint", ""))
    exact_action = bool(target_action and candidate_action and target_action == candidate_action)
    action_contains = bool(
        target_action
        and candidate_action
        and (
            (len(target_action) >= 8 and target_action in candidate_action)
            or (len(candidate_action) >= 8 and candidate_action in target_action)
        )
    )
    action_match = exact_action or action_contains or (action_common >= 2)

    target_location = _normalize_text(target_profile.get("location_hint", ""))
    candidate_location = _normalize_text(candidate_profile.get("location_hint", ""))
    exact_location = bool(target_location and candidate_location and target_location == candidate_location)
    location_contains = bool(
        target_location
        and candidate_location
        and (
            (len(target_location) >= 6 and target_location in candidate_location)
            or (len(candidate_location) >= 6 and candidate_location in target_location)
        )
    )
    require_location_match = bool(target_location and candidate_location)
    location_match = (not require_location_match) or exact_location or location_contains or (location_common >= 1)

    scene_match = scene_common >= 2 or scene_element_common >= 1 or action_match

    if not action_match:
        return False, {
            "reject": "action_mismatch",
            "action_common": action_common,
            "location_common": location_common,
            "scene_common": scene_common,
            "exact_action": exact_action,
        }

    if require_location_match and not location_match:
        return False, {
            "reject": "location_mismatch",
            "action_common": action_common,
            "location_common": location_common,
            "scene_common": scene_common,
            "exact_location": exact_location,
        }

    if not scene_match:
        return False, {
            "reject": "scene_mismatch",
            "action_common": action_common,
            "location_common": location_common,
            "scene_common": scene_common,
            "scene_element_common": scene_element_common,
        }

    rank_key = action_common * 100 + location_common * 10 + scene_common
    return True, {
        "character_match": True,
        "action_match": action_match,
        "location_match": location_match,
        "scene_match": scene_match,
        "action_common": action_common,
        "location_common": location_common,
        "scene_common": scene_common,
        "scene_element_common": scene_element_common,
        "exact_action": exact_action,
        "exact_location": exact_location,
        "rank_key": rank_key,
    }


def build_scene_descriptor(
    character: CharacterSuggestion,
    segment_text: str,
    prompt: str,
    metadata: dict | None = None,
) -> dict:
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

    base = {
        "character_name": _normalize_text(character.name),
        "character_role": _normalize_text(character.role),
        "reference_image_path": _normalize_text(character.reference_image_path or ""),
        "action_hint": _normalize_text(action_hint)[:220],
        "location_hint": _normalize_text(location_hint)[:220],
        "segment_text": sentence[:700],
        "scene_elements": [],
        "action_keywords": [],
        "location_keywords": [],
        "mood": "",
        "shot_type": "",
    }
    if metadata:
        merged = {
            **base,
            "action_hint": _normalize_text(str(metadata.get("action_hint") or base["action_hint"]))[:220],
            "location_hint": _normalize_text(str(metadata.get("location_hint") or base["location_hint"]))[:220],
            "scene_elements": metadata.get("scene_elements") or [],
            "action_keywords": metadata.get("action_keywords") or [],
            "location_keywords": metadata.get("location_keywords") or [],
            "mood": _normalize_text(str(metadata.get("mood", "")))[:80],
            "shot_type": _normalize_text(str(metadata.get("shot_type", "")))[:80],
        }
        return _normalize_scene_descriptor(merged)
    return _normalize_scene_descriptor(base)


async def _llm_match_candidate(
    target_profile: dict,
    candidates: list[dict],
    model_id: str | None,
) -> tuple[str | None, str]:
    if not settings.llm_api_key or not candidates:
        return None, "llm disabled or no candidates"

    url = f"{settings.llm_api_base_url.rstrip('/')}/chat/completions"
    model = model_id or settings.llm_default_model
    prompt = {
        "task": "select_reusable_scene_image",
        "rules": [
            "This decision is strict: if uncertain, return should_reuse=false.",
            "User experience first: avoid wrong reuse. Wrong reuse is worse than generating a new image.",
            "Only reuse at high match level.",
            "character_match must be true, otherwise reject.",
            "action_match must be true, otherwise reject.",
            "If both sides contain location hints, location_match must be true.",
            "If scene elements differ substantially, reject.",
            "Do not select by writing style; only compare character, action and location.",
            "Return strict JSON only.",
        ],
        "target": {
            "character_name": target_profile.get("character_name", ""),
            "character_role": target_profile.get("character_role", ""),
            "character_key": target_profile.get("character_key", ""),
            "action_hint": target_profile.get("action_hint", ""),
            "location_hint": target_profile.get("location_hint", ""),
            "segment_text": target_profile.get("segment_text", ""),
        },
        "candidates": [
            {
                "id": item["id"],
                "character_name": ((item.get("match_profile") or {}).get("character_name") or ""),
                "character_key": ((item.get("match_profile") or {}).get("character_key") or ""),
                "action_hint": ((item.get("match_profile") or {}).get("action_hint") or ""),
                "location_hint": ((item.get("match_profile") or {}).get("location_hint") or ""),
                "segment_text": ((item.get("match_profile") or {}).get("segment_text") or ""),
            }
            for item in candidates
        ],
        "output_schema": {
            "should_reuse": True,
            "selected_id": "candidate-id-or-null",
            "character_match": True,
            "action_match": True,
            "location_match": True,
            "scene_match": True,
            "reason": "short reason",
        },
    }
    candidate_location_map = {
        str(item.get("id")): _normalize_text(((item.get("match_profile") or {}).get("location_hint") or ""))
        for item in candidates
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
        return None, "llm request failed"

    parsed = _parse_json_object(content)
    if not parsed:
        return None, "llm response unparsable"

    selected_id = parsed.get("selected_id")
    should_reuse = bool(parsed.get("should_reuse", False))
    character_match = bool(parsed.get("character_match", False))
    action_match = bool(parsed.get("action_match", False))
    location_match = bool(parsed.get("location_match", False))
    scene_match = bool(parsed.get("scene_match", False))
    reason = str(parsed.get("reason", ""))[:240]
    if not should_reuse or not selected_id:
        return None, reason or "llm says no match"
    target_has_location = bool(_normalize_text(target_profile.get("location_hint", "")))
    selected_has_location = bool(candidate_location_map.get(str(selected_id), ""))
    require_location_match = target_has_location and selected_has_location
    if not character_match or not action_match or not scene_match or (require_location_match and not location_match):
        return None, reason or "llm strict checks failed"
    return str(selected_id), reason or "llm matched"


async def find_reusable_scene_image(
    scene_descriptor: dict,
    model_id: str | None = None,
    disallow_entry_ids: set[str] | None = None,
) -> dict | None:
    ensure_scene_cache_paths()

    target_descriptor = _normalize_scene_descriptor(scene_descriptor)
    blocked_ids = {str(item) for item in (disallow_entry_ids or set()) if str(item)}

    with _CACHE_LOCK:
        conn = _connect_db()
        try:
            _ensure_db_schema(conn)
            _migrate_index_to_db_if_needed(conn)
            entries = _load_entries_from_db(conn)
        finally:
            conn.close()

    target_ref_path = _normalize_text(target_profile.get("reference_image_path", ""))
    if target_ref_path:
        same_ref: list[dict] = []
        for entry in entries:
            profile = _entry_match_profile(entry)
            ref = _normalize_text(profile.get("reference_image_path", ""))
            if ref and ref == target_ref_path:
                same_ref.append(entry)
        if same_ref:
            entries = same_ref

    target_profile = _build_match_profile(target_descriptor)
    viable: list[dict] = []
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if entry_id and entry_id in blocked_ids:
            continue

        migrated = _migrate_entry_schema(entry)
        if not migrated:
            continue

        image_path = Path(migrated.get("image_path") or "")
        if not image_path.exists():
            continue
        candidate_profile = _entry_match_profile(migrated)
        matched, details = _text_match_verdict(target_profile, candidate_profile)
        if not matched:
            continue
        rank_key = int(details.get("rank_key", 0))
        enriched = {
            **migrated,
            "match_profile": candidate_profile,
            "heuristic_score": float(rank_key),
            "score_details": details,
        }
        viable.append(enriched)

    if not viable:
        return None

    ranked = sorted(
        viable,
        key=lambda item: int((item.get("score_details") or {}).get("rank_key", 0)),
        reverse=True,
    )
    top = ranked[:5]

    exact_candidates: list[dict] = []
    for item in top:
        item_profile = _entry_match_profile(item)
        same_action = _normalize_text(target_profile.get("action_hint", "")) and _normalize_text(
            target_profile.get("action_hint", "")
        ) == _normalize_text(item_profile.get("action_hint", ""))
        target_location = _normalize_text(target_profile.get("location_hint", ""))
        item_location = _normalize_text(item_profile.get("location_hint", ""))
        same_location = (not target_location and not item_location) or (
            target_location and item_location and target_location == item_location
        )
        if same_action and same_location:
            exact_candidates.append(item)

    if exact_candidates:
        best_exact = sorted(
            exact_candidates,
            key=lambda item: int((item.get("score_details") or {}).get("rank_key", 0)),
            reverse=True,
        )[0]
        return {
            "image_path": str(best_exact["image_path"]),
            "match_type": "text-exact",
            "confidence": 1.0,
            "reason": "exact action/location match",
            "entry_id": best_exact.get("id"),
        }

    if len(top) == 1:
        only = top[0]
        only_profile = _entry_match_profile(only)
        same_action = _normalize_text(target_profile.get("action_hint", "")) and _normalize_text(
            target_profile.get("action_hint", "")
        ) == _normalize_text(only_profile.get("action_hint", ""))
        target_location = _normalize_text(target_profile.get("location_hint", ""))
        only_location = _normalize_text(only_profile.get("location_hint", ""))
        same_location = (not target_location and not only_location) or (
            target_location and only_location and target_location == only_location
        )
        if same_action and same_location:
            return {
                "image_path": str(only["image_path"]),
                "match_type": "text-exact",
                "confidence": 1.0,
                "reason": "single candidate exact action/location",
                "entry_id": only.get("id"),
            }

    best_precheck = top[0].get("score_details") or {}
    if not bool(best_precheck.get("exact_action", False)) and int(best_precheck.get("action_common", 0)) < 3:
        return None
    if int(best_precheck.get("scene_common", 0)) < 2 and int(best_precheck.get("scene_element_common", 0)) < 1:
        return None

    selected_id, reason = await _llm_match_candidate(target_profile, top, model_id=model_id)
    if selected_id:
        selected = next((item for item in top if str(item.get("id")) == selected_id), None)
        if selected:
            return {
                "image_path": str(selected["image_path"]),
                "match_type": "llm",
                "confidence": 0.9,
                "reason": reason,
                "entry_id": selected.get("id"),
            }

    best = top[0]
    best_details = best.get("score_details") or {}
    if best_details.get("reject") in {"character_mismatch", "action_mismatch", "location_mismatch"}:
        return None

    # conservative text-only fallback: only when action/location text are exactly same
    best_profile = _entry_match_profile(best)
    same_action = _normalize_text(target_profile.get("action_hint", "")) and _normalize_text(
        target_profile.get("action_hint", "")
    ) == _normalize_text(best_profile.get("action_hint", ""))
    target_location = _normalize_text(target_profile.get("location_hint", ""))
    best_location = _normalize_text(best_profile.get("location_hint", ""))
    same_location = (not target_location and not best_location) or (
        target_location and best_location and target_location == best_location
    )
    if same_action and same_location:
        return {
            "image_path": str(best["image_path"]),
            "match_type": "text-exact",
            "confidence": 1.0,
            "reason": "exact action/location text match",
            "entry_id": best.get("id"),
        }

    return None


async def force_llm_select_scene_image(
    scene_descriptor: dict,
    model_id: str | None = None,
    disallow_entry_ids: set[str] | None = None,
) -> dict | None:
    ensure_scene_cache_paths()

    target_descriptor = _normalize_scene_descriptor(scene_descriptor)
    target_profile = _build_match_profile(target_descriptor)
    blocked_ids = {str(item) for item in (disallow_entry_ids or set()) if str(item)}

    with _CACHE_LOCK:
        conn = _connect_db()
        try:
            _ensure_db_schema(conn)
            _migrate_index_to_db_if_needed(conn)
            entries = _load_entries_from_db(conn)
        finally:
            conn.close()

    target_ref_path = _normalize_text(target_profile.get("reference_image_path", ""))
    if target_ref_path:
        same_ref: list[dict] = []
        for entry in entries:
            profile = _entry_match_profile(entry)
            ref = _normalize_text(profile.get("reference_image_path", ""))
            if ref and ref == target_ref_path:
                same_ref.append(entry)
        if same_ref:
            entries = same_ref

    candidates: list[dict] = []
    target_action_tokens = set(target_profile.get("action_tokens") or [])
    target_location_tokens = set(target_profile.get("location_tokens") or [])
    target_scene_tokens = set(target_profile.get("scene_tokens") or [])

    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if entry_id and entry_id in blocked_ids:
            continue

        migrated = _migrate_entry_schema(entry)
        if not migrated:
            continue

        image_path = Path(migrated.get("image_path") or "")
        if not image_path.exists():
            continue

        candidate_profile = _entry_match_profile(migrated)
        action_common = _common_token_count(target_action_tokens, set(candidate_profile.get("action_tokens") or []))
        location_common = _common_token_count(target_location_tokens, set(candidate_profile.get("location_tokens") or []))
        scene_common = _common_token_count(target_scene_tokens, set(candidate_profile.get("scene_tokens") or []))
        character_bonus = 1000 if _character_match(target_profile, candidate_profile) else 0
        llm_rank = character_bonus + action_common * 100 + location_common * 20 + scene_common

        candidates.append(
            {
                **migrated,
                "match_profile": candidate_profile,
                "llm_rank": llm_rank,
            }
        )

    if not candidates:
        return None

    top = sorted(candidates, key=lambda item: int(item.get("llm_rank", 0)), reverse=True)[:20]
    selected_id, reason = await _llm_match_candidate(target_profile, top, model_id=model_id)
    if not selected_id:
        return None

    selected = next((item for item in top if str(item.get("id")) == selected_id), None)
    if not selected:
        return None

    return {
        "image_path": str(selected["image_path"]),
        "match_type": "llm-fallback",
        "confidence": 0.75,
        "reason": reason,
        "entry_id": selected.get("id"),
    }


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

    normalized_descriptor = _normalize_scene_descriptor(scene_descriptor)
    match_profile = _build_match_profile(normalized_descriptor)
    entry = {
        "id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "image_path": target.as_posix(),
        "prompt": _normalize_text(match_profile.get("scene_summary", "") or prompt)[:220],
        "descriptor": normalized_descriptor,
        "match_profile": match_profile,
    }

    with _CACHE_LOCK:
        conn = _connect_db()
        try:
            _ensure_db_schema(conn)
            _insert_entry_to_db(conn, entry)
            _prune_db_entries(conn, keep=3000)
            conn.commit()
        finally:
            conn.close()

    # keep json index in sync for backward compatibility / manual inspection
    try:
        with _CACHE_LOCK:
            payload = _load_index_unlocked()
            entries = [item for item in payload.get("entries", []) if isinstance(item, dict)]
            entries.append(entry)
            payload["schema_version"] = 2
            payload["entries"] = entries[-3000:]
            _save_index_unlocked(payload)
    except Exception:
        logger.exception("Failed to mirror scene cache entry into index.json")
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
    image.save(dst)
    return dst


def list_scene_cache_entries(
    disallow_entry_ids: set[str] | None = None,
) -> list[dict]:
    ensure_scene_cache_paths()
    blocked_ids = {str(item) for item in (disallow_entry_ids or set()) if str(item)}

    with _CACHE_LOCK:
        conn = _connect_db()
        try:
            _ensure_db_schema(conn)
            _migrate_index_to_db_if_needed(conn)
            entries = _load_entries_from_db(conn)
        finally:
            conn.close()

    valid: list[dict] = []
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if entry_id and entry_id in blocked_ids:
            continue

        migrated = _migrate_entry_schema(entry)
        if not migrated:
            continue

        image_path = Path(migrated.get("image_path") or "")
        if not image_path.exists():
            continue

        valid.append(migrated)

    return valid
