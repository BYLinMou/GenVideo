from __future__ import annotations

import logging
from datetime import datetime
import shutil
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import project_path, settings
from .logging_setup import setup_logging
from .models import (
    AnalyzeCharactersRequest,
    AnalyzeCharactersResponse,
    BgmLibraryItem,
    BgmSelectRequest,
    BgmStatusResponse,
    BgmUploadResponse,
    CharacterImageItem,
    ConfirmCharactersRequest,
    CreateCharacterImageRequest,
    GenerateNovelAliasesRequest,
    GenerateNovelAliasesResponse,
    GenerateVideoRequest,
    GenerateVideoResponse,
    RemixBgmRequest,
    RemixBgmResponse,
    SegmentItem,
    SegmentTextRequest,
    SegmentTextResponse,
)
from .services.character_assets_service import (
    create_character_reference_image,
    list_character_reference_images,
)
from .services.llm_service import (
    LLMServiceError,
    analyze_characters,
    generate_novel_aliases,
    group_sentences,
    segment_by_fixed,
    segment_by_smart,
    split_sentences,
)
from .services.model_service import get_models
from .services.video_service import _render_final_sync, cancel_job, create_job
from .state import job_store
from .voice_catalog import VOICE_INFOS


setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)

origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

project_path(settings.output_dir).mkdir(parents=True, exist_ok=True)
project_path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
project_path(settings.character_ref_dir).mkdir(parents=True, exist_ok=True)
project_path("assets/bgm").mkdir(parents=True, exist_ok=True)
project_path(settings.scene_cache_dir).mkdir(parents=True, exist_ok=True)
project_path(settings.scene_cache_index_path).parent.mkdir(parents=True, exist_ok=True)

app.mount("/outputs", StaticFiles(directory=project_path(settings.output_dir)), name="outputs")
app.mount(
    "/assets/character_refs",
    StaticFiles(directory=project_path(settings.character_ref_dir)),
    name="character_refs",
)
app.mount(
    "/assets/bgm",
    StaticFiles(directory=project_path("assets/bgm")),
    name="bgm_assets",
)


def _bgm_root() -> Path:
    root = project_path("assets/bgm")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _bgm_current_path() -> Path:
    return project_path("assets/bgm.mp3")


def _bgm_default_path() -> Path:
    return project_path("assets/bgm/happinessinmusic-rock-trailer-417598.mp3")


def _resolve_active_bgm_path() -> Path:
    current = _bgm_current_path()
    if current.exists():
        return current
    return _bgm_default_path()


def _current_bgm_source_filename() -> str | None:
    current = _resolve_active_bgm_path()
    if not current.exists():
        return None

    current_data = current.read_bytes()
    for path in sorted(_bgm_root().glob("*.mp3")):
        try:
            if path.read_bytes() == current_data:
                return path.name
        except Exception:
            continue
    return None


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"internal error: {exc}"})


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.app_env}


@app.get("/api/logs/tail")
async def tail_logs(lines: int = 200) -> dict:
    log_path = project_path(settings.log_dir) / "backend.log"
    if not log_path.exists():
        return {"lines": []}
    all_lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return {"lines": all_lines[-max(1, min(lines, 1000)) :]}


@app.get("/api/models")
async def list_models() -> dict:
    models = await get_models()
    return {"models": [item.model_dump() for item in models]}


@app.get("/api/tts/voices")
async def list_voices() -> dict:
    return {"voices": [voice.model_dump() for voice in VOICE_INFOS]}


@app.post("/api/analyze-characters", response_model=AnalyzeCharactersResponse)
async def analyze_characters_api(payload: AnalyzeCharactersRequest) -> AnalyzeCharactersResponse:
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    try:
        characters, confidence, model_used = await analyze_characters(payload.text, payload.analysis_depth, payload.model_id)
        return AnalyzeCharactersResponse(characters=characters, confidence=confidence, model_used=model_used)
    except LLMServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/generate-novel-aliases", response_model=GenerateNovelAliasesResponse)
async def generate_novel_aliases_api(payload: GenerateNovelAliasesRequest) -> GenerateNovelAliasesResponse:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        aliases, model_used = await generate_novel_aliases(text=text, count=payload.count, model_id=payload.model_id)
        return GenerateNovelAliasesResponse(aliases=aliases, model_used=model_used)
    except LLMServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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

    total_sentences = len(split_sentences(text))
    if payload.method == "fixed":
        pieces = segment_by_fixed(text, chunk_size=payload.fixed_size)
    elif payload.method == "smart":
        pieces = await segment_by_smart(text, payload.model_id)
    else:
        sentences = split_sentences(text)
        pieces = group_sentences(sentences, payload.sentences_per_segment)

    segments = []
    for index, item in enumerate(pieces):
        count = 0
        if payload.method == "sentence":
            count = len(split_sentences(item))
        segments.append(SegmentItem(index=index, text=item, sentence_count=count))

    return SegmentTextResponse(
        segments=segments,
        total_segments=len(segments),
        total_sentences=total_sentences,
    )


@app.get("/api/character-reference-images")
async def get_character_reference_images(request: Request) -> dict:
    base_url = str(request.base_url).rstrip("/")
    images = list_character_reference_images(base_url)
    return {"images": [item.model_dump() for item in images]}


@app.post("/api/character-reference-images/upload", response_model=CharacterImageItem)
async def upload_character_reference_image(request: Request, file: UploadFile = File(...)) -> CharacterImageItem:
    suffix = Path(file.filename or "image.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="unsupported image format")

    base_url = str(request.base_url).rstrip("/")
    root = project_path(settings.character_ref_dir)
    root.mkdir(parents=True, exist_ok=True)
    stem = Path(file.filename).stem.replace(" ", "_") or "upload"
    dest = root / f"upload_{stem}_{uuid4().hex[:8]}{suffix}"
    content = await file.read()
    dest.write_bytes(content)

    return CharacterImageItem(
        path=dest.as_posix(),
        url=f"{base_url}/assets/character_refs/{quote(dest.name)}",
        filename=dest.name,
    )


@app.post("/api/character-reference-images/generate", response_model=CharacterImageItem)
async def generate_character_reference_image(request: Request, payload: CreateCharacterImageRequest) -> CharacterImageItem:
    base_url = str(request.base_url).rstrip("/")
    try:
        width_raw, height_raw = payload.resolution.lower().split("x")
        resolution = (max(256, int(width_raw)), max(256, int(height_raw)))
    except Exception:
        resolution = (768, 768)
    return await create_character_reference_image(payload.character_name, payload.prompt, resolution, base_url)


@app.post("/api/bgm/upload", response_model=BgmUploadResponse)
async def upload_bgm(file: UploadFile = File(...)) -> BgmUploadResponse:
    suffix = Path(file.filename or "bgm.mp3").suffix.lower()
    if suffix != ".mp3":
        raise HTTPException(status_code=400, detail="only .mp3 is supported")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")

    bgm_root = _bgm_root()
    stem = Path(file.filename or "bgm").stem.replace(" ", "_") or "bgm"
    lib_path = bgm_root / f"{stem}_{uuid4().hex[:8]}.mp3"
    lib_path.write_bytes(data)

    bgm_path = _bgm_current_path()
    bgm_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(lib_path, bgm_path)

    return BgmUploadResponse(
        status="ok",
        path=lib_path.as_posix(),
        filename=lib_path.name,
        size=len(data),
    )


@app.get("/api/bgm/library")
async def list_bgm_library(request: Request) -> dict:
    base_url = str(request.base_url).rstrip("/")
    items: list[BgmLibraryItem] = []
    for path in sorted(_bgm_root().glob("*.mp3")):
        stat = path.stat()
        items.append(
            BgmLibraryItem(
                path=path.as_posix(),
                url=f"{base_url}/assets/bgm/{quote(path.name)}",
                filename=path.name,
                size=int(stat.st_size),
                updated_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            )
        )
    return {"items": [item.model_dump() for item in items]}


@app.post("/api/bgm/select", response_model=BgmStatusResponse)
async def select_bgm(payload: BgmSelectRequest) -> BgmStatusResponse:
    target = _bgm_root() / Path(payload.filename).name
    if not target.exists() or target.suffix.lower() != ".mp3":
        raise HTTPException(status_code=404, detail="bgm file not found")

    current = _bgm_current_path()
    current.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(target, current)

    stat = current.stat()
    return BgmStatusResponse(
        exists=True,
        path=current.as_posix(),
        filename=current.name,
        size=int(stat.st_size),
        updated_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        source_filename=target.name,
    )


@app.delete("/api/bgm/current")
async def delete_current_bgm() -> dict:
    current = _bgm_current_path()
    if current.exists():
        current.unlink()
    return {"status": "ok"}


@app.get("/api/bgm", response_model=BgmStatusResponse)
async def get_bgm_status() -> BgmStatusResponse:
    bgm_path = _resolve_active_bgm_path()
    if not bgm_path.exists():
        return BgmStatusResponse(
            exists=False,
            path=bgm_path.as_posix(),
            filename=bgm_path.name,
            size=0,
            updated_at=None,
            source_filename=None,
        )

    stat = bgm_path.stat()
    return BgmStatusResponse(
        exists=True,
        path=bgm_path.as_posix(),
        filename=bgm_path.name,
        size=int(stat.st_size),
        updated_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        source_filename=_current_bgm_source_filename(),
    )


@app.post("/api/generate-video", response_model=GenerateVideoResponse)
async def generate_video(request: Request, payload: GenerateVideoRequest) -> GenerateVideoResponse:
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    base_url = str(request.base_url).rstrip("/")
    job_id = create_job(payload=payload, base_url=base_url)
    return GenerateVideoResponse(job_id=job_id, status="queued")


@app.post("/api/jobs/{job_id}/remix-bgm", response_model=RemixBgmResponse)
async def remix_bgm(request: Request, job_id: str, payload: RemixBgmRequest) -> RemixBgmResponse:
    status = job_store.get(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    if status.status != "completed" or not status.output_video_path:
        raise HTTPException(status_code=409, detail="video not ready")

    final_path = Path(status.output_video_path)
    if not final_path.exists():
        raise HTTPException(status_code=404, detail="video missing")

    clip_root = project_path(settings.temp_dir) / job_id / "clips"
    clip_paths = [str(item) for item in sorted(clip_root.glob("clip_*.mp4"))]
    if not clip_paths:
        raise HTTPException(status_code=404, detail="segment clips not found for remix")

    await run_in_threadpool(
        _render_final_sync,
        clip_paths,
        final_path,
        payload.fps or 30,
        payload.bgm_enabled,
        payload.bgm_volume,
    )

    base_url = str(request.base_url).rstrip("/")
    return RemixBgmResponse(
        job_id=job_id,
        status="completed",
        output_video_url=f"{base_url}/api/jobs/{job_id}/video",
    )


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_video_job(request: Request, job_id: str) -> dict:
    base_url = str(request.base_url).rstrip("/")
    if not cancel_job(job_id, base_url):
        raise HTTPException(status_code=404, detail="job not found")
    return {"status": "cancel_requested", "job_id": job_id}


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


@app.get("/api/jobs/{job_id}/clips/{clip_index}")
async def get_job_clip(job_id: str, clip_index: int):
    if clip_index < 0:
        raise HTTPException(status_code=400, detail="clip_index must be >= 0")
    clip_path = project_path(settings.temp_dir) / job_id / "clips" / f"clip_{clip_index:04d}.mp4"
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="clip not found")
    return FileResponse(clip_path, media_type="video/mp4", filename=clip_path.name)
