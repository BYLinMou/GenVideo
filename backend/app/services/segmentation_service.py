from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .llm_service import group_sentences, segment_by_fixed, segment_by_smart, split_sentences


@dataclass
class SegmentPlan:
    segments: list[str]
    total_sentences: int
    request_signature: str


def count_sentences(text: str) -> int:
    return len(split_sentences(text))


def build_segment_request_signature(
    *,
    text: str,
    method: str,
    sentences_per_segment: int,
    fixed_size: int,
    model_id: str | None,
) -> str:
    payload = {
        "text": (text or "").strip(),
        "method": method or "sentence",
        "sentences_per_segment": max(1, int(sentences_per_segment or 1)),
        "fixed_size": max(20, int(fixed_size or 120)),
        "model_id": (model_id or "").strip(),
    }
    packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def _clean_segments(items: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for item in items or []:
        text = str(item or "").strip()
        if text:
            cleaned.append(text)
    return cleaned


def resolve_precomputed_segments(
    *,
    text: str,
    method: str,
    sentences_per_segment: int,
    fixed_size: int,
    model_id: str | None,
    request_signature: str | None,
    precomputed_segments: list[str] | None,
) -> list[str] | None:
    provided = (request_signature or "").strip()
    if not provided:
        return None
    expected = build_segment_request_signature(
        text=text,
        method=method,
        sentences_per_segment=sentences_per_segment,
        fixed_size=fixed_size,
        model_id=model_id,
    )
    if provided != expected:
        return None

    cleaned = _clean_segments(precomputed_segments)
    return cleaned or None


async def build_segment_plan(
    *,
    text: str,
    method: str,
    sentences_per_segment: int,
    fixed_size: int,
    model_id: str | None,
) -> SegmentPlan:
    selected_method = method if method in {"sentence", "fixed", "smart"} else "sentence"
    signature = build_segment_request_signature(
        text=text,
        method=selected_method,
        sentences_per_segment=sentences_per_segment,
        fixed_size=fixed_size,
        model_id=model_id,
    )

    if selected_method == "fixed":
        segments = segment_by_fixed(text, chunk_size=fixed_size)
        return SegmentPlan(segments=segments, total_sentences=0, request_signature=signature)

    if selected_method == "smart":
        segments = await segment_by_smart(text, model_id)
        return SegmentPlan(segments=segments, total_sentences=0, request_signature=signature)

    sentences = split_sentences(text)
    segments = group_sentences(sentences, sentences_per_segment)
    return SegmentPlan(segments=segments, total_sentences=len(sentences), request_signature=signature)
