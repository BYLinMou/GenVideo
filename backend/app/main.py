from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .models import (
    AnalyzeCharactersRequest,
    AnalyzeCharactersResponse,
    ConfirmCharactersRequest,
    GenerateVideoRequest,
    GenerateVideoResponse,
    SegmentItem,
    SegmentTextRequest,
    SegmentTextResponse,
)
from .services.llm_service import analyze_characters, segment_by_fixed, segment_by_sentence, segment_by_smart
from .services.model_service import get_models
from .services.video_service import create_job
from .state import job_store


app = FastAPI(title=settings.app_name)

origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Path(settings.output_dir).mkdir(parents=True, exist_ok=True)
Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)

app.mount("/outputs", StaticFiles(directory=settings.output_dir), name="outputs")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.app_env}


@app.get("/api/models")
async def list_models() -> dict:
    models = await get_models()
    return {"models": [item.model_dump() for item in models]}


@app.post("/api/analyze-characters", response_model=AnalyzeCharactersResponse)
async def analyze_characters_api(payload: AnalyzeCharactersRequest) -> AnalyzeCharactersResponse:
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    characters, confidence, model_used = await analyze_characters(
        text=payload.text,
        depth=payload.analysis_depth,
        model_id=payload.model_id,
    )
    return AnalyzeCharactersResponse(characters=characters, confidence=confidence, model_used=model_used)


@app.post("/api/confirm-characters")
async def confirm_characters(payload: ConfirmCharactersRequest) -> dict:
    if not payload.characters:
        raise HTTPException(status_code=400, detail="characters is required")
    return {"status": "ok", "characters": [item.model_dump() for item in payload.characters]}


@app.post("/api/segment-text", response_model=SegmentTextResponse)
async def segment_text(payload: SegmentTextRequest) -> SegmentTextResponse:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    if payload.method == "sentence":
        pieces = segment_by_sentence(text)
    elif payload.method == "fixed":
        pieces = segment_by_fixed(text, chunk_size=payload.fixed_size)
    else:
        pieces = await segment_by_smart(text, payload.model_id)

    return SegmentTextResponse(segments=[SegmentItem(index=index, text=item) for index, item in enumerate(pieces)])


@app.post("/api/generate-video", response_model=GenerateVideoResponse)
async def generate_video(request: Request, payload: GenerateVideoRequest) -> GenerateVideoResponse:
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    base_url = str(request.base_url).rstrip("/")
    job_id = create_job(payload=payload, base_url=base_url)
    return GenerateVideoResponse(job_id=job_id, status="queued")


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str) -> dict:
    status = job_store.get(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return status.model_dump()


@app.get("/api/jobs/{job_id}/video")
async def get_job_video(job_id: str):
    status = job_store.get(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    if status.status != "completed" or not status.output_video_path:
        raise HTTPException(status_code=409, detail="video not ready")

    path = Path(status.output_video_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="video missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)

