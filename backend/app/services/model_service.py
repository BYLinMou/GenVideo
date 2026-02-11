from __future__ import annotations

from .llm_service import probe_openai_models
from ..config import settings
from ..models import ModelInfo


DEFAULT_MODEL_CATALOG: list[ModelInfo] = [
    ModelInfo(
        id="gpt-4o-mini",
        name="GPT-4o mini",
        provider="openai-compatible",
        description="Fast and cost-effective model for extraction and segmentation.",
        capabilities=["text-analysis", "character-extraction", "segmentation"],
        available=False,
    ),
    ModelInfo(
        id="gpt-4.1-mini",
        name="GPT-4.1 mini",
        provider="openai-compatible",
        description="Balanced reasoning model for robust character analysis.",
        capabilities=["text-analysis", "character-extraction", "segmentation"],
        available=False,
    ),
    ModelInfo(
        id="gpt-4o",
        name="GPT-4o",
        provider="openai-compatible",
        description="High quality model for nuanced story understanding.",
        capabilities=["text-analysis", "character-extraction", "segmentation"],
        available=False,
    ),
]


async def get_models() -> list[ModelInfo]:
    catalog = [item.model_copy() for item in DEFAULT_MODEL_CATALOG]
    if not settings.llm_api_key:
        return catalog

    dynamic_ids = await probe_openai_models()
    if not dynamic_ids:
        for item in catalog:
            if item.id == settings.llm_default_model:
                item.available = True
        return catalog

    indexed = {item.id: item for item in catalog}
    for model_id in dynamic_ids:
        if model_id in indexed:
            indexed[model_id].available = True
        else:
            indexed[model_id] = ModelInfo(
                id=model_id,
                name=model_id,
                provider="openai-compatible",
                description="Discovered dynamically from configured provider.",
                capabilities=["text-analysis", "character-extraction", "segmentation"],
                available=True,
            )
    return list(indexed.values())

