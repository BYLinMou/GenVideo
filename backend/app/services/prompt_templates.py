from __future__ import annotations

STRICT_JSON_SYSTEM_PROMPT = "You are a strict JSON generator."
SCENE_REUSE_SELECTOR_SYSTEM_PROMPT = "You are a strict JSON selector for scene-image reuse. Output JSON only."

DEFAULT_IMAGE_PROMPT = "Generate one single image based on the current plot segment."

CHARACTER_REFERENCE_FULL_BODY_RULES = (
    "Character reference sheet style. "
    "2D anime style, clean line art, cel shading, non-photorealistic. "
    "Show only one character (the target character) in the image. "
    "No other people, no crowd, no background characters, no extra faces. "
    "Must show full body from head to toe in frame. "
    "Do NOT crop to half body, portrait, or close-up. "
    "Include key props/weapons/accessories in full view. "
    "Keep anatomy complete and visible."
)

SEGMENT_IMAGE_BUNDLE_RULES = (
    "Keep facial identity consistent across scenes; hairstyle and outfit may change when required by the current segment.",
    "Character appearance is optional per frame: LLM may output pure scene/environment frame when segment focus is on place/atmosphere/system message.",
    "Reference image (if present) is for character look only, never for scene/background.",
    "Prefer 2D anime style, clean line art and cel shading; avoid photorealistic or 3D-render look.",
    "If multiple reference images are provided, this segment may involve multiple characters. Keep each identity consistent.",
    "Scene/background/action must be inferred from current segment text.",
    "If current segment omits explicit character name, use adjacent segment context to infer the implied acting/speaking character.",
    "When character_candidates are provided, return primary_index and related_indexes using those candidate indexes.",
    "For TTS assignment, use tts_sentence_units and return sentence_speakers by sentence_index only; never rewrite or copy sentence text.",
    "speaker_type must be narrator or character; when speaker_type=character, character_index must be a valid character_candidates index.",
    "When sentence lacks explicit speaker name, infer from adjacent context and speaking verbs, but still output index mapping only.",
    "Return is_scene_only=true when this frame should be pure environment/scene without visible character.",
    "If story_world_context is provided, keep era/architecture/costume/props/culture consistent with that world setting.",
    "Output one concise production-ready prompt in English.",
    "Also output strict scene metadata for cache-reuse matching.",
    "Action must be concrete visible action (e.g. holding knife, raising right hand, running).",
    "Location must be concrete place if present (e.g. classroom, corridor, street).",
    "Scene elements must be concrete visual nouns/background details.",
    "English onomatopoeia is allowed when visually appropriate.",
    "Environmental/prop text is allowed only when naturally required by the scene (e.g. signs, labels).",
    "Do not add speech bubbles, dialogue balloons, subtitle-like dialogue text, or character conversation captions.",
    "If any visible words/labels/signage/onomatopoeia are used in the image, they must use English letters only.",
    "No markdown, no explanation.",
)


def build_story_world_summary_prompt(text: str) -> str:
    return (
        "You summarize the global world setting for a novel-to-video pipeline. "
        "Return strict JSON only in schema: "
        '{"world_summary":""}. '
        "world_summary must be one concise English sentence (max 40 words) that captures era, cultural setting, architecture/props/costume tone, "
        "and visual world constraints. Prefer broad stable setting, not per-scene details."
        "\n\nNovel text:\n"
        f"{text[:14000]}"
    )

SCENE_REUSE_SELECTOR_RULES = (
    "This decision is strict: if uncertain, return should_reuse=false.",
    "User experience first: avoid wrong reuse. Wrong reuse is worse than generating a new image.",
    "Only reuse at high match level.",
    "If target has reference_image_paths, selected candidate must overlap at least one same path.",
    "If target has reference_image_ids, selected candidate must overlap at least one same id.",
    "character_match must be true, unless both target and selected candidate are is_scene_only=true.",
    "action_match must be true, otherwise reject.",
    "If both sides contain location hints, location_match must be true.",
    "If scene elements differ substantially, reject.",
    "Do not select by writing style; only compare character, action and location.",
    "Return strict JSON only.",
)


def build_smart_segmentation_prompt(clean_text: str) -> str:
    return (
        "Split the following novel text into short-video segments. "
        "Try to cut at scene transitions and keep semantic coherence. "
        "Do not rewrite, summarize, omit, or reorder any content; preserve original wording exactly. "
        "Return strict JSON only in this schema: {\"segments\":[\"Segment 1\",\"Segment 2\"]}.\n\n"
        f"Text:\n{clean_text[:14000]}"
    )


def build_character_identity_guard(
    name: str,
    anchors: str,
    personality: str,
    has_reference: bool,
) -> str:
    personality_clause = f" Character personality and vibe: {personality}." if personality else ""
    reference_clause = (
        f"Use the provided reference image primarily for facial identity matching of {name} "
        "(face shape, key facial features, expression style). "
        "Do not copy composition or background from the reference image. "
        if has_reference
        else "No reference image is available; enforce identity from appearance anchors only. "
    )
    return (
        "Character consistency is mandatory across frames. "
        "But if current segment is better represented as environment-only/scene-only, character does not need to appear in frame. "
        f"{reference_clause}"
        "Never change core facial identity. Hairstyle and outfit may adapt to plot needs. "
        f"Character appearance anchors: {anchors}."
        f"{personality_clause}"
    )


def build_fallback_segment_image_prompt(guard: str, scene_text: str, story_world_context: str | None = None) -> str:
    world_clause = (
        f"Global world setting consistency requirement: {story_world_context.strip()}. "
        if (story_world_context or "").strip()
        else ""
    )
    return (
        f"{guard} "
        f"{world_clause}"
        "Build one single image frame according to this current plot segment: "
        f"{scene_text}. "
        "It is allowed to output a pure scene/environment shot without any character when that better matches the segment. "
        "Background and action must come from the current plot segment. "
        "2D anime style, clean line art, cel shading, expressive eyes, cinematic illustration, detailed lighting, clean composition, non-photorealistic, no 3D render, no watermark. "
        "English onomatopoeia is allowed when visually appropriate, and required environmental text/signage is allowed. "
        "Do not add speech bubbles, dialogue balloons, subtitle-like dialogue text, or character conversation captions. "
        "If adding any visible text or onomatopoeia, use English letters only."
    )


def build_final_segment_image_prompt(
    guard: str,
    scene_text: str,
    candidate: str,
    story_world_context: str | None = None,
) -> str:
    world_clause = (
        f"Global world setting consistency requirement: {story_world_context.strip()}. "
        if (story_world_context or "").strip()
        else ""
    )
    return (
        f"{guard} "
        f"{world_clause}"
        f"Current plot segment: {scene_text}. "
        "If character is not necessary for this segment, you may generate scene-only frame. "
        "Scene/background/action must follow current plot segment. "
        f"Additional style and composition details: {candidate}. "
        "English onomatopoeia is allowed when visually appropriate, and required environmental text/signage is allowed. "
        "Do not add speech bubbles, dialogue balloons, subtitle-like dialogue text, or character conversation captions. "
        "If any visible text appears in frame (signs, SFX, labels), it must use English letters only."
    )


def build_character_analysis_prompt(
    text: str,
    depth: str,
    allowed_ids: str,
    voice_lines: str,
    story_world_context: str | None = None,
) -> str:
    detail = "Output detailed fields" if depth == "detailed" else "Output concise fields"
    world_clause = (
        f"Global story world context: {story_world_context.strip()}. "
        if (story_world_context or "").strip()
        else ""
    )
    return (
        "You are a novel character analysis assistant. Extract major characters from the text and return JSON only. "
        f"{detail}. "
        f"{world_clause}"
        "Character setting must be consistent with the story world context: era, region/culture, social identity, clothing, props and tone. "
        "Unless the text explicitly says otherwise, avoid cross-world mismatch (e.g. ancient Chinese setting with modern/western/Japanese role styling). "
        "Also determine character identity flags: is_main_character and is_story_self. "
        "is_story_self means this character corresponds to first-person narrator 'I/我' in the novel perspective. "
        "At most one character can be is_main_character=true, and at most one can be is_story_self=true. "
        "voice_id must be selected strictly from the allowed voice IDs below. "
        "Do not invent any new voice name or ID. "
        "If unsure, choose the closest one from the list. "
        "JSON schema: "
        "{\"characters\":[{\"name\":\"\",\"role\":\"\",\"importance\":1,"
        "\"is_main_character\":false,\"is_story_self\":false,"
        "\"appearance\":\"\",\"personality\":\"\",\"voice_id\":\"\",\"base_prompt\":\"\"}],"
        "\"confidence\":0.0}"
        "\n\nAllowed voice IDs: "
        f"{allowed_ids}"
        "\nVoice catalog:"
        f"\n{voice_lines}"
        "\n\nText:\n"
        f"{text[:14000]}"
    )


def build_alias_prompt(text: str, count: int) -> str:
    return (
        "你是中文小说命名顾问。请基于文本生成小说‘别名’候选。"
        "硬性规则：\n"
        "1) 每个别名必须是4到8个汉字；\n"
        "2) 不能包含数字、英文字母、标点符号、空格；\n"
        "3) 禁止使用常见词语/俗语/成语/地名作为核心表达；\n"
        "4) 风格要和原文题材、情绪、意象一致；\n"
        f"5) 一次输出{count}个，不要重复；\n"
        "6) 禁止使用生僻字，尽量使用常用汉字。\n"
        "仅输出严格JSON：{\"aliases\":[\"别名1\",\"别名2\"]}\n\n"
        f"文本：\n{text[:12000]}"
    )


def build_character_reference_prompt(prompt: str) -> str:
    base = (prompt or "").strip()
    if not base:
        return CHARACTER_REFERENCE_FULL_BODY_RULES
    return f"{base}. {CHARACTER_REFERENCE_FULL_BODY_RULES}"


def build_image_retry_prompt(prompt: str) -> str:
    return (
        "Create one single image only. Do not explain. "
        "English onomatopoeia is allowed when visually appropriate, and required environmental text/signage is allowed. "
        "Do not add speech bubbles, dialogue balloons, subtitle-like dialogue text, or character conversation captions. "
        "If any visible text appears in frame, use English letters only. "
        f"2D anime style, clean line art, cel shading, expressive eyes, non-photorealistic, no 3D render. Illustration based on this description: {prompt}"
    )
