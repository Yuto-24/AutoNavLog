from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from autonavlog.forecast_backup import BackupRequest, calculation_contract
from autonavlog.local import LocalApplication
from autonavlog.weather.local_msm import LocalMsmWeather
from autonavlog.web.facade import WebApplicationError
from autonavlog.web.forecast_backup import BackupJobs, calculator, install_backup_routes
from scripts.local_reference import reference_state

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
FEED = ROOT / "tests/fixtures/msm-portable"


@pytest.fixture(scope="module")
def reference():
    return reference_state(feed=str(FEED))


@pytest.fixture
def local(reference):
    value = LocalApplication(DATA)
    value.dispatch("bootstrap", reference["workingRecovery"])
    yield value
    value.close()


@pytest.fixture
def backup_request(local):
    prepared = local.prepare_forecast_backup({"reason": "WEATHER_CATALOG_EXPIRED"})
    return BackupRequest(**prepared.model_dump(), request_id=uuid4(), issued_at=datetime.now(UTC))


def wait(jobs, owner, backup_request):
    deadline = time.monotonic() + 15
    while True:
        state = jobs.get(owner, str(backup_request.request_id))
        if state["status"] != "running":
            return state
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_request_strips_recovery_and_identity(local):
    local.session.project.metadata["web_owner_id"] = "private-owner"
    local.session.project.metadata["unrelated"] = "private-data"
    local.session.project.pilot_name = "private-pilot"
    value = local.prepare_forecast_backup({"reason": "WEATHER_CATALOG_EXPIRED"}).model_dump_json()
    assert "private-" not in value
    assert "durableToken" not in value and "repositoryRecovery" not in value


def test_contract_detects_data_and_engine_mismatch(backup_request):
    from autonavlog.forecast_backup import BackupContract

    contract = calculation_contract(DATA)
    assert contract == backup_request.contract
    for field in ("engine_sha256", "data_sha256"):
        different = BackupContract(**{**contract.model_dump(), field: "0" * 64})
        assert different != contract


def test_request_rejects_non_weather_reason(backup_request):
    with pytest.raises(ValueError):
        BackupRequest.model_validate({**backup_request.model_dump(), "reason": "HTTP_500"})


def test_response_loss_and_token_retry_do_not_duplicate(tmp_path, backup_request):
    entered, release = Event(), Event()
    calls = []

    def compute(value):
        calls.append(value.request_id)
        entered.set()
        assert release.wait(5)
        raise RuntimeError("sensitive diagnostic must never escape")

    jobs = BackupJobs(tmp_path / "jobs.sqlite3", compute)
    try:
        first = jobs.submit("owner", backup_request)
        assert entered.wait(5)
        for _ in range(4):
            assert jobs.submit("owner", backup_request)["request_id"] == first["request_id"]
        assert len(calls) == 1
        release.set()
        result = wait(jobs, "owner", backup_request)
        assert result["error"] == "FORECAST_BACKUP_FAILED"
        assert "sensitive" not in json.dumps(result)
    finally:
        release.set()
        jobs.close()


def test_owner_isolation_conflict_and_capacity(tmp_path, backup_request):
    release = Event()
    jobs = BackupJobs(tmp_path / "jobs.sqlite3", lambda _: release.wait(5), maximum=2, per_owner=1)
    try:
        jobs.submit("a", backup_request)
        with pytest.raises(WebApplicationError) as error:
            jobs.get("b", str(backup_request.request_id))
        assert error.value.status_code == 404
        changed = backup_request.model_copy(update={"reason": "WEATHER_COMMUNICATION_FAILED"})
        with pytest.raises(WebApplicationError) as error:
            jobs.submit("a", changed)
        assert error.value.status_code == 409
        with pytest.raises(WebApplicationError) as error:
            jobs.submit("a", backup_request.model_copy(update={"request_id": uuid4()}))
        assert error.value.status_code == 429
        jobs.submit("b", backup_request)
        with pytest.raises(WebApplicationError) as error:
            jobs.submit("c", backup_request)
        assert error.value.status_code == 429
    finally:
        release.set()
        jobs.close()


def test_restart_does_not_repeat_unknown_job(tmp_path, backup_request):
    path = tmp_path / "jobs.sqlite3"
    first = BackupJobs(path, lambda _: None)
    try:
        import hashlib

        from autonavlog.forecast_backup import canonical_json

        digest = hashlib.sha256(
            canonical_json(backup_request.model_dump(mode="json")).encode()
        ).hexdigest()
        with first.connect() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",
                (
                    "a",
                    str(backup_request.request_id),
                    digest,
                    time.time() - 601,
                    "running",
                    None,
                    None,
                ),
            )
    finally:
        first.close()
    calls = []
    second = BackupJobs(path, lambda _: calls.append(1))
    try:
        assert second.submit("a", backup_request)["error"] == "FORECAST_BACKUP_INTERRUPTED"
        assert calls == []
    finally:
        second.close()


def test_expired_request_cannot_recreate_pruned_job(tmp_path, backup_request):
    jobs = BackupJobs(tmp_path / "jobs.sqlite3", lambda _: None)
    try:
        expired = backup_request.model_copy(
            update={"issued_at": datetime.now(UTC) - timedelta(days=2)}
        )
        with pytest.raises(WebApplicationError) as error:
            jobs.submit("a", expired)
        assert error.value.status_code == 410
        with jobs.connect() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",
                (
                    "a",
                    str(backup_request.request_id),
                    "x",
                    time.time() - 86401,
                    "failed",
                    None,
                    "safe",
                ),
            )
        with pytest.raises(WebApplicationError):
            jobs.get("a", str(backup_request.request_id))
        with jobs.connect() as db:
            assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        jobs.close()


@pytest.mark.parametrize("rounding_mode", [False, True])
def test_matching_server_calculation_and_local_adoption(
    tmp_path,
    local,
    backup_request,
    rounding_mode,
):
    prepared = local.prepare_forecast_backup(
        {
            "reason": "WEATHER_CATALOG_EXPIRED",
            "rounding_mode": rounding_mode,
        }
    )
    backup_request = BackupRequest(
        **prepared.model_dump(),
        request_id=uuid4(),
        issued_at=datetime.now(UTC),
    )

    def weather():
        portable = LocalMsmWeather()
        catalog = json.loads((FEED / "catalog.json").read_text())
        catalog.update(
            generated_at=datetime.now(UTC).isoformat(),
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        )
        requirement = local.session.calculation_service.forecast_service.build_initial_requirement(
            backup_request.project
        )
        asset = json.loads(portable.plan(backup_request.project, requirement, json.dumps(catalog)))
        portable.accept((FEED / asset["file"]).read_bytes(), asset["sha256"])
        return portable.provider

    jobs = BackupJobs(
        tmp_path / "jobs.sqlite3", calculator(DATA, weather, local.app.destination_wind_provider)
    )
    app = FastAPI()

    @app.exception_handler(WebApplicationError)
    async def error_handler(_request, error):
        return JSONResponse({"error": {"code": error.code}}, status_code=error.status_code)

    import hashlib

    install_backup_routes(app, jobs, backup_request.contract, "http://localhost:4202")
    headers = {
        "Origin": "http://localhost:4202",
        "X-AutoNavLog-Request": "a" * 64,
    }
    guest = hashlib.sha256(("a" * 64).encode()).hexdigest()
    try:
        with TestClient(app) as client:
            url = "/api/forecast-backup/jobs/" + str(backup_request.request_id)
            response = client.put(url, json=backup_request.model_dump(mode="json"), headers=headers)
            assert response.status_code == 202, response.text
            state = wait(jobs, guest, backup_request)
            assert state["status"] == "succeeded", state
            result = local.adopt_forecast_backup(
                {"request": backup_request.model_dump(mode="json"), "result": state["result"]}
            )
            assert result["readiness"]["calculationIsCurrent"]
            assert "FORECAST_BACKUP_USED" in [item["code"] for item in result["outcome"]["issues"]]
            for field in ("engine_sha256", "data_sha256"):
                bad = backup_request.model_dump(mode="json")
                bad["contract"][field] = "0" * 64
                assert client.put(url, json=bad, headers=headers).status_code == 409
            assert client.get(url, headers={"Origin": headers["Origin"]}).status_code == 404
    finally:
        jobs.close()


def test_result_integrity_and_stale_input_do_not_replace_last_calculation(local, backup_request):
    import hashlib

    from autonavlog.forecast_backup import BackupResult, canonical_json

    last = local.session.last_calculation
    assert last is not None
    result = BackupResult(
        contract=backup_request.contract,
        request_id=backup_request.request_id,
        input_identity=backup_request.input_identity,
        rounding_mode=backup_request.rounding_mode,
        refresh_available=False,
        calculation=last,
        calculation_sha256=hashlib.sha256(
            canonical_json(last.model_dump(mode="json")).encode()
        ).hexdigest(),
    ).model_dump(mode="json")
    original = last.model_dump_json()
    corrupted = json.loads(json.dumps(result))
    corrupted["calculation"]["project"]["name"] = "changed in transit"
    with pytest.raises(ValueError):
        local.adopt_forecast_backup(
            {"request": backup_request.model_dump(mode="json"), "result": corrupted}
        )
    assert local.session.last_calculation.model_dump_json() == original
    for field in ("engine_sha256", "data_sha256"):
        incompatible = json.loads(json.dumps(result))
        incompatible["contract"][field] = "0" * 64
        with pytest.raises(WebApplicationError) as error:
            local.adopt_forecast_backup(
                {"request": backup_request.model_dump(mode="json"), "result": incompatible}
            )
        assert error.value.code == "FORECAST_BACKUP_INCOMPATIBLE"
        assert local.session.last_calculation.model_dump_json() == original
    local.session.project.total_usable_fuel_gal += 1
    with pytest.raises(WebApplicationError) as error:
        local.adopt_forecast_backup(
            {"request": backup_request.model_dump(mode="json"), "result": result}
        )
    assert error.value.code == "FORECAST_BACKUP_STALE"
    assert local.session.last_calculation.model_dump_json() == original


def test_large_result_is_not_stored(tmp_path, backup_request):
    class Result:
        def model_dump_json(self):
            return "x" * (2 * 1024 * 1024 + 1)

    jobs = BackupJobs(tmp_path / "jobs.sqlite3", lambda _: Result())
    try:
        jobs.submit("owner", backup_request)
        assert wait(jobs, "owner", backup_request)["error"] == "FORECAST_BACKUP_FAILED"
        with jobs.connect() as db:
            assert db.execute("SELECT result FROM jobs").fetchone()[0] is None
    finally:
        jobs.close()


def test_final_provider_requirement_and_actual_query_are_checked():
    from types import SimpleNamespace

    from autonavlog.domain.enums import Availability
    from autonavlog.weather.complete_forecast import CompleteForecastProvider
    from autonavlog.weather.local_msm import LocalWeatherError

    calls = []

    class Provider:
        def inspect_run_status(self, run, requirement):
            return SimpleNamespace(selected_run_covers_requirement=True)

        def prepare_run(self, run, requirement):
            calls.append(requirement)
            if requirement == "final":
                raise LocalWeatherError("WEATHER_PREPARED_UNAVAILABLE", "missing final time")

        def query_batch(self, run, requests):
            return [SimpleNamespace(availability=Availability.UNAVAILABLE)]

    provider = CompleteForecastProvider(Provider())
    with pytest.raises(LocalWeatherError):
        provider.inspect_run_status("run", "final")
    assert calls == ["final"]
    assert provider.failure_code == "WEATHER_PREPARED_UNAVAILABLE"
    with pytest.raises(LocalWeatherError):
        provider.query_batch("run", [])


def result_for(backup_request, last):
    import hashlib

    from autonavlog.forecast_backup import BackupResult, canonical_json

    return BackupResult(
        contract=backup_request.contract,
        request_id=backup_request.request_id,
        input_identity=backup_request.input_identity,
        rounding_mode=backup_request.rounding_mode,
        refresh_available=False,
        calculation=last,
        calculation_sha256=hashlib.sha256(
            canonical_json(last.model_dump(mode="json")).encode()
        ).hexdigest(),
    ).model_dump(mode="json")


def test_later_rounding_or_input_operation_invalidates_response(local, backup_request):
    before = local.session.last_calculation.model_dump_json()
    result = result_for(backup_request, local.session.last_calculation)
    local.session.rounding_mode = not local.session.rounding_mode
    with pytest.raises(WebApplicationError) as error:
        local.adopt_forecast_backup(
            {"request": backup_request.model_dump(mode="json"), "result": result}
        )
    assert error.value.code == "FORECAST_BACKUP_STALE"
    assert local.session.last_calculation.model_dump_json() == before
    local.session.rounding_mode = backup_request.rounding_mode
    recovery = json.loads(local.dispatch("state"))["workingRecovery"]
    local.dispatch("bootstrap", recovery)
    with pytest.raises(WebApplicationError) as error:
        local.adopt_forecast_backup(
            {"request": backup_request.model_dump(mode="json"), "result": result}
        )
    assert error.value.code == "FORECAST_BACKUP_STALE"


def test_no_refresh_response_cannot_change_fixed_run(local):
    before = local.session.last_calculation.model_dump_json()
    latest = local.session.last_calculation
    local.session.project.selected_forecast_run_id = "20260915180000"
    prepared = local.prepare_forecast_backup({"reason": "WEATHER_PREPARED_UNAVAILABLE"})
    backup_request = BackupRequest(
        **prepared.model_dump(),
        request_id=uuid4(),
        issued_at=datetime.now(UTC),
    )
    with pytest.raises(WebApplicationError) as error:
        local.adopt_forecast_backup(
            {
                "request": backup_request.model_dump(mode="json"),
                "result": result_for(backup_request, latest),
            }
        )
    assert error.value.code == "FORECAST_BACKUP_INVALID"
    assert local.session.project.selected_forecast_run_id == "20260915180000"
    assert local.session.last_calculation.model_dump_json() == before


def test_expired_queued_job_never_begins_compute(tmp_path, backup_request):
    release, started = Event(), Event()
    calls = []

    def compute(value):
        calls.append(value.request_id)
        if len(calls) == 2:
            started.set()
        assert release.wait(5)
        raise RuntimeError("fixture")

    jobs = BackupJobs(tmp_path / "jobs.sqlite3", compute)
    queued = backup_request.model_copy(update={"request_id": uuid4()})
    try:
        jobs.submit("owner", backup_request)
        jobs.submit("owner", backup_request.model_copy(update={"request_id": uuid4()}))
        assert started.wait(5)
        jobs.submit("owner", queued)
        with jobs.connect() as db:
            db.execute(
                "UPDATE jobs SET created=? WHERE id=?", (time.time() - 601, str(queued.request_id))
            )
        assert jobs.get("owner", str(queued.request_id))["error"] == "FORECAST_BACKUP_INTERRUPTED"
    finally:
        release.set()
        jobs.close()
    assert queued.request_id not in calls


@pytest.mark.parametrize("polled", [False, True])
def test_late_completion_rejected_independently_of_polling(tmp_path, backup_request, polled):
    release, started = Event(), Event()

    class Result:
        def model_dump_json(self):
            return "{}"

    def compute(_):
        started.set()
        assert release.wait(5)
        return Result()

    jobs = BackupJobs(tmp_path / "jobs.sqlite3", compute)
    try:
        jobs.submit("owner", backup_request)
        assert started.wait(5)
        with jobs.connect() as db:
            db.execute(
                "UPDATE jobs SET created=? WHERE id=?",
                (time.time() - 601, str(backup_request.request_id)),
            )
        if polled:
            assert jobs.get("owner", str(backup_request.request_id))["status"] == "failed"
    finally:
        release.set()
        jobs.close()
    state = jobs.get("owner", str(backup_request.request_id))
    assert state["error"] == "FORECAST_BACKUP_INTERRUPTED"
    assert state["result"] is None


@pytest.fixture
def guest_api(tmp_path, backup_request):
    jobs = BackupJobs(tmp_path / "guest.sqlite3", lambda _: (_ for _ in ()).throw(ValueError()))
    app = FastAPI()

    @app.exception_handler(WebApplicationError)
    async def error_handler(_request, error):
        return JSONResponse({"error": {"code": error.code}}, status_code=error.status_code)

    install_backup_routes(app, jobs, backup_request.contract, "http://localhost:4202")
    with TestClient(app) as client:
        yield client, jobs
    jobs.close()


def test_guest_capability_separation_and_transport_limits(guest_api, backup_request):
    client, jobs = guest_api
    url = "/api/forecast-backup/jobs/" + str(backup_request.request_id)
    headers = {"Origin": "http://localhost:4202", "X-AutoNavLog-Request": "a" * 64}
    assert (
        client.put(url, json=backup_request.model_dump(mode="json"), headers=headers).status_code
        == 202
    )
    assert client.get(url, headers={**headers, "X-AutoNavLog-Request": "b" * 64}).status_code == 404
    assert client.get(url, headers={**headers, "X-AutoNavLog-Request": "short"}).status_code == 404
    assert client.get(url, headers={**headers, "Origin": "https://evil.example"}).status_code == 403
    assert client.put(url, content=b"x" * (256 * 1024 + 1), headers=headers).status_code == 413
    preflight = client.options(url, headers={"Origin": headers["Origin"]})
    assert preflight.headers["access-control-allow-methods"] == "GET, PUT, OPTIONS"
    assert "Authorization" not in preflight.headers["access-control-allow-headers"]
    with jobs.connect() as db:
        stored = " ".join(str(tuple(row)) for row in db.execute("SELECT * FROM jobs"))
        assert headers["X-AutoNavLog-Request"] not in stored
        assert "testclient" not in stored


def test_request_frequency_is_independent_of_capability(guest_api, backup_request):
    client, jobs = guest_api
    url = "/api/forecast-backup/jobs/" + str(backup_request.request_id)
    for index in range(120):
        headers = {"Origin": "http://localhost:4202", "X-AutoNavLog-Request": f"{index:064x}"}
        assert client.get(url, headers=headers).status_code == 404
    assert client.get(url, headers=headers).status_code == 429
    with jobs.connect() as db:
        assert db.execute("SELECT count(*) FROM rates").fetchone()[0] == 1


def test_per_client_concurrency_and_global_queue_are_distinct(tmp_path, backup_request):
    release = Event()
    jobs = BackupJobs(tmp_path / "guest.sqlite3", lambda _: release.wait(10))
    try:
        for index in range(8):
            request = backup_request.model_copy(update={"request_id": uuid4()})
            jobs.submit(str(index), request, f"client-{index // 2}")
            if index == 1:
                with pytest.raises(WebApplicationError) as error:
                    jobs.submit("other-capability", backup_request, "client-0")
                assert error.value.status_code == 429
        with pytest.raises(WebApplicationError) as error:
            jobs.submit("global", backup_request, "new-client")
        assert error.value.status_code == 429
    finally:
        release.set()
        jobs.close()


def test_per_client_frequency_and_retention(tmp_path, backup_request):
    jobs = BackupJobs(tmp_path / "guest.sqlite3", lambda _: None)
    try:
        for index in range(6):
            request = backup_request.model_copy(update={"request_id": uuid4()})
            jobs.submit(str(index), request, "one-client")
            wait(jobs, str(index), request)
        with pytest.raises(WebApplicationError) as error:
            jobs.submit("new-capability", backup_request, "one-client")
        assert error.value.status_code == 429
        # Another client is not blocked by this client's frequency budget.
        jobs.submit("other", backup_request, "other-client")
        wait(jobs, "other", backup_request)
        with jobs.connect() as db:
            db.execute("UPDATE admissions SET created=?", (time.time() - 86401,))
        request = backup_request.model_copy(update={"request_id": uuid4()})
        jobs.submit("fresh", request, "one-client")
        with jobs.connect() as db:
            assert db.execute("SELECT count(*) FROM admissions").fetchone()[0] == 1
    finally:
        jobs.close()


def test_complexity_and_private_metadata_rejected_before_job(guest_api, backup_request):
    client, jobs = guest_api
    url = "/api/forecast-backup/jobs/" + str(backup_request.request_id)
    headers = {"Origin": "http://localhost:4202", "X-AutoNavLog-Request": "a" * 64}
    for change in ({"tgl_count": 21}, {"pilot_name": "private"}, {"metadata": {"owner": "other"}}):
        body = backup_request.model_dump(mode="json")
        body["project"].update(change)
        assert client.put(url, json=body, headers=headers).status_code == 422
    with jobs.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_invalid_input_does_not_reach_weather(local):
    local.session.project.sections = []

    class NoWeather:
        def __getattr__(self, _name):
            raise AssertionError("invalid input reached weather")

    local.session.weather_provider = NoWeather()
    with pytest.raises(WebApplicationError):
        local.prepare_forecast_backup({"reason": "WEATHER_CATALOG_EXPIRED"})


def test_compute_process_is_killed_and_temporary_data_removed(
    tmp_path, backup_request, monkeypatch
):
    import subprocess
    import sys

    from autonavlog.web.backup_process import ProcessCalculator

    original = subprocess.Popen
    children = []

    def hanging(_args, **kwargs):
        child = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", hanging)
    with pytest.raises(subprocess.TimeoutExpired):
        ProcessCalculator(DATA, tmp_path, deadline_seconds=0.1)(backup_request)
    assert children[0].poll() is not None
    assert list(tmp_path.iterdir()) == []


def test_registration_does_not_open_legacy_sessions_or_migration(tmp_path, monkeypatch):
    from autonavlog.web.app import create_app
    from autonavlog.web.runtime import WebRuntimeConfig

    monkeypatch.setenv("AUTONAVLOG_FORECAST_BACKUP_ORIGIN", "http://localhost:4202")
    app = create_app(WebRuntimeConfig(DATA, tmp_path))
    with TestClient(app) as client:
        assert client.post("/api/application-session").status_code == 401
        assert client.post("/api/calculation-jobs").status_code == 401
        assert client.post("/api/projects").status_code in {401, 405}
        assert not list(tmp_path.glob("*.navmate*"))


def test_concurrent_orphan_reclamation_is_idempotent(tmp_path, backup_request, monkeypatch):
    import os

    import autonavlog.web.backup_process as module
    from autonavlog.web.backup_process import ProcessCalculator

    old = tmp_path / "job-orphan"
    old.mkdir()
    os.utime(old, (time.time() - 601, time.time() - 601))
    original = module.shutil.rmtree
    calls = []

    def another_worker_removed(path, *args, **kwargs):
        if Path(path) == old:
            original(path)
            raise FileNotFoundError("other worker completed cleanup")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, "rmtree", another_worker_removed)
    monkeypatch.setattr(
        ProcessCalculator, "_compute", lambda self, request, directory: calls.append(request)
    )
    ProcessCalculator(DATA, tmp_path)(backup_request)
    assert calls == [backup_request]
    assert list(tmp_path.iterdir()) == []


def test_child_wall_deadline_survives_without_supervisor():
    import signal
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from autonavlog.web.backup_runner import configure_limits; "
            "import time; configure_limits(1); time.sleep(30)",
        ],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == -signal.SIGALRM
