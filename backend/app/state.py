from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from .models import JobStatus


@dataclass
class JobStore:
    jobs: dict[str, JobStatus] = field(default_factory=dict)
    cancelled: set[str] = field(default_factory=set)
    lock: Lock = field(default_factory=Lock)

    def set(self, status: JobStatus) -> None:
        with self.lock:
            self.jobs[status.job_id] = status

    def get(self, job_id: str) -> JobStatus | None:
        with self.lock:
            return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            if job_id not in self.jobs:
                return False
            self.cancelled.add(job_id)
            return True

    def is_cancelled(self, job_id: str) -> bool:
        with self.lock:
            return job_id in self.cancelled

    def clear_cancel(self, job_id: str) -> None:
        with self.lock:
            self.cancelled.discard(job_id)


job_store = JobStore()

