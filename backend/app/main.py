from __future__ import annotations

import hmac
import hashlib
import logging
from datetime import datetime
import shutil
import subprocess
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
    FinalVideoItem,
    FinalVideoListResponse,
    GenerateNovelAliasesRequest,
    GenerateNovelAliasesResponse,
    GenerateVideoRequest,
    GenerateVideoResponse,
    JobStatus,
    RemixBgmRequest,
    RemixBgmResponse,
    SegmentItem,
    SegmentTextRequest,
    SegmentTextResponse,
    WorkspaceAuthStatusResponse,
    WorkspaceLoginRequest,
)
from .services.character_assets_service import (
    create_character_reference_image,
    list_character_reference_images,
)
from .services.llm_service import (
    LLMServiceError,
    analyze_characters,
    generate_novel_aliases,
)
from .services.segmentation_service import build_segment_plan
from .services.segmentation_service import count_sentences
from .services.model_service import get_models
from .services.video_service import _render_final_sync, cancel_job, create_job, resume_interrupted_jobs, resume_job
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
project_path(settings.watermark_asset_dir).mkdir(parents=True, exist_ok=True)
project_path("assets/bgm").mkdir(parents=True, exist_ok=True)
project_path(settings.scene_cache_dir).mkdir(parents=True, exist_ok=True)
project_path(settings.scene_cache_index_path).parent.mkdir(parents=True, exist_ok=True)
project_path(settings.scene_cache_db_path).parent.mkdir(parents=True, exist_ok=True)
project_path(settings.jobs_db_path).parent.mkdir(parents=True, exist_ok=True)

app.mount("/outputs", StaticFiles(directory=project_path(settings.output_dir)), name="outputs")
app.mount(
    "/assets/character_refs",
    StaticFiles(directory=project_path(settings.character_ref_dir)),
    name="character_refs",
)
app.mount(
    "/assets/watermark",
    StaticFiles(directory=project_path(settings.watermark_asset_dir)),
    name="watermark_assets",
)
app.mount(
    "/assets/bgm",
    StaticFiles(directory=project_path("assets/bgm")),
    name="bgm_assets",
)

WORKSPACE_AUTH_COOKIE = "workspace_auth_token"


def _workspace_password_required() -> bool:
    return bool((settings.admin_password or "").strip())


def _workspace_password_valid(provided: str | None) -> bool:
    expected = (settings.admin_password or "").strip()
    if not expected:
        return True
    current = str(provided or "")
    return hmac.compare_digest(current, expected)


def _workspace_password_token(raw: str | None) -> str:
    value = str(raw or "")
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _workspace_cookie_valid(request: Request) -> bool:
    expected = (settings.admin_password or "").strip()
    if not expected:
        return True
    expected_token = _workspace_password_token(expected)
    current_token = str(request.cookies.get(WORKSPACE_AUTH_COOKIE) or "")
    if not current_token:
        return False
    return hmac.compare_digest(current_token, expected_token)


def _is_public_api_path(path: str) -> bool:
    normalized = str(path or "").strip()
    if not normalized.startswith("/api/"):
        return True
    public_prefixes = (
        "/api/health",
        "/api/workspace-auth",
        "/api/final-videos",
    )
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in public_prefixes)


@app.middleware("http")
async def workspace_auth_middleware(request: Request, call_next):
    if request.method.upper() == "OPTIONS":
        return await call_next(request)

    if not _workspace_password_required() or _is_public_api_path(request.url.path):
        return await call_next(request)

    if _workspace_password_valid(request.headers.get("x-workspace-password")):
        return await call_next(request)
    if _workspace_cookie_valid(request):
        return await call_next(request)

    return JSONResponse(status_code=401, content={"detail": "workspace authentication required"})


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


def _build_disk_job_status(job_id: str) -> JobStatus | None:
    final_path = project_path(settings.output_dir) / f"{job_id}.mp4"
    if not final_path.exists():
        return None
    try:
        if int(final_path.stat().st_size) < 16384:
            return None
    except Exception:
        return None

    clip_root = project_path(settings.temp_dir) / job_id / "clips"
    clip_count = sum(1 for _ in clip_root.glob("clip_*.mp4")) if clip_root.exists() else 0
    preview_limit = max(0, min(clip_count, int(settings.job_clip_preview_limit or 0)))
    preview_urls = [f"/api/jobs/{job_id}/clips/{index}" for index in range(preview_limit)]

    return JobStatus(
        job_id=job_id,
        status="completed",
        progress=1.0,
        step="done",
        message="Recovered from output directory",
        current_segment=clip_count,
        total_segments=clip_count,
        output_video_url=f"/api/jobs/{job_id}/video",
        output_video_path=str(final_path),
        clip_count=clip_count,
        clip_preview_urls=preview_urls,
    )


def _resolve_job_status(job_id: str) -> JobStatus | None:
    status = job_store.get(job_id)

    if status and status.status in {"queued", "running"}:
        return status

    recovered = _build_disk_job_status(job_id)
    if recovered and (not status or status.status in {"failed", "cancelled"} or not status.output_video_path):
        job_store.set(recovered)
        return recovered

    if status:
        return status
    return recovered


def _job_clip_path(job_id: str, clip_index: int) -> Path:
    return project_path(settings.temp_dir) / job_id / "clips" / f"clip_{clip_index:04d}.mp4"


def _job_clip_thumb_path(job_id: str, clip_index: int) -> Path:
    return project_path(settings.temp_dir) / job_id / "thumbs" / f"clip_{clip_index:04d}.jpg"


def _normalize_final_video_filename(filename: str) -> str:
    safe_name = Path(filename or "").name
    if not safe_name or safe_name != str(filename or ""):
        raise HTTPException(status_code=400, detail="invalid filename")
    if Path(safe_name).suffix.lower() != ".mp4":
        raise HTTPException(status_code=400, detail="only mp4 files are supported")
    return safe_name


def _resolve_final_video_path(filename: str) -> Path:
    safe_name = _normalize_final_video_filename(filename)

    target = project_path(settings.output_dir) / safe_name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="video not found")
    return target


def _final_video_thumb_path(filename: str) -> Path:
    safe_name = Path(filename or "").name
    stem = Path(safe_name).stem
    return project_path(settings.temp_dir) / "final_video_thumbs" / f"{stem}.jpg"


def _safe_unlink(path: Path) -> bool:
    try:
        if not path.exists():
            return False
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except Exception:
        logger.exception("Failed to delete path: %s", path)
        return False


def _delete_job_artifacts(job_id: str) -> dict[str, bool]:
    temp_job_dir = project_path(settings.temp_dir) / str(job_id or "")
    final_video_path = project_path(settings.output_dir) / f"{job_id}.mp4"
    final_thumb_path = _final_video_thumb_path(f"{job_id}.mp4")
    return {
        "temp_removed": _safe_unlink(temp_job_dir),
        "video_removed": _safe_unlink(final_video_path),
        "final_thumb_removed": _safe_unlink(final_thumb_path),
    }


def _ensure_final_video_thumbnail(filename: str) -> Path:
    video_path = _resolve_final_video_path(filename)
    thumb_path = _final_video_thumb_path(filename)

    if thumb_path.exists():
        try:
            if thumb_path.stat().st_size >= 512 and thumb_path.stat().st_mtime >= video_path.stat().st_mtime:
                return thumb_path
        except Exception:
            pass

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg not found")

    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "4",
            str(thumb_path),
        ],
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0 or not thumb_path.exists():
        raise RuntimeError((proc.stderr or "ffmpeg thumbnail failed")[:300])

    return thumb_path


def _ensure_job_clip_thumbnail(job_id: str, clip_index: int) -> Path:
    clip_path = _job_clip_path(job_id, clip_index)
    if not clip_path.exists() or not clip_path.is_file():
        raise FileNotFoundError("clip not found")

    thumb_path = _job_clip_thumb_path(job_id, clip_index)
    if thumb_path.exists():
        try:
            if thumb_path.stat().st_size >= 512 and thumb_path.stat().st_mtime >= clip_path.stat().st_mtime:
                return thumb_path
        except Exception:
            pass

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg not found")

    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(clip_path),
            "-frames:v",
            "1",
            "-q:v",
            "4",
            str(thumb_path),
        ],
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0 or not thumb_path.exists():
        raise RuntimeError((proc.stderr or "ffmpeg thumbnail failed")[:300])

    return thumb_path


@app.on_event("startup")
async def _recover_jobs_on_startup() -> None:
    resumed = resume_interrupted_jobs()
    if resumed:
        logger.info("Recovered interrupted jobs: %s", ", ".join(resumed))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"internal error: {exc}"})


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.app_env}


@app.get("/api/workspace-auth/status", response_model=WorkspaceAuthStatusResponse)
async def workspace_auth_status() -> WorkspaceAuthStatusResponse:
    return WorkspaceAuthStatusResponse(required=_workspace_password_required())


@app.post("/api/workspace-auth/login")
async def workspace_auth_login(payload: WorkspaceLoginRequest) -> dict:
    if not _workspace_password_required():
        return {"ok": True}
    if not _workspace_password_valid(payload.password):
        raise HTTPException(status_code=401, detail="invalid workspace password")
    response = JSONResponse(content={"ok": True})
    response.set_cookie(
        key=WORKSPACE_AUTH_COOKIE,
        value=_workspace_password_token(payload.password),
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return response


@app.post("/api/workspace-auth/logout")
async def workspace_auth_logout() -> dict:
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(WORKSPACE_AUTH_COOKIE, path="/")
    return response


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

    plan = await build_segment_plan(
        text=text,
        method=payload.method,
        sentences_per_segment=payload.sentences_per_segment,
        fixed_size=payload.fixed_size,
        model_id=payload.model_id,
    )

    segments = []
    for index, item in enumerate(plan.segments):
        count = 0
        if payload.method == "sentence":
            count = count_sentences(item)
        segments.append(SegmentItem(index=index, text=item, sentence_count=count))

    return SegmentTextResponse(
        segments=segments,
        total_segments=len(segments),
        total_sentences=plan.total_sentences,
        request_signature=plan.request_signature,
    )


@app.get("/api/character-reference-images")
async def get_character_reference_images(request: Request) -> dict:
    images = list_character_reference_images()
    return {"images": [item.model_dump() for item in images]}


@app.post("/api/character-reference-images/upload", response_model=CharacterImageItem)
async def upload_character_reference_image(request: Request, file: UploadFile = File(...)) -> CharacterImageItem:
    suffix = Path(file.filename or "image.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="unsupported image format")

    root = project_path(settings.character_ref_dir)
    root.mkdir(parents=True, exist_ok=True)
    stem = Path(file.filename).stem.replace(" ", "_") or "upload"
    dest = root / f"upload_{stem}_{uuid4().hex[:8]}{suffix}"
    content = await file.read()
    dest.write_bytes(content)

    return CharacterImageItem(
        path=dest.as_posix(),
        url=f"/assets/character_refs/{quote(dest.name)}",
        filename=dest.name,
    )


@app.post("/api/character-reference-images/generate", response_model=CharacterImageItem)
async def generate_character_reference_image(request: Request, payload: CreateCharacterImageRequest) -> CharacterImageItem:
    try:
        width_raw, height_raw = payload.resolution.lower().split("x")
        resolution = (max(256, int(width_raw)), max(256, int(height_raw)))
    except Exception:
        resolution = (768, 768)
    return await create_character_reference_image(payload.character_name, payload.prompt, resolution)


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


@app.post("/api/watermark/upload")
async def upload_watermark(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "watermark.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="unsupported watermark format")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty watermark file")

    root = project_path(settings.watermark_asset_dir)
    root.mkdir(parents=True, exist_ok=True)
    stem = Path(file.filename or "watermark").stem.replace(" ", "_") or "watermark"
    output = root / f"{stem}_{uuid4().hex[:8]}{suffix}"
    output.write_bytes(data)
    return {
        "path": output.as_posix(),
        "filename": output.name,
        "size": len(data),
    }


@app.get("/api/bgm/library")
async def list_bgm_library(request: Request) -> dict:
    items: list[BgmLibraryItem] = []
    for path in sorted(_bgm_root().glob("*.mp3")):
        stat = path.stat()
        items.append(
            BgmLibraryItem(
                path=path.as_posix(),
                url=f"/assets/bgm/{quote(path.name)}",
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
    status = _resolve_job_status(job_id)
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
        "fast",
        payload.novel_alias,
        payload.watermark_enabled,
        payload.watermark_type,
        payload.watermark_text,
        payload.watermark_image_path,
        payload.watermark_opacity,
    )

    base_url = str(request.base_url).rstrip("/")
    return RemixBgmResponse(
        job_id=job_id,
        status="completed",
        output_video_url=f"/api/jobs/{job_id}/video",
    )


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_video_job(request: Request, job_id: str) -> dict:
    base_url = str(request.base_url).rstrip("/")
    if not cancel_job(job_id, base_url):
        raise HTTPException(status_code=404, detail="job not found")
    return {"status": "cancel_requested", "job_id": job_id}


@app.post("/api/jobs/{job_id}/resume")
async def resume_video_job(request: Request, job_id: str) -> dict:
    base_url = str(request.base_url).rstrip("/")
    ok, code = resume_job(job_id, base_url)
    if not ok:
        if code == "not_found":
            raise HTTPException(status_code=404, detail="job not found")
        if code == "already_completed":
            raise HTTPException(status_code=409, detail="job already completed")
        raise HTTPException(status_code=409, detail="job cannot resume")
    return {"status": code, "job_id": job_id}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> dict:
    status = _resolve_job_status(job_id)
    if status and status.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="cannot delete an active job")

    file_removed = await run_in_threadpool(_delete_job_artifacts, job_id)
    job_removed = job_store.delete_job(job_id)

    removed_any = bool(job_removed or any(file_removed.values()))
    if not removed_any:
        raise HTTPException(status_code=404, detail="job not found")

    return {
        "job_id": job_id,
        "deleted": True,
        "job_removed": bool(job_removed),
        "temp_removed": bool(file_removed.get("temp_removed")),
        "video_removed": bool(file_removed.get("video_removed")),
        "final_thumb_removed": bool(file_removed.get("final_thumb_removed")),
    }


@app.get("/api/jobs")
async def list_jobs(limit: int = 100) -> dict:
    statuses = job_store.list_recent(limit=limit)
    return {"jobs": [item.model_dump() for item in statuses]}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str) -> dict:
    status = _resolve_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="job not found")
    return status.model_dump()


@app.get("/api/jobs/{job_id}/video")
async def get_job_video(job_id: str):
    status = _resolve_job_status(job_id)
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
    clip_path = _job_clip_path(job_id, clip_index)
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="clip not found")
    return FileResponse(clip_path, media_type="video/mp4", filename=clip_path.name)


@app.get("/api/jobs/{job_id}/clips/{clip_index}/thumb")
async def get_job_clip_thumbnail(job_id: str, clip_index: int):
    if clip_index < 0:
        raise HTTPException(status_code=400, detail="clip_index must be >= 0")
    try:
        thumb_path = await run_in_threadpool(_ensure_job_clip_thumbnail, job_id, clip_index)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="clip not found")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(thumb_path, media_type="image/jpeg", filename=thumb_path.name)


@app.get("/api/final-videos", response_model=FinalVideoListResponse)
async def list_final_videos(limit: int = 200) -> FinalVideoListResponse:
    safe_limit = max(1, min(int(limit or 200), 2000))
    output_root = project_path(settings.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    rows: list[tuple[float, FinalVideoItem]] = []
    for path in output_root.glob("*.mp4"):
        try:
            stat = path.stat()
            created_ts = float(getattr(stat, "st_ctime", 0.0) or 0.0)
            updated_ts = float(getattr(stat, "st_mtime", created_ts) or created_ts)
            created_at = datetime.fromtimestamp(created_ts).isoformat(timespec="seconds")
            updated_at = datetime.fromtimestamp(updated_ts).isoformat(timespec="seconds")
            safe_name = path.name
            encoded = quote(safe_name)
            rows.append(
                (
                    created_ts,
                    FinalVideoItem(
                        filename=safe_name,
                        size=int(stat.st_size),
                        created_at=created_at,
                        updated_at=updated_at,
                        video_url=f"/outputs/{encoded}",
                        thumbnail_url=f"/api/final-videos/{encoded}/thumb",
                        download_url=f"/api/final-videos/{encoded}/download",
                    ),
                )
            )
        except Exception:
            logger.exception("Failed to inspect final video file: %s", path)

    sorted_rows = sorted(rows, key=lambda item: item[0], reverse=True)
    return FinalVideoListResponse(videos=[item for _, item in sorted_rows[:safe_limit]])


@app.get("/api/final-videos/{filename}/thumb")
async def get_final_video_thumbnail(filename: str):
    try:
        thumb_path = await run_in_threadpool(_ensure_final_video_thumbnail, filename)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(thumb_path, media_type="image/jpeg", filename=thumb_path.name)


@app.get("/api/final-videos/{filename}/download")
async def download_final_video(filename: str):
    path = _resolve_final_video_path(filename)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.delete("/api/workspace/final-videos/{filename}")
async def delete_final_video(filename: str) -> dict:
    safe_name = _normalize_final_video_filename(filename)
    final_video_path = project_path(settings.output_dir) / safe_name
    job_id = Path(safe_name).stem

    status = job_store.get(job_id)
    if status and status.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="cannot delete final video for an active job")

    removed_flags = {
        "video_removed": _safe_unlink(final_video_path),
        "final_thumb_removed": _safe_unlink(_final_video_thumb_path(safe_name)),
        "temp_removed": _safe_unlink(project_path(settings.temp_dir) / job_id),
    }
    job_removed = job_store.delete_job(job_id)

    if not any(removed_flags.values()) and not job_removed:
        raise HTTPException(status_code=404, detail="final video not found")

    return {
        "filename": safe_name,
        "job_id": job_id,
        "deleted": True,
        "job_removed": bool(job_removed),
        **removed_flags,
    }
