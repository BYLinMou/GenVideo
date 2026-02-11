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

    delimiters = {"。", "！", "？", "；", "，", ";", ",", "!", "?"}
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

        # 防止文本编码损坏时出现 "????" 导致每字符切分
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
        "請把以下中文小說文本分為短視頻段落，盡量在場景轉換處切分。"
        "輸出 JSON：{\"segments\":[\"段落1\",\"段落2\"]}，只輸出 JSON。\n\n"
        f"文本：\n{text[:14000]}"
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
    ignored = {"小說", "故事", "今天", "這個", "一個", "自己", "我們"}

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
        ranked = ["旁白"]

    output: list[CharacterSuggestion] = []
    for index, name in enumerate(ranked):
        role = "主角" if index == 0 else "配角"
        personality = "冷靜、果斷" if index == 0 else "溫和、友善"
        output.append(
            CharacterSuggestion(
                name=name,
                role=role,
                importance=max(10 - index, 5),
                appearance="外貌待補充",
                personality=personality,
                voice_id=recommend_voice(role, personality),
                base_prompt=f"{name}，{personality}，小說角色立繪",
            )
        )
    return output


def _character_prompt(text: str, depth: str) -> str:
    detail = "請輸出詳細信息" if depth == "detailed" else "請輸出精簡信息"
    return (
        "你是小說角色分析助手。從文本中提取主要角色，並返回 JSON。"
        f"{detail}。只輸出 JSON。"
        "JSON 格式："
        "{\"characters\":[{\"name\":\"\",\"role\":\"\",\"importance\":1,"
        "\"appearance\":\"\",\"personality\":\"\",\"voice_id\":\"\","
        "\"base_prompt\":\"\"}],\"confidence\":0.0}"
        "\n\n文本：\n"
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
                role = str(item.get("role", "配角"))
                personality = str(item.get("personality", ""))
                voice_id = str(item.get("voice_id") or recommend_voice(role, personality))
                characters.append(
                    CharacterSuggestion(
                        name=str(item.get("name", "角色")),
                        role=role,
                        importance=max(1, min(10, int(item.get("importance", 5)))),
                        appearance=str(item.get("appearance", "")),
                        personality=personality,
                        voice_id=voice_id,
                        base_prompt=str(item.get("base_prompt", f"{item.get('name', '角色')} 立繪")),
                    )
                )

            if not characters:
                raise ValueError("Empty characters from LLM")
            return characters, max(0.0, min(1.0, confidence)), selected_model
    except Exception:
        logger.exception("Analyze characters failed, fallback to local extractor")
        return _fallback_character_analysis(text), 0.5, selected_model
