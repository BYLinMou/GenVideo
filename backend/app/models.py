from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModelInfo(BaseModel):
    id: str
    name: str
    provider: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    available: bool = False


class VoiceInfo(BaseModel):
    id: str
    name: str
    gender: str
    age: str
    description: str


class CharacterSuggestion(BaseModel):
    name: str
    role: str = "supporting"
    importance: int = 5
    appearance: str = ""
    personality: str = ""
    voice_id: str = "zh-CN-YunxiNeural"
    reference_image_path: str | None = None
    reference_image_url: str | None = None
    base_prompt: str = ""


class AnalyzeCharactersRequest(BaseModel):
    text: str
    analysis_depth: Literal["basic", "detailed"] = "detailed"
    model_id: str | None = None


class AnalyzeCharactersResponse(BaseModel):
    characters: list[CharacterSuggestion]
    confidence: float = 0.0
    model_used: str


class ConfirmCharactersRequest(BaseModel):
    characters: list[CharacterSuggestion]


class SegmentTextRequest(BaseModel):
    text: str
    method: Literal["sentence", "fixed", "smart"] = "sentence"
    fixed_size: int = Field(default=120, ge=20, le=1000)
    sentences_per_segment: int = Field(default=5, ge=1, le=50)
    model_id: str | None = None


class SegmentItem(BaseModel):
    index: int
    text: str
    sentence_count: int = 0


class SegmentTextResponse(BaseModel):
    segments: list[SegmentItem]
    total_segments: int
    total_sentences: int


class GenerateVideoRequest(BaseModel):
    text: str
    characters: list[CharacterSuggestion]
    segment_method: Literal["sentence", "fixed", "smart"] = "sentence"
    sentences_per_segment: int = Field(default=5, ge=1, le=50)
    max_segment_groups: int = Field(default=0, ge=0, le=10000)
    resolution: str = "1080x1920"
    image_aspect_ratio: str | None = None
    subtitle_style: Literal["basic", "highlight", "danmaku", "center"] = "highlight"
    camera_motion: Literal["vertical", "horizontal", "auto"] = "vertical"
    fps: int = Field(default=30, ge=15, le=60)
    model_id: str | None = None
    enable_scene_image_reuse: bool = True


class GenerateVideoResponse(BaseModel):
    job_id: str
    status: str


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: float = 0.0
    step: str = ""
    message: str = ""
    output_video_url: str | None = None
    output_video_path: str | None = None
    clip_count: int = 0
    clip_preview_urls: list[str] = Field(default_factory=list)


class CreateCharacterImageRequest(BaseModel):
    character_name: str
    prompt: str
    model_id: str | None = None
    resolution: str = "768x768"


class CharacterImageItem(BaseModel):
    path: str
    url: str
    filename: str

