from __future__ import annotations

from threading import Event

import pytest

from autonavlog.web.calculation_jobs import (
    CalculationJobAlreadyActiveError,
    CalculationJobQueue,
)


def test_job_is_owner_and_session_bound() -> None:
    queue = CalculationJobQueue(workers=1, maximum_queued=2)
    try:
        job = queue.submit(
            owner_id="owner-a",
            session_token="session-a",
            task=lambda: {"state": "ok"},
        )
        assert queue.get(job.id, owner_id="owner-b", session_token="session-a") is None
        assert queue.get(job.id, owner_id="owner-a", session_token="session-b") is None
        assert queue.get(job.id, owner_id="owner-a", session_token="session-a") is job
    finally:
        queue.shutdown()


def test_queue_is_bounded_and_rejects_duplicate_session_job() -> None:
    queue = CalculationJobQueue(workers=1, maximum_queued=1)
    release = Event()
    started = Event()

    def blocked() -> dict[str, str]:
        started.set()
        release.wait(timeout=2)
        return {"state": "ok"}

    try:
        first = queue.submit(owner_id="a", session_token="s1", task=blocked)
        assert started.wait(timeout=1)
        second = queue.submit(owner_id="b", session_token="s2", task=blocked)
        with pytest.raises(CalculationJobAlreadyActiveError, match="active"):
            queue.submit(owner_id="a", session_token="s1", task=blocked)
        with pytest.raises(OverflowError, match="full"):
            queue.submit(owner_id="c", session_token="s3", task=blocked)
        assert first.status in {"preparing_weather", "calculating"}
        assert queue.queue_position(second) == 1
        snapshot = queue.snapshot(
            second.id,
            owner_id="b",
            session_token="s2",
        )
        assert snapshot is not None
        assert snapshot.status == "queued"
        assert snapshot.queue_position == 1
    finally:
        release.set()
        queue.shutdown()
