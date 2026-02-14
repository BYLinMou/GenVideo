from __future__ import annotations

import json
import logging
import re

import httpx

from ..config import settings
from ..models import CharacterSuggestion
from ..voice_catalog import VOICE_INFOS, recommend_voice
from .prompt_templates import (
    SEGMENT_IMAGE_BUNDLE_RULES,
    STRICT_JSON_SYSTEM_PROMPT,
    build_alias_prompt,
    build_character_analysis_prompt,
    build_character_identity_guard,
    build_fallback_segment_image_prompt,
    build_final_segment_image_prompt,
    build_story_world_summary_prompt,
    build_smart_segmentation_prompt,
)


logger = logging.getLogger(__name__)

_VOICE_ID_SET = {item.id for item in VOICE_INFOS}
_VOICE_NAME_TO_ID = {item.name.lower(): item.id for item in VOICE_INFOS}


class LLMServiceError(RuntimeError):
    pass


def _base_url(path: str) -> str:
    return f"{settings.llm_api_base_url.rstrip('/')}{path}"


def _response_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                for key in ("message", "detail", "type"):
                    value = error.get(key)
                    if value:
                        return str(value)
            if error:
                return str(error)
            for key in ("detail", "message"):
                value = payload.get(key)
                if value:
                    return str(value)
    except Exception:
        pass

    text = (response.text or "").strip()
    return text[:300] if text else "unknown upstream error"


async def probe_openai_models() -> list[str]:
    if not settings.llm_api_key:
        return []

    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(_base_url("/models"), headers=headers)
            response.raise_for_status()
            payload = response.json()
            return sorted([item["id"] for item in payload.get("data", []) if item.get("id")])
    except Exception:
        logger.exception("Failed to probe models")
        return []


def _extract_json_object(text: str) -> dict | None:
    content = text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        content = re.sub(r"```$", "", content).strip()
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_segmentation_text(text: str) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    heading_pattern = re.compile(r"^\s*#\s*\d+\s*[\uFF08(]\s*\d+\s*\u53E5\s*[\uFF09)]\s*$")

    kept_lines: list[str] = []
    for line in raw.split("\n"):
        normalized = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if not normalized:
            continue
        if heading_pattern.match(normalized):
            continue
        kept_lines.append(normalized)

    merged = " ".join(kept_lines)
    merged = re.sub(r"[ \t\f\v]+", " ", merged)
    return merged.strip()
    raw = re.sub(r"^\s*#\s*\d+\s*[（(]\s*\d+\s*句\s*[）)]\s*$", "", raw, flags=re.MULTILINE)
    raw = raw.replace("\n", " ")
    raw = re.sub(r"[ \t\f\v]+", " ", raw)
    return raw.strip()


def split_sentences(text: str) -> list[str]:
    clean = _normalize_segmentation_text(text)
    if not clean:
        return []

    return _split_sentences_v2(clean)

    sentence_delimiters = {
        "\u3002",  # full stop
        "\uff01",  # exclamation
        "\uff1f",  # question
        "\uff1b",  # semicolon
        ".",
        "!",
        "?",
        ";",
    }
    closing_marks = {"】", "]", "）", ")", "」", "』", "”", '"', "’", "'"}

    closing_marks = {
        "\u3011",
        "]",
        "\uFF09",
        ")",
        "\u300D",
        "\u300F",
        "\u201D",
        '"',
        "\u2019",
        "'",
    }

    sentences: list[str] = []
    buffer: list[str] = []
    length = len(clean)

    def _flush() -> None:
        candidate = "".join(buffer).strip()
        if candidate:
            sentences.append(candidate)
        buffer.clear()

    index = 0
    while index < length:
        char = clean[index]

        buffer.append(char)

        if char not in sentence_delimiters:
            index += 1
            continue

        prev_char = clean[index - 1] if index - 1 >= 0 else ""
        next_char = clean[index + 1] if index + 1 < length else ""

        if next_char in sentence_delimiters:
            index += 1
            continue

        if char == "." and prev_char.isdigit() and next_char.isdigit():
            index += 1
            continue

        if char == "?" and prev_char == "?":
            index += 1
            continue

        tail_index = index + 1
        while tail_index < length and clean[tail_index] in closing_marks:
            buffer.append(clean[tail_index])
            tail_index += 1

        _flush()
        index = tail_index

    _flush()
    return sentences


def _split_sentences_v2(clean: str) -> list[str]:
    sentence_delimiters = {
        "\u3002",  # 。
        "\uff01",  # ！
        "\uff1f",  # ？
        "\uff1b",  # ；
        "\uff0c",  # ，
        ".",
        "!",
        "?",
        ";",
        ",",
    }
    opening_marks = {
        "\u3010",  # 【
        "\u300c",  # 「
        "\u300e",  # 『
        "\u201c",  # “
        "\u2018",  # ‘
        "\uFF08",  # （
        "(",
        "[",
        "{",
        '"',
        "'",
    }
    closing_marks = {
        "\u3011",  # 】
        "]",
        "\uFF09",  # ）
        ")",
        "\u300D",  # 」
        "\u300F",  # 』
        "\u201D",  # ”
        "\u2019",  # ’
        "}",
        '"',
        "'",
    }

    sentences: list[str] = []
    buffer: list[str] = []
    length = len(clean)

    def _flush() -> None:
        candidate = "".join(buffer).strip()
        if candidate:
            sentences.append(candidate)
        buffer.clear()

    index = 0
    while index < length:
        char = clean[index]
        buffer.append(char)

        if char not in sentence_delimiters:
            index += 1
            continue

        prev_char = clean[index - 1] if index - 1 >= 0 else ""
        next_char = clean[index + 1] if index + 1 < length else ""

        if next_char in sentence_delimiters:
            index += 1
            continue

        if next_char in opening_marks:
            index += 1
            continue

        if char == "." and prev_char.isdigit() and next_char.isdigit():
            index += 1
            continue

        if char == "?" and prev_char == "?":
            index += 1
            continue

        tail_index = index + 1
        while tail_index < length and clean[tail_index] in closing_marks:
            buffer.append(clean[tail_index])
            tail_index += 1

        _flush()
        index = tail_index

    _flush()
    return sentences


def group_sentences(sentences: list[str], sentences_per_segment: int) -> list[str]:
    count = max(1, sentences_per_segment)
    grouped: list[str] = []
    for start in range(0, len(sentences), count):
        grouped.append("".join(sentences[start : start + count]))
    return grouped


def segment_by_sentence_groups(text: str, sentences_per_segment: int) -> list[str]:
    sentences = split_sentences(text)
    if not sentences:
        return []
    return group_sentences(sentences, sentences_per_segment)


def segment_by_fixed(text: str, chunk_size: int = 120) -> list[str]:
    clean = _normalize_segmentation_text(text)
    if not clean:
        return []
    return [clean[index : index + chunk_size] for index in range(0, len(clean), chunk_size)]


async def segment_by_smart(text: str, model_id: str | None) -> list[str]:
    clean_text = _normalize_segmentation_text(text)
    selected_model = model_id or settings.llm_default_model
    if not settings.llm_api_key:
        return segment_by_sentence_groups(clean_text, sentences_per_segment=5)

    prompt = build_smart_segmentation_prompt(clean_text)
    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": STRICT_JSON_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(_base_url("/chat/completions"), headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = _extract_json_object(content)
            if parsed and isinstance(parsed.get("segments"), list):
                segments = [str(item).strip() for item in parsed["segments"] if str(item).strip()]
                if segments:
                    return segments
    except Exception:
        logger.exception("Smart segmentation failed, fallback to sentence groups")

    return segment_by_sentence_groups(clean_text, sentences_per_segment=5)


def _clean_text(value: str | None, limit: int) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()[:limit]


def _normalize_index(value: object, size: int) -> int | None:
    if size <= 0:
        return None
    try:
        index = int(value)
    except Exception:
        return None
    if 0 <= index < size:
        return index
    return None


def _normalize_index_list(values: object, size: int, limit: int = 4) -> list[int]:
    if size <= 0:
        return []
    raw_items: list[object]
    if isinstance(values, list):
        raw_items = values
    elif values is None:
        raw_items = []
    else:
        raw_items = [values]

    output: list[int] = []
    seen: set[int] = set()
    for raw in raw_items:
        index = _normalize_index(raw, size)
        if index is None or index in seen:
            continue
        seen.add(index)
        output.append(index)
        if len(output) >= max(1, int(limit)):
            break
    return output


def _normalize_keyword_list(values: object, limit: int) -> list[str]:
    if isinstance(values, str):
        parts = re.split(r"[,，;；|/]", values)
    elif isinstance(values, list):
        parts = [str(item) for item in values]
    else:
        parts = []

    output: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        item = _clean_text(str(raw), 80)
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


def _normalize_scene_metadata(raw: dict | None) -> dict:
    payload = raw or {}
    action_hint = _clean_text(str(payload.get("action_hint", "")), 220)
    location_hint = _clean_text(str(payload.get("location_hint", "")), 220)
    scene_elements = _normalize_keyword_list(payload.get("scene_elements") or payload.get("visual_elements") or [], 10)
    action_keywords = _normalize_keyword_list(payload.get("action_keywords") or [], 10)
    location_keywords = _normalize_keyword_list(payload.get("location_keywords") or [], 8)
    mood = _clean_text(str(payload.get("mood", "")), 80)
    shot_type = _clean_text(str(payload.get("shot_type", "")), 80)

    return {
        "action_hint": action_hint,
        "location_hint": location_hint,
        "scene_elements": scene_elements,
        "action_keywords": action_keywords,
        "location_keywords": location_keywords,
        "mood": mood,
        "shot_type": shot_type,
    }


def _fallback_scene_metadata(segment_text: str, image_prompt: str) -> dict:
    text = _clean_text(segment_text, 1800)
    prompt_text = _clean_text(image_prompt, 1800)
    source = f"{text} {prompt_text}".strip()
    pieces = [piece.strip() for piece in re.split(r"[。！？；，,!?;]", source) if piece.strip()]
    action_hint = pieces[0] if pieces else source[:220]

    location_hint = ""
    location_markers = ["在", "于", "到", "来到", "进入", "教室", "街", "学校", "公园", "森林", "办公室", "家", "医院", "车站"]
    for part in pieces[1:]:
        if any(marker in part for marker in location_markers):
            location_hint = part
            break

    keyword_source = _clean_text(source, 2400)
    raw_tokens = re.split(r"[^\w\u4e00-\u9fff]+", keyword_source)
    scene_elements: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        token = _clean_text(token, 40)
        if len(token) < 2:
            continue
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        scene_elements.append(token)
        if len(scene_elements) >= 8:
            break

    return _normalize_scene_metadata(
        {
            "action_hint": action_hint,
            "location_hint": location_hint,
            "scene_elements": scene_elements,
            "action_keywords": scene_elements[:6],
            "location_keywords": [location_hint] if location_hint else [],
            "mood": "",
            "shot_type": "",
        }
    )


def _character_identity_guard(character: CharacterSuggestion) -> str:
    name = _clean_text(character.name, 80) or "main character"
    appearance = _clean_text(character.appearance, 500)
    base_prompt = _clean_text(character.base_prompt, 500)
    personality = _clean_text(character.personality, 240)
    has_reference = bool(character.reference_image_path)

    anchor_parts = [part for part in [appearance, base_prompt] if part]
    if not anchor_parts:
        anchor_parts = [name]
    anchors = "; ".join(anchor_parts)
    return build_character_identity_guard(
        name=name,
        anchors=anchors,
        personality=personality,
        has_reference=has_reference,
    )


def _fallback_segment_image_prompt(
    character: CharacterSuggestion,
    segment_text: str,
    story_world_context: str | None = None,
    previous_segment_text: str = "",
    next_segment_text: str = "",
) -> str:
    guard = _character_identity_guard(character)
    current_segment = _clean_text(segment_text, 1200)
    previous_segment = _clean_text(previous_segment_text, 420)
    next_segment = _clean_text(next_segment_text, 420)
    if previous_segment or next_segment:
        context_parts: list[str] = [f"Current segment: {current_segment}"]
        if previous_segment:
            context_parts.append(f"Previous segment context: {previous_segment}")
        if next_segment:
            context_parts.append(f"Next segment context: {next_segment}")
        scene_text = _clean_text("\n".join(context_parts), 1900)
    else:
        scene_text = current_segment
    return build_fallback_segment_image_prompt(
        guard=guard,
        scene_text=scene_text,
        story_world_context=_clean_text(story_world_context, 320),
    )


def _fallback_segment_image_bundle(
    character: CharacterSuggestion,
    segment_text: str,
    story_world_context: str | None = None,
    previous_segment_text: str = "",
    next_segment_text: str = "",
    default_primary_index: int | None = None,
    default_related_indexes: list[int] | None = None,
) -> dict:
    prompt = _fallback_segment_image_prompt(
        character,
        segment_text,
        story_world_context=story_world_context,
        previous_segment_text=previous_segment_text,
        next_segment_text=next_segment_text,
    )
    return {
        "prompt": prompt,
        "metadata": _fallback_scene_metadata(segment_text, prompt),
        "character_assignment": {
            "primary_index": default_primary_index,
            "related_indexes": [int(item) for item in (default_related_indexes or []) if isinstance(item, int)][:3],
            "confidence": 0.0,
            "reason": "fallback",
        },
    }


async def summarize_story_world_context(text: str, model_id: str | None) -> str:
    clean_text = _clean_text(text, 14000)
    if not clean_text or not settings.llm_api_key:
        return ""

    selected_model = model_id or settings.llm_default_model
    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": STRICT_JSON_SYSTEM_PROMPT},
            {"role": "user", "content": build_story_world_summary_prompt(clean_text)},
        ],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(_base_url("/chat/completions"), headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = _extract_json_object(content) or {}
            summary = _clean_text(str(parsed.get("world_summary", "")), 320)
            return summary
    except Exception:
        logger.exception("Story world context summarization failed")
        return ""


async def build_segment_image_bundle(
    character: CharacterSuggestion,
    segment_text: str,
    model_id: str | None,
    related_reference_image_paths: list[str] | None = None,
    story_world_context: str | None = None,
    previous_segment_text: str = "",
    next_segment_text: str = "",
    character_candidates: list[CharacterSuggestion] | None = None,
    default_primary_index: int | None = None,
    default_related_indexes: list[int] | None = None,
) -> dict:
    candidates = list(character_candidates or [])
    if not candidates:
        candidates = [character]

    safe_default_primary = _normalize_index(default_primary_index, len(candidates))
    safe_default_related = _normalize_index_list(default_related_indexes, len(candidates), limit=3)
    if safe_default_primary is None and safe_default_related:
        safe_default_primary = safe_default_related[0]
    if safe_default_primary is None:
        safe_default_primary = 0 if candidates else None
    if safe_default_primary is not None and safe_default_primary not in safe_default_related:
        safe_default_related.insert(0, safe_default_primary)

    fallback_bundle = _fallback_segment_image_bundle(
        character,
        segment_text,
        story_world_context=story_world_context,
        previous_segment_text=previous_segment_text,
        next_segment_text=next_segment_text,
        default_primary_index=safe_default_primary,
        default_related_indexes=safe_default_related,
    )
    guard = _character_identity_guard(character)
    scene_text = _clean_text(segment_text, 1200)
    world_context = _clean_text(story_world_context, 320)
    if not settings.llm_api_key:
        return fallback_bundle

    selected_model = model_id or settings.llm_default_model
    request_body = {
        "task": "build_image_prompt_for_story_segment",
        "rules": list(SEGMENT_IMAGE_BUNDLE_RULES),
        "character": {
            "name": _clean_text(character.name, 120),
            "appearance": _clean_text(character.appearance, 800),
            "personality": _clean_text(character.personality, 400),
            "base_prompt": _clean_text(character.base_prompt, 800),
            "has_reference_image": bool(character.reference_image_path),
            "related_reference_image_paths": [str(item) for item in (related_reference_image_paths or []) if str(item).strip()][:2],
        },
        "story_world_context": world_context,
        "current_segment": _clean_text(segment_text, 1800),
        "adjacent_context": {
            "previous_segment": _clean_text(previous_segment_text, 500),
            "next_segment": _clean_text(next_segment_text, 500),
        },
        "character_candidates": [
            {
                "index": index,
                "name": _clean_text(item.name, 80),
                "role": _clean_text(item.role, 80),
                "importance": max(1, min(10, int(item.importance or 5))),
                "is_main_character": bool(item.is_main_character),
                "is_story_self": bool(item.is_story_self),
                "has_reference_image": bool(item.reference_image_path),
            }
            for index, item in enumerate(candidates)
        ],
        "default_character_assignment": {
            "primary_index": safe_default_primary,
            "related_indexes": safe_default_related,
        },
        "output_schema": {
            "primary_index": 0,
            "related_indexes": [0],
            "character_confidence": 0.0,
            "character_reason": "",
            "prompt": "",
            "action_hint": "",
            "location_hint": "",
            "scene_elements": [""],
            "action_keywords": [""],
            "location_keywords": [""],
            "mood": "",
            "shot_type": "",
        },
    }
    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": STRICT_JSON_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(request_body, ensure_ascii=False)},
        ],
        "temperature": 0.15,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(_base_url("/chat/completions"), headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = _extract_json_object(content)
            candidate = _clean_text(str((parsed or {}).get("prompt", "")), 2200)
            if candidate:
                resolved_primary = _normalize_index((parsed or {}).get("primary_index"), len(candidates))
                resolved_related = _normalize_index_list((parsed or {}).get("related_indexes"), len(candidates), limit=3)
                if resolved_primary is None and resolved_related:
                    resolved_primary = resolved_related[0]
                if resolved_primary is None:
                    resolved_primary = safe_default_primary
                if resolved_primary is not None and resolved_primary not in resolved_related:
                    resolved_related.insert(0, resolved_primary)

                prompt_character = candidates[resolved_primary] if resolved_primary is not None and 0 <= resolved_primary < len(candidates) else character
                guard = _character_identity_guard(prompt_character)

                final_prompt = build_final_segment_image_prompt(
                    guard=guard,
                    scene_text=scene_text,
                    candidate=candidate,
                    story_world_context=world_context,
                )
                metadata = _normalize_scene_metadata(parsed)
                if not metadata.get("action_hint"):
                    metadata["action_hint"] = _fallback_scene_metadata(segment_text, final_prompt).get("action_hint", "")
                if not metadata.get("location_hint"):
                    metadata["location_hint"] = _fallback_scene_metadata(segment_text, final_prompt).get("location_hint", "")
                return {
                    "prompt": final_prompt,
                    "metadata": metadata,
                    "character_assignment": {
                        "primary_index": resolved_primary,
                        "related_indexes": resolved_related,
                        "confidence": max(0.0, min(1.0, float((parsed or {}).get("character_confidence", 0.0) or 0.0))),
                        "reason": _clean_text(str((parsed or {}).get("character_reason", "")), 240),
                    },
                }
    except Exception:
        logger.exception("LLM image prompt/metadata build failed, using fallback bundle")

    return fallback_bundle


async def build_segment_image_prompt(
    character: CharacterSuggestion,
    segment_text: str,
    model_id: str | None,
    previous_segment_text: str = "",
    next_segment_text: str = "",
) -> str:
    bundle = await build_segment_image_bundle(
        character=character,
        segment_text=segment_text,
        model_id=model_id,
        previous_segment_text=previous_segment_text,
        next_segment_text=next_segment_text,
    )
    return _clean_text(str(bundle.get("prompt", "")), 2600) or _fallback_segment_image_prompt(
        character,
        segment_text,
        previous_segment_text=previous_segment_text,
        next_segment_text=next_segment_text,
    )


def _fallback_character_analysis(text: str) -> list[CharacterSuggestion]:
    names = re.findall(r"[\u4e00-\u9fff]{2,3}", re.sub(r"\s+", " ", text))
    ignored = {
        "\u5c0f\u8bf4",  # novel
        "\u6545\u4e8b",  # story
        "\u4eca\u5929",  # today
        "\u8fd9\u4e2a",  # this
        "\u4e00\u4e2a",  # one
        "\u81ea\u5df1",  # self
        "\u6211\u4eec",  # we
    }

    ranked: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen or name in ignored:
            continue
        seen.add(name)
        ranked.append(name)
        if len(ranked) >= 5:
            break

    if not ranked:
        ranked = ["Narrator"]

    output: list[CharacterSuggestion] = []
    for index, name in enumerate(ranked):
        role = "protagonist" if index == 0 else "supporting"
        personality = "calm, decisive" if index == 0 else "kind, friendly"
        output.append(
            CharacterSuggestion(
                name=name,
                role=role,
                importance=max(10 - index, 5),
                appearance="appearance to be completed",
                personality=personality,
                voice_id=recommend_voice(role, personality),
                base_prompt=f"{name}, {personality}, novel character illustration",
            )
        )
    return output


def _character_prompt(text: str, depth: str, story_world_context: str | None = None) -> str:
    voice_lines = "\n".join(
        f"- {voice.id} | {voice.name} | {voice.gender}/{voice.age} | {voice.description}" for voice in VOICE_INFOS
    )
    allowed_ids = ", ".join(sorted(_VOICE_ID_SET))
    return build_character_analysis_prompt(
        text=text,
        depth=depth,
        allowed_ids=allowed_ids,
        voice_lines=voice_lines,
        story_world_context=_clean_text(story_world_context, 320),
    )


_ALIAS_STOPWORDS = {
    "天下",
    "江湖",
    "苍生",
    "王朝",
    "帝国",
    "都市",
    "校园",
    "重生",
    "逆袭",
    "传奇",
    "神话",
    "风云",
    "山河",
    "春秋",
    "长安",
    "洛阳",
    "金陵",
    "燕京",
    "姑苏",
    "巴蜀",
    "中原",
}


def _sanitize_alias(value: str) -> str:
    raw = re.sub(r"\s+", "", str(value or ""))
    cleaned = "".join(ch for ch in raw if "\u4e00" <= ch <= "\u9fa5")
    return cleaned


def _is_alias_valid(alias: str) -> bool:
    if not alias:
        return False
    if not (4 <= len(alias) <= 8):
        return False
    if any(token in alias for token in _ALIAS_STOPWORDS):
        return False
    if not re.fullmatch(r"[\u4e00-\u9fa5]{4,8}", alias):
        return False
    return True


def _fallback_aliases(text: str, count: int) -> list[str]:
    seed = _sanitize_alias((text or "")[:24])
    base = seed[:4] if len(seed) >= 4 else "此间风骨"
    pool = [
        f"{base}微光",
        f"{base}旧梦",
        f"{base}余烬",
        f"{base}暗潮",
        f"{base}长夜",
        f"{base}孤舟",
        f"{base}星霜",
        f"{base}归途",
        f"{base}残照",
        f"{base}回响",
        "雾中焰心",
        "夜潮归人",
        "风痕未冷",
        "烬海拾光",
        "雪骨沉灯",
    ]
    output: list[str] = []
    seen: set[str] = set()
    for item in pool:
        candidate = _sanitize_alias(item)
        if not _is_alias_valid(candidate) or candidate in seen:
            continue
        seen.add(candidate)
        output.append(candidate)
        if len(output) >= count:
            break
    return output


def _alias_prompt(text: str, count: int) -> str:
    return build_alias_prompt(text=text, count=count)


async def generate_novel_aliases(text: str, count: int, model_id: str | None) -> tuple[list[str], str]:
    selected_model = model_id or settings.llm_default_model
    wanted = max(1, min(20, int(count or 10)))

    def _dedupe(items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            cleaned = _sanitize_alias(item)
            if not _is_alias_valid(cleaned):
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            result.append(cleaned)
            if len(result) >= wanted:
                break
        return result

    if not settings.llm_api_key:
        raise LLMServiceError("LLM API key is missing")

    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": STRICT_JSON_SYSTEM_PROMPT},
            {"role": "user", "content": _alias_prompt(text, wanted)},
        ],
        "temperature": 0.85,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(_base_url("/chat/completions"), headers=headers, json=payload)
            if response.status_code >= 400:
                detail = _response_error_message(response)
                raise LLMServiceError(f"LLM alias generation failed ({response.status_code}): {detail}")
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = _extract_json_object(content) or {}
            aliases = parsed.get("aliases") if isinstance(parsed, dict) else []
            if not isinstance(aliases, list):
                aliases = []

            normalized = _dedupe([str(item) for item in aliases])
            if len(normalized) < wanted:
                raise LLMServiceError(
                    f"LLM alias generation returned insufficient valid aliases ({len(normalized)}/{wanted})"
                )
            return normalized[:wanted], selected_model
    except LLMServiceError:
        raise
    except Exception as exc:
        logger.exception("Generate novel aliases failed")
        raise LLMServiceError(f"LLM alias generation failed: {exc}") from exc


def _normalize_voice_id(raw_voice: str | None, role: str, personality: str) -> str:
    candidate = (raw_voice or "").strip()
    if candidate in _VOICE_ID_SET:
        return candidate

    lowered = candidate.lower()
    if lowered and lowered in _VOICE_NAME_TO_ID:
        return _VOICE_NAME_TO_ID[lowered]

    if lowered:
        for voice_id in _VOICE_ID_SET:
            if voice_id.lower() in lowered:
                return voice_id

    recommended = recommend_voice(role, personality)
    if recommended in _VOICE_ID_SET:
        return recommended

    return VOICE_INFOS[0].id


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text in {"1", "true", "yes", "y", "on", "是"}


def _normalize_identity_flags(characters: list[CharacterSuggestion], source_text: str = "") -> list[CharacterSuggestion]:
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
        main_indexes = [best_index]

    has_first_person = any(token in str(source_text or "") for token in ("我", "我们", "咱", "咱们", "I ", " I", "I'm", "I,"))
    if has_first_person and not self_indexes:
        characters[main_indexes[0]].is_story_self = True

    return characters


async def analyze_characters(text: str, depth: str, model_id: str | None) -> tuple[list[CharacterSuggestion], float, str]:
    selected_model = model_id or settings.llm_default_model
    if not settings.llm_api_key:
        raise LLMServiceError("LLM API key is missing")

    story_world_context = await summarize_story_world_context(text, model_id)
    if story_world_context:
        logger.info("Character analysis story world context: %s", story_world_context)

    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": STRICT_JSON_SYSTEM_PROMPT},
            {"role": "user", "content": _character_prompt(text, depth, story_world_context=story_world_context)},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(_base_url("/chat/completions"), headers=headers, json=payload)
            if response.status_code >= 400:
                detail = _response_error_message(response)
                raise LLMServiceError(f"LLM character analysis failed ({response.status_code}): {detail}")
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = _extract_json_object(content)
            if not parsed:
                raise LLMServiceError("LLM returned unparseable JSON")

            raw_items = parsed.get("characters") or []
            confidence = float(parsed.get("confidence", 0.75))

            characters: list[CharacterSuggestion] = []
            for item in raw_items:
                role = str(item.get("role", "supporting"))
                personality = str(item.get("personality", ""))
                voice_id = _normalize_voice_id(item.get("voice_id"), role, personality)
                characters.append(
                    CharacterSuggestion(
                        name=str(item.get("name", "character")),
                        role=role,
                        importance=max(1, min(10, int(item.get("importance", 5)))),
                        is_main_character=_as_bool(item.get("is_main_character")),
                        is_story_self=_as_bool(item.get("is_story_self")),
                        appearance=str(item.get("appearance", "")),
                        personality=personality,
                        voice_id=voice_id,
                        base_prompt=str(item.get("base_prompt", f"{item.get('name', 'character')} portrait")),
                    )
                )

            if not characters:
                raise LLMServiceError("LLM returned empty characters")
            _normalize_identity_flags(characters, source_text=text)
            return characters, max(0.0, min(1.0, confidence)), selected_model
    except LLMServiceError:
        raise
    except Exception as exc:
        logger.exception("Analyze characters failed")
        raise LLMServiceError(f"LLM character analysis failed: {exc}") from exc
