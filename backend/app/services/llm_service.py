from __future__ import annotations

import json
import re

import httpx

from ..config import settings
from ..models import CharacterSuggestion
from ..voice_catalog import recommend_voice


def _base_url(path: str) -> str:
    return f"{settings.llm_api_base_url.rstrip('/')}{path}"


async def probe_openai_models() -> list[str]:
    if not settings.llm_api_key:
        return []

    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(_base_url("/models"), headers=headers)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or []
            model_ids = [item.get("id") for item in data if item.get("id")]
            return sorted(model_ids)
    except Exception:
        return []


def _extract_json_object(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def _fallback_character_analysis(text: str) -> list[CharacterSuggestion]:
    cleaned = re.sub(r"\s+", " ", text)
    names = re.findall(r"[\u4e00-\u9fff]{2,3}", cleaned)
    ranked: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        if name in {"小說", "故事", "今天", "這個", "一個", "自己", "我們"}:
            continue
        seen.add(name)
        ranked.append(name)
        if len(ranked) >= 5:
            break

    if not ranked:
        ranked = ["旁白"]

    characters: list[CharacterSuggestion] = []
    for index, name in enumerate(ranked):
        role = "主角" if index == 0 else "配角"
        personality = "冷靜、果斷" if index == 0 else "溫和、友善"
        voice = recommend_voice(role, personality)
        characters.append(
            CharacterSuggestion(
                name=name,
                role=role,
                importance=max(10 - index, 5),
                appearance="外貌特徵待補充",
                personality=personality,
                suggested_voice=voice,
                suggested_style="anime style, cinematic light",
                base_prompt=f"{name}，{personality}，小說場景人物肖像",
            )
        )
    return characters


def _build_character_prompt(text: str, depth: str) -> str:
    detail_hint = "盡量詳盡" if depth == "detailed" else "簡潔"
    return (
        "你是一個專業的小說角色分析助手。"
        f"請根據給定文本提取主要角色，並以JSON格式輸出，{detail_hint}。"
        "請只輸出 JSON，不要額外文字。"
        "JSON 格式："
        "{\"characters\":[{\"name\":\"\",\"role\":\"\",\"importance\":1,\"appearance\":\"\","
        "\"personality\":\"\",\"suggested_voice\":\"\",\"suggested_style\":\"\",\"base_prompt\":\"\"}],"
        "\"confidence\":0.0}"
        "\n\n文本如下：\n"
        f"{text[:12000]}"
    )


async def analyze_characters(text: str, depth: str, model_id: str | None) -> tuple[list[CharacterSuggestion], float, str]:
    selected_model = model_id or settings.llm_default_model
    if not settings.llm_api_key:
        chars = _fallback_character_analysis(text)
        return chars, 0.42, selected_model

    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": "You are a strict JSON generator."},
            {"role": "user", "content": _build_character_prompt(text, depth)},
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
                raise ValueError("No parseable JSON in LLM output")

            raw_items = parsed.get("characters") or []
            confidence = float(parsed.get("confidence", 0.75))
            characters: list[CharacterSuggestion] = []
            for raw in raw_items:
                role = str(raw.get("role", "配角"))
                personality = str(raw.get("personality", ""))
                voice = str(raw.get("suggested_voice") or recommend_voice(role, personality))
                characters.append(
                    CharacterSuggestion(
                        name=str(raw.get("name", "角色")),
                        role=role,
                        importance=max(1, min(10, int(raw.get("importance", 5)))),
                        appearance=str(raw.get("appearance", "")),
                        personality=personality,
                        suggested_voice=voice,
                        suggested_style=str(raw.get("suggested_style", "anime style")),
                        base_prompt=str(raw.get("base_prompt", f"{raw.get('name', '角色')} 人像")),
                    )
                )

            if not characters:
                raise ValueError("Empty character list")
            return characters, max(0.0, min(1.0, confidence)), selected_model
    except Exception:
        chars = _fallback_character_analysis(text)
        return chars, 0.5, selected_model


def _split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?])", text)
    return [item.strip() for item in parts if item.strip()]


def segment_by_sentence(text: str, max_chars: int = 120) -> list[str]:
    sentences = _split_sentences(text)
    if not sentences:
        return []

    merged: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            if current:
                merged.append(current)
            current = sentence
    if current:
        merged.append(current)
    return merged


def segment_by_fixed(text: str, chunk_size: int = 120) -> list[str]:
    clean = re.sub(r"\s+", " ", text.strip())
    if not clean:
        return []
    return [clean[i : i + chunk_size] for i in range(0, len(clean), chunk_size)]


async def segment_by_smart(text: str, model_id: str | None) -> list[str]:
    selected_model = model_id or settings.llm_default_model
    if not settings.llm_api_key:
        return segment_by_sentence(text)

    prompt = (
        "請將以下小說文本分段為短視頻場景，每段控制在60-140字。"
        "輸出 JSON：{\"segments\":[\"段1\",\"段2\"]}。只輸出 JSON。"
        "\n\n文本：\n"
        f"{text[:12000]}"
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
        pass

    return segment_by_sentence(text)

