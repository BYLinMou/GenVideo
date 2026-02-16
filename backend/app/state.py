from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .config import project_path, settings
from .models import GenerateVideoRequest, JobStatus


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobStore:
    db_path: Path = field(default_factory=lambda: project_path(settings.jobs_db_path))
    lock: Lock = field(default_factory=Lock)

    def __post_init__(self) -> None:
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _ensure_jobs_column(self, conn: sqlite3.Connection, column_name: str, column_ddl: str) -> None:
        try:
            rows = conn.execute("PRAGMA table_info(jobs)").fetchall()
            existing = {str(row[1]) for row in rows}
            if column_name in existing:
                return
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column_name} {column_ddl}")
        except Exception:
            logger.exception("Failed to ensure jobs column: %s", column_name)

    def _init_db(self) -> None:
        with self.lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        progress REAL NOT NULL,
                        step TEXT NOT NULL,
                        message TEXT NOT NULL,
                        current_segment INTEGER NOT NULL DEFAULT 0,
                        total_segments INTEGER NOT NULL DEFAULT 0,
                        output_video_url TEXT,
                        output_video_path TEXT,
                        clip_count INTEGER NOT NULL DEFAULT 0,
                        clip_preview_urls_json TEXT NOT NULL DEFAULT '[]',
                        clip_image_sources_json TEXT NOT NULL DEFAULT '[]',
                        image_source_report_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                self._ensure_jobs_column(conn, "image_source_report_json", "TEXT")
                self._ensure_jobs_column(conn, "clip_image_sources_json", "TEXT NOT NULL DEFAULT '[]'")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS job_payloads (
                        job_id TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL,
                        base_url TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS job_cancel_flags (
                        job_id TEXT PRIMARY KEY,
                        cancelled_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

    def _build_preview_urls(self, job_id: str, clip_count: int) -> list[str]:
        limit = max(0, int(settings.job_clip_preview_limit or 0))
        preview_count = min(max(0, int(clip_count or 0)), limit)
        return [f"/api/jobs/{job_id}/clips/{index}" for index in range(preview_count)]

    def _row_to_status(self, row: sqlite3.Row) -> JobStatus:
        clip_count = max(0, int(row["clip_count"] or 0))
        previews = self._build_preview_urls(str(row["job_id"]), clip_count)
        clip_image_sources: list[str] = []
        raw_clip_sources = row["clip_image_sources_json"] if "clip_image_sources_json" in row.keys() else None
        if raw_clip_sources:
            try:
                parsed = json.loads(str(raw_clip_sources))
                if isinstance(parsed, list):
                    clip_image_sources = [str(item or "") for item in parsed]
            except Exception:
                clip_image_sources = []

        image_source_report: dict[str, object] | None = None
        raw_report = row["image_source_report_json"] if "image_source_report_json" in row.keys() else None
        if raw_report:
            try:
                parsed = json.loads(str(raw_report))
                if isinstance(parsed, dict):
                    image_source_report = parsed
            except Exception:
                image_source_report = None
        return JobStatus(
            job_id=str(row["job_id"]),
            status=str(row["status"]),
            progress=float(row["progress"] or 0.0),
            step=str(row["step"] or ""),
            message=str(row["message"] or ""),
            current_segment=max(0, int(row["current_segment"] or 0)),
            total_segments=max(0, int(row["total_segments"] or 0)),
            output_video_url=str(row["output_video_url"]) if row["output_video_url"] else None,
            output_video_path=str(row["output_video_path"]) if row["output_video_path"] else None,
            clip_count=clip_count,
            clip_preview_urls=previews,
            clip_image_sources=clip_image_sources,
            image_source_report=image_source_report,
            created_at=str(row["created_at"]) if "created_at" in row.keys() and row["created_at"] else None,
            updated_at=str(row["updated_at"]) if "updated_at" in row.keys() and row["updated_at"] else None,
        )

    def set(self, status: JobStatus) -> None:
        with self.lock:
            now = _now_iso()
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, status, progress, step, message,
                        current_segment, total_segments,
                        output_video_url, output_video_path,
                        clip_count, clip_preview_urls_json, clip_image_sources_json, image_source_report_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        status=excluded.status,
                        progress=excluded.progress,
                        step=excluded.step,
                        message=excluded.message,
                        current_segment=excluded.current_segment,
                        total_segments=excluded.total_segments,
                        output_video_url=excluded.output_video_url,
                        output_video_path=excluded.output_video_path,
                        clip_count=excluded.clip_count,
                        clip_preview_urls_json=excluded.clip_preview_urls_json,
                        clip_image_sources_json=excluded.clip_image_sources_json,
                        image_source_report_json=excluded.image_source_report_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        status.job_id,
                        status.status,
                        float(status.progress or 0.0),
                        status.step or "",
                        status.message or "",
                        max(0, int(status.current_segment or 0)),
                        max(0, int(status.total_segments or 0)),
                        status.output_video_url,
                        status.output_video_path,
                        max(0, int(status.clip_count or 0)),
                        "[]",
                        json.dumps(status.clip_image_sources, ensure_ascii=False) if status.clip_image_sources else "[]",
                        json.dumps(status.image_source_report, ensure_ascii=False) if status.image_source_report else None,
                        now,
                        now,
                    ),
                )
                conn.commit()

    def get(self, job_id: str) -> JobStatus | None:
        with self.lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        job_id, status, progress, step, message,
                        current_segment, total_segments,
                        output_video_url, output_video_path,
                        clip_count, clip_preview_urls_json, clip_image_sources_json, image_source_report_json,
                        created_at, updated_at
                    FROM jobs
                    WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
            if not row:
                return None
            return self._row_to_status(row)

    def list_recent(self, limit: int = 100) -> list[JobStatus]:
        safe_limit = max(1, min(int(limit or 100), 500))
        with self.lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        job_id, status, progress, step, message,
                        current_segment, total_segments,
                        output_video_url, output_video_path,
                        clip_count, clip_preview_urls_json, clip_image_sources_json, image_source_report_json,
                        created_at, updated_at
                    FROM jobs
                    ORDER BY created_at DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
        return [self._row_to_status(row) for row in rows]

    def save_payload(self, job_id: str, payload: GenerateVideoRequest, base_url: str) -> None:
        with self.lock:
            now = _now_iso()
            payload_json = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO job_payloads (job_id, payload_json, base_url, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        payload_json=excluded.payload_json,
                        base_url=excluded.base_url,
                        updated_at=excluded.updated_at
                    """,
                    (job_id, payload_json, base_url or "", now),
                )
                conn.commit()

    def load_payload(self, job_id: str) -> tuple[GenerateVideoRequest, str] | None:
        with self.lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT payload_json, base_url FROM job_payloads WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
        if not row:
            return None

        try:
            payload_data = json.loads(str(row["payload_json"] or "{}"))
            payload = GenerateVideoRequest.model_validate(payload_data)
            return payload, str(row["base_url"] or "")
        except Exception:
            logger.exception("Failed to deserialize job payload: %s", job_id)
            return None

    def list_incomplete_job_ids(self) -> list[str]:
        with self.lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT job_id
                    FROM jobs
                    WHERE status IN ('queued', 'running')
                    ORDER BY updated_at ASC
                    """
                ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            with self._connect() as conn:
                exists = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
                if not exists:
                    return False
                conn.execute(
                    "INSERT OR REPLACE INTO job_cancel_flags (job_id, cancelled_at) VALUES (?, ?)",
                    (job_id, _now_iso()),
                )
                conn.commit()
                return True

    def is_cancelled(self, job_id: str) -> bool:
        with self.lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM job_cancel_flags WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
            return bool(row)

    def clear_cancel(self, job_id: str) -> None:
        with self.lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM job_cancel_flags WHERE job_id = ?", (job_id,))
                conn.commit()

    def delete_job(self, job_id: str) -> bool:
        with self.lock:
            with self._connect() as conn:
                exists = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
                conn.execute("DELETE FROM job_payloads WHERE job_id = ?", (job_id,))
                conn.execute("DELETE FROM job_cancel_flags WHERE job_id = ?", (job_id,))
                conn.commit()
                return bool(exists)


job_store = JobStore()
