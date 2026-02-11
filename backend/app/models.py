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


class CharacterSuggestion(BaseModel):
    name: str
    role: str = "配角"
    importance: int = 5
    appearance: str = ""
    personality: str = ""
    suggested_voice: str = "zh-CN-YunxiNeural"
    suggested_style: str = "anime style"
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
    method: Literal["sentence", "fixed", "smart"] = "smart"
    fixed_size: int = 120
    model_id: str | None = None


class SegmentItem(BaseModel):
    index: int
    text: str


class SegmentTextResponse(BaseModel):
    segments: list[SegmentItem]


class GenerateVideoRequest(BaseModel):
    text: str
    characters: list[CharacterSuggestion]
    segment_method: Literal["sentence", "fixed", "smart"] = "smart"
    max_segments: int = Field(default=0, ge=0, le=10000)
    segments_per_image: int = Field(default=5, ge=1, le=50)
    resolution: str = "1080x1920"
    subtitle_style: Literal["basic", "highlight", "danmaku", "center"] = "highlight"
    fps: int = 30
    model_id: str | None = None


class GenerateVideoResponse(BaseModel):
    job_id: str
    status: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: float = 0.0
    step: str = ""
    message: str = ""
    output_video_url: str | None = None
    output_video_path: str | None = None
