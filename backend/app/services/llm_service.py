from __future__ import annotations

import json
import logging
import re

import httpx

from ..config import settings
from ..models import CharacterSuggestion
from ..voice_catalog import recommend_voice


logger = logging.getLogger(__name__)


def _base_url(path: str) -> str:
    return f"{settings.llm_api_base_url.rstrip('/')}{path}"


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


def split_sentences(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text.strip())
    if not clean:
        return []

    delimiters = {
        "\u3002",  # full stop
        "\uff01",  # exclamation
        "\uff1f",  # question
        "\uff1b",  # semicolon
        "\uff0c",  # comma
        ";",
        ",",
        "!",
        "?",
    }
    sentences: list[str] = []
    current_chars: list[str] = []
    length = len(clean)

    for index, char in enumerate(clean):
        current_chars.append(char)
        if char not in delimiters:
            continue

        next_char = clean[index + 1] if index + 1 < length else ""
        prev_char = clean[index - 1] if index - 1 >= 0 else ""

        if next_char in delimiters:
            continue

        # Avoid splitting each char when broken encoding appears as "????".
        if char == "?" and prev_char == "?":
            continue

        candidate = "".join(current_chars).strip()
        if candidate:
            sentences.append(candidate)
        current_chars = []

    tail = "".join(current_chars).strip()
    if tail:
        sentences.append(tail)

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
    clean = re.sub(r"\s+", " ", text.strip())
    if not clean:
        return []
    return [clean[index : index + chunk_size] for index in range(0, len(clean), chunk_size)]


async def segment_by_smart(text: str, model_id: str | None) -> list[str]:
    selected_model = model_id or settings.llm_default_model
    if not settings.llm_api_key:
        return segment_by_sentence_groups(text, sentences_per_segment=5)

    prompt = (
        "Split the following novel text into short-video segments. "
        "Try to cut at scene transitions and keep semantic coherence. "
        "Return strict JSON only in this schema: {\"segments\":[\"Segment 1\",\"Segment 2\"]}.\n\n"
        f"Text:\n{text[:14000]}"
    )
    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": "You are a strict JSON generator."},
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

    return segment_by_sentence_groups(text, sentences_per_segment=5)


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


def _character_prompt(text: str, depth: str) -> str:
    detail = "Output detailed fields" if depth == "detailed" else "Output concise fields"
    return (
        "You are a novel character analysis assistant. Extract major characters from the text and return JSON only. "
        f"{detail}. "
        "JSON schema: "
        "{\"characters\":[{\"name\":\"\",\"role\":\"\",\"importance\":1,"
        "\"appearance\":\"\",\"personality\":\"\",\"voice_id\":\"\",\"base_prompt\":\"\"}],"
        "\"confidence\":0.0}"
        "\n\nText:\n"
        f"{text[:14000]}"
    )


async def analyze_characters(text: str, depth: str, model_id: str | None) -> tuple[list[CharacterSuggestion], float, str]:
    selected_model = model_id or settings.llm_default_model
    if not settings.llm_api_key:
        return _fallback_character_analysis(text), 0.45, selected_model

    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": "You are a strict JSON generator."},
            {"role": "user", "content": _character_prompt(text, depth)},
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
            if not parsed:
                raise ValueError("LLM returned unparseable JSON")

            raw_items = parsed.get("characters") or []
            confidence = float(parsed.get("confidence", 0.75))

            characters: list[CharacterSuggestion] = []
            for item in raw_items:
                role = str(item.get("role", "supporting"))
                personality = str(item.get("personality", ""))
                voice_id = str(item.get("voice_id") or recommend_voice(role, personality))
                characters.append(
                    CharacterSuggestion(
                        name=str(item.get("name", "character")),
                        role=role,
                        importance=max(1, min(10, int(item.get("importance", 5)))),
                        appearance=str(item.get("appearance", "")),
                        personality=personality,
                        voice_id=voice_id,
                        base_prompt=str(item.get("base_prompt", f"{item.get('name', 'character')} portrait")),
                    )
                )

            if not characters:
                raise ValueError("Empty characters from LLM")
            return characters, max(0.0, min(1.0, confidence)), selected_model
    except Exception:
        logger.exception("Analyze characters failed, fallback to local extractor")
        return _fallback_character_analysis(text), 0.5, selected_model
