from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from threading import Event
from time import monotonic
from zoneinfo import ZoneInfo

import httpx
import pytest
from test_issue_94_facade_persistence import OWNER, _new_project

from autonavlog.web.app import create_app
from autonavlog.web.migration_remote import GoogleIdentity
from autonavlog.web.runtime import WebRuntimeConfig, build_web_application

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def waiting_app(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONAVLOG_FIREBASE_PROJECT_ID", "diagnostic-project")
    monkeypatch.setenv("AUTONAVLOG_FIREBASE_API_KEY", "synthetic-key")
    monkeypatch.setenv("AUTONAVLOG_NAVMATE_URL", "https://navmate.example")
    web = build_web_application(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path,
            weather_mode="fake",
            trusted_local_identity=OWNER,
        )
    )
    session, _ = _new_project(web)
    app = create_app(web_application=web)
    yield app, web, session
    app.state.calculation_jobs.shutdown()


@pytest.mark.anyio
async def test_migration_enabled_long_calculation_keeps_progress_responsive(
    waiting_app, monkeypatch
):
    app, web, session = waiting_app
    session.project.flight_date = date(2026, 11, 2)
    session.project.planned_departure_time_jst = datetime(
        2026, 11, 2, 15, tzinfo=ZoneInfo("Asia/Tokyo")
    )
    session.project.weather_mode = "FORECAST"
    session.project.ftd_weather = None
    session.project.selected_forecast_run_id = None
    started, release = Event(), Event()
    original = session.weather_provider.prepare_run

    def slow(*args, **kwargs):
        started.set()
        assert release.wait(40), "bounded test release"
        return original(*args, **kwargs)

    monkeypatch.setattr(session.weather_provider, "prepare_run", slow)
    headers = {"X-AutoNavLog-Session": session.token}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test", headers=headers
    ) as client:
        try:
            created = await client.post("/api/calculation-jobs")
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            assert await asyncio.to_thread(started.wait, 2)
            start = monotonic()
            for path in [
                f"/api/calculation-jobs/{job_id}",
                "/api/calculation-jobs/active",
                "/api/account-link",
            ]:
                response = await asyncio.wait_for(client.get(path), timeout=1)
                assert response.status_code == 200
            assert monotonic() - start < 1
            assert (await client.post("/api/calculation-jobs")).status_code == 409
            assert (await client.delete("/api/application-session")).status_code == 409
            payload = {
                "flight_date": "2099-08-10",
                "departure_time_jst": "15:00",
                "total_usable_fuel_gal": 90,
                "default_variation_deg_east": 8,
            }
            for method, path, body in [
                ("PUT", "/api/project", payload),
                ("POST", "/api/project/recalculate", payload),
                ("POST", "/api/projects/save", {"name": "synthetic"}),
            ]:
                response = await asyncio.wait_for(
                    client.request(method, path, json=body), timeout=1
                )
                assert response.status_code == 409
                assert response.json()["error"]["code"] == "OPERATION_IN_PROGRESS"
            await asyncio.sleep(31)
            response = await asyncio.wait_for(
                client.get(f"/api/calculation-jobs/{job_id}"), timeout=1
            )
            assert response.status_code == 200
            assert response.json()["status"] not in {"succeeded", "failed"}
        finally:
            release.set()
        for _ in range(100):
            response = await client.get(f"/api/calculation-jobs/{job_id}")
            if response.json()["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(0.05)
        assert response.json()["status"] == "succeeded"
        assert response.json()["state"]["outcome"] is not None
        assert (await client.get("/api/calculation-jobs/active")).status_code == 204


@pytest.mark.anyio
async def test_job_reads_preserve_session_and_migration_boundaries(waiting_app):
    app, web, session = waiting_app
    other = web.create_session(OWNER)
    job = app.state.calculation_jobs.submit(
        owner_id=session.owner_id,
        session_token=session.token,
        task=lambda report: {},
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        path = f"/api/calculation-jobs/{job.id}"
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get(path, headers={"X-AutoNavLog-Session": other.token})
        ).status_code == 404
        migration = app.state.legacy_migration
        migration.link(OWNER, GoogleIdentity("synthetic", "synthetic-account"))
        from autonavlog.storage.repository import owner_storage_key

        with migration.db() as db:
            db.execute(
                "UPDATE links SET state='MIGRATING', expires=9999999999 WHERE owner=?",
                (owner_storage_key(OWNER),),
            )
        response = await client.get(path, headers={"X-AutoNavLog-Session": session.token})
        assert response.status_code == 423
        with migration.db() as db:
            db.execute("UPDATE links SET expires=0")
        assert migration.read_status(owner_storage_key(OWNER))["state"] == "LINKED"
        # Effective expiry on reads never performs lease-repair writes.
        with migration.db() as db:
            assert db.execute("SELECT state FROM links").fetchone()[0] == "MIGRATING"
        assert (
            await client.get(path, headers={"X-AutoNavLog-Session": session.token})
        ).status_code == 200


@pytest.mark.anyio
async def test_session_lock_conflict_is_fast(waiting_app):
    app, web, session = waiting_app
    held, release = Event(), Event()

    def hold():
        with session.lock:
            held.set()
            release.wait(5)

    holder = asyncio.create_task(asyncio.to_thread(hold))
    try:
        assert await asyncio.to_thread(held.wait, 1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
            headers={"X-AutoNavLog-Session": session.token},
        ) as client:
            response = await asyncio.wait_for(
                client.post("/api/projects/save", json={"name": "test"}), 1
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "OPERATION_IN_PROGRESS"
    finally:
        release.set()
        await holder
