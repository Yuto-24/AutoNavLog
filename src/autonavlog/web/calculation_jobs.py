from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from secrets import token_urlsafe
from threading import BoundedSemaphore, RLock
from typing import Any, Literal

JobStatus = Literal[
    "queued",
    "preparing_weather",
    "calculating",
    "succeeded",
    "failed",
]


@dataclass
class CalculationJob:
    id: str
    owner_id: str
    session_token: str
    status: JobStatus = "queued"
    created_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class CalculationJobQueue:
    """Bounded in-process calculation queue with session/owner isolation."""

    def __init__(self, *, workers: int = 4, maximum_queued: int = 128) -> None:
        if workers < 1 or maximum_queued < 1:
            raise ValueError("workers and maximum_queued must be positive")
        self.workers = workers
        self.maximum_queued = maximum_queued
        self._executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="autonavlog-calculation",
        )
        self._capacity = BoundedSemaphore(workers + maximum_queued)
        self._jobs: dict[str, CalculationJob] = {}
        self._lock = RLock()

    def submit(
        self,
        *,
        owner_id: str,
        session_token: str,
        task: Callable[[], dict[str, Any]],
    ) -> CalculationJob:
        with self._lock:
            if any(
                item.session_token == session_token
                and item.status not in {"succeeded", "failed"}
                for item in self._jobs.values()
            ):
                raise OverflowError("session already has an active calculation job")
            if not self._capacity.acquire(blocking=False):
                raise OverflowError("calculation queue is full")
            job = CalculationJob(
                id=token_urlsafe(24),
                owner_id=owner_id,
                session_token=session_token,
            )
            self._jobs[job.id] = job
            self._prune()
        try:
            self._executor.submit(self._run, job.id, task)
        except BaseException:
            self._capacity.release()
            with self._lock:
                self._jobs.pop(job.id, None)
            raise
        return job

    def _run(self, job_id: str, task: Callable[[], dict[str, Any]]) -> None:
        try:
            self._set_status(job_id, "preparing_weather")
            self._set_status(job_id, "calculating")
            result = task()
        except Exception as error:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.error = {
                    "code": str(getattr(error, "code", "CALCULATION_JOB_FAILED")),
                    "message": str(error),
                    "status": int(getattr(error, "status_code", 500)),
                }
                job.updated_at_utc = datetime.now(timezone.utc)
        else:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "succeeded"
                job.result = result
                job.updated_at_utc = datetime.now(timezone.utc)
        finally:
            self._capacity.release()

    def _set_status(self, job_id: str, status: JobStatus) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = status
            job.updated_at_utc = datetime.now(timezone.utc)

    def get(self, job_id: str, *, owner_id: str, session_token: str) -> CalculationJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner_id != owner_id or job.session_token != session_token:
                return None
            return job

    def queue_position(self, job: CalculationJob) -> int | None:
        if job.status != "queued":
            return None
        with self._lock:
            queued = sorted(
                (item for item in self._jobs.values() if item.status == "queued"),
                key=lambda item: item.created_at_utc,
            )
            return next(
                (index for index, item in enumerate(queued, start=1) if item.id == job.id),
                None,
            )

    def _prune(self) -> None:
        maximum_history = self.maximum_queued * 2
        if len(self._jobs) <= maximum_history:
            return
        finished = sorted(
            (
                item
                for item in self._jobs.values()
                if item.status in {"succeeded", "failed"}
            ),
            key=lambda item: item.updated_at_utc,
        )
        for item in finished[: len(self._jobs) - maximum_history]:
            self._jobs.pop(item.id, None)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)
