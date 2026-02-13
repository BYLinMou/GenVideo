from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env.local")
load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_name: str = "GenVideo Backend"
    app_env: str = "development"
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")

    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_api_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_API_BASE_URL")
    llm_default_model: str = Field(default="gpt-oss-120b", alias="LLM_DEFAULT_MODEL")

    image_api_key: str = Field(default="", alias="IMAGE_API_KEY")
    image_api_url: str = Field(default="https://api.poe.com/v1", alias="IMAGE_API_URL")
    image_model: str = Field(default="nano-banana", alias="IMAGE_MODEL")

    tts_api_url: str = Field(default="", alias="TTS_API_URL")
    subtitle_font_path: str = Field(default="", alias="SUBTITLE_FONT_PATH")

    output_dir: str = Field(default="outputs", alias="OUTPUT_DIR")
    temp_dir: str = Field(default="outputs/temp", alias="TEMP_DIR")
    character_ref_dir: str = Field(default="assets/character_refs", alias="CHARACTER_REF_DIR")
    watermark_asset_dir: str = Field(default="assets/watermark", alias="WATERMARK_ASSET_DIR")
    scene_cache_dir: str = Field(default="assets/scene_cache/images", alias="SCENE_CACHE_DIR")
    scene_cache_index_path: str = Field(default="assets/scene_cache/index.json", alias="SCENE_CACHE_INDEX_PATH")
    scene_cache_db_path: str = Field(default="assets/scene_cache/scene_cache.db", alias="SCENE_CACHE_DB_PATH")
    jobs_db_path: str = Field(default="assets/jobs/jobs.db", alias="JOBS_DB_PATH")
    job_clip_preview_limit: int = Field(default=200, alias="JOB_CLIP_PREVIEW_LIMIT")
    log_dir: str = Field(default="logs", alias="LOG_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    cors_allow_origins: str = Field(default="*", alias="CORS_ALLOW_ORIGINS")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")


settings = Settings()


def project_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path
