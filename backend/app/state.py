from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from .models import JobStatus


@dataclass
class JobStore:
    jobs: dict[str, JobStatus]
    lock: Lock

    def set(self, status: JobStatus) -> None:
        with self.lock:
            self.jobs[status.job_id] = status

    def get(self, job_id: str) -> JobStatus | None:
        with self.lock:
            return self.jobs.get(job_id)


job_store = JobStore(jobs={}, lock=Lock())

