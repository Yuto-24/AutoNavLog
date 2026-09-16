from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest

from autonavlog.local import LocalApplication

ROOT = Path(__file__).resolve().parents[2]
INPUTS = json.loads((ROOT / "tests/fixtures/issue_117_ftd.json").read_text())


@pytest.fixture
def local():
    application = LocalApplication(ROOT / "data", forecast_fixture=ROOT / "tests/fixtures/msm")
    yield application
    application.close()


def calculate(local, *, forecast=False):
    inputs = json.loads(json.dumps(INPUTS))
    if forecast:
        inputs["confirm"].update(
            weather_mode="FORECAST", flight_date="2026-09-12", departure_time_jst="12:00"
        )
    local.dispatch(
        "importRoute",
        {
            "filename": "issue_43_golden.kml",
            "kml_text": (ROOT / "tests/fixtures/issue_43_golden.kml").read_text(),
        },
    )
    state = json.loads(local.dispatch("confirmRoute", inputs["confirm"]))
    project = state["project"]
    update = {
        **{
            key: inputs["confirm"][key]
            for key in (
                "flight_date",
                "departure_time_jst",
                "weather_mode",
                "ftd_weather",
                "default_variation_deg_east",
            )
        },
        "total_usable_fuel_gal": 90,
        "sections": [
            {
                "section_id": section["id"],
                "phase": section["phase"],
                "planned_altitude_ft_msl": altitude,
            }
            for section, altitude in zip(project["sections"], inputs["altitudes"], strict=True)
        ],
        "visual_reporting_point_node_id": project["route_nodes"][-2]["id"],
        "selected_pattern_altitude_ft_msl": 1000,
    }
    local.dispatch("updateProject", update)
    return json.loads(local.dispatch("calculate"))


def test_local_ftd_golden_and_failed_calculation_preserves_last_good(local, monkeypatch):
    state = calculate(local)
    golden = json.loads((ROOT / "tests/fixtures/issue_117_ftd_golden.json").read_text())
    outcome = state["outcome"]
    assert state["savedProjects"] == []
    assert state["readiness"]["calculationIsCurrent"]
    assert state["readiness"]["issues"] == []
    assert len(state["project"]["sections"]) == 4
    assert [point["type"] for point in outcome["derived_points"]] == ["RCA", "EOC"]
    assert outcome["summary"] == golden["summary"]
    assert outcome["fuel_plan"] == pytest.approx(golden["fuel"], abs=1e-8, rel=0)
    assert len(outcome["sections"]) == len(golden["zones"])
    for actual, expected in zip(outcome["sections"], golden["zones"], strict=True):
        for key, value in expected.items():
            if isinstance(value, dict) and "value" in value:
                adopted = actual[key]
                assert adopted["automatic_value"] == pytest.approx(value["value"], abs=1e-8, rel=0)
                assert adopted["automatic_status"] == value["status"]
            elif isinstance(value, (int, float)):
                assert actual[key] == pytest.approx(value, abs=1e-8, rel=0)
            else:
                assert actual[key] == value

    previous = local.session.outcome

    def fail(*args, **kwargs):
        raise RuntimeError("calculation failure")

    monkeypatch.setattr(local.session.calculation_service, "calculate", fail)
    with pytest.raises(RuntimeError, match="calculation failure"):
        local.dispatch("calculate")
    assert local.session.outcome is previous


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/projects/save", {"name": "unsupported"}),
        ("importRoute", {"filename": "route.kmz", "content_base64": "bm90IGEgemlw"}),
        ("importRoute", {"filename": "route.kml", "kml_text": "<broken"}),
        ("calculate", {}),
    ],
)
def test_local_fails_closed(local, path, payload):
    with pytest.raises(ValueError):
        local.dispatch(path, payload)
    assert local.session.project is None


def test_new_local_instance_does_not_restore_project(local):
    calculate(local)
    another = LocalApplication(ROOT / "data", forecast_fixture=ROOT / "tests/fixtures/msm")
    try:
        state = json.loads(another.dispatch("bootstrap"))
        assert state["project"] is None
        assert state["outcome"] is None
        assert state["savedProjects"] == []
    finally:
        another.close()


def test_local_forecast_uses_actual_fixture_without_network(local, monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("Local Calculation must not fetch weather")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    state = calculate(local, forecast=True)
    assert state["outcome"]["selected_forecast_run_id"] == "20260912030000"
    assert state["outcome"]["status"] == "READY_FOR_COPY"
    assert state["outcome"]["summary"]["time"]["text"] == "0:50"
    assert state["outcome"]["summary"]["distance"]["text"] == "125.5"
    assert state["readiness"]["calculationIsCurrent"]
    assert state["savedProjects"] == []
    assert state["outcome"]["display_rows"][-2]["wind"]["reason_code"] == "TAF_PROVIDER_DISABLED"


@pytest.mark.parametrize(
    "operation,payload,code",
    [
        ("calculate", {}, "PROJECT_REQUIRED"),
        ("confirmRoute", {}, "VALIDATION_FAILED"),
        ("importRoute", {"filename": "a.kml", "kml_text": "<broken"}, "KML_IMPORT_FAILED"),
        ("importRoute", {"filename": "a.kml", "content_base64": "!!"}, "UPLOAD_ENCODING_INVALID"),
        (
            "importRoute",
            {"filename": "a.kmz", "content_base64": "bm90IGEgemlw"},
            "KML_IMPORT_FAILED",
        ),
    ],
)
def test_local_error_response_preserves_application_meaning(local, operation, payload, code):
    error = json.loads(local.dispatch_response(operation, payload))["error"]
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == code
    assert error["message"]
    assert "Traceback" not in error["message"]
    if code == "VALIDATION_FAILED":
        assert error["message"] == "入力内容を確認してください。"
        assert {"location", "message", "type"} == set(error["details"]["issues"][0])
    assert local.session.project is None


def test_local_operations_keep_draft_last_good_and_failed_transaction(local, monkeypatch):
    state = calculate(local)
    project = local.session.project
    node = state["project"]["route_nodes"][1]
    renamed = json.loads(
        local.dispatch_response(
            "renameRouteNode",
            {
                "node_id": node["id"],
                "name": "renamed",
            },
        )
    )
    assert renamed["project"]["route_nodes"][1]["name"] == "renamed"
    assert renamed["outcome"] is not None
    local.dispatch_response("acknowledge", {"key": "test/key", "checked": True})
    local.dispatch_response("replaceCheckPoints", {"check_points": []})
    # The facade commits the valid draft before calculation; failure retains it and last-good.
    before_outcome = local.session.outcome.model_dump(mode="json")
    update = {
        "flight_date": "2026-09-11",
        "departure_time_jst": "09:00",
        "total_usable_fuel_gal": 75,
        "default_variation_deg_east": 8,
        "weather_mode": "FTD",
        "ftd_weather": project.ftd_weather.model_dump(mode="json"),
    }

    def fail(*args, **kwargs):
        raise RuntimeError("calculation failure")

    monkeypatch.setattr(local.session.calculation_service, "calculate", fail)
    error = json.loads(local.dispatch_response("updateAndRecalculate", update))["error"]
    assert error == {"code": "REQUEST_FAILED", "message": "処理に失敗しました。", "details": {}}
    assert local.session.project.total_usable_fuel_gal == 75
    after_outcome = local.session.outcome.model_dump(mode="json")
    # Readiness reclassifies stale status; canonical calculation values remain unchanged.
    for key in before_outcome.keys() - {"status"}:
        assert after_outcome[key] == before_outcome[key]
    assert local.session.outcome is not None
    # A plain update commits its draft, preserving the previous result for stale display.
    draft = json.loads(local.dispatch_response("updateProject", update))
    assert draft["project"]["total_usable_fuel_gal"] == 75
    assert draft["outcome"] is not None
    assert not draft["readiness"]["calculationIsCurrent"]


@pytest.mark.parametrize(
    "operation,endpoint,payload",
    [
        ("calculate", "/api/calculation-jobs", {}),
        (
            "renameRouteNode",
            "/api/project/route-nodes/invalid/name",
            {"node_id": "invalid", "name": "test"},
        ),
        ("confirmRoute", "/api/route/confirm", {}),
        ("importRoute", "/api/import", {"filename": "a.kml", "kml_text": "<broken"}),
        ("importRoute", "/api/import", {"filename": "a.kml", "content_base64": "!!"}),
    ],
)
def test_local_and_fastapi_errors_have_the_same_application_meaning(
    local, tmp_path, operation, endpoint, payload
):
    from autonavlog.web.app import create_app
    from autonavlog.web.runtime import WebRuntimeConfig

    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "legacy",
            weather_mode="fake",
            trusted_local_identity="issue118-test",
            session_cookie_secure=False,
        )
    )

    async def legacy_error():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await client.post("/api/session")
            if operation == "renameRouteNode":
                result = (await client.put(endpoint, json={"name": payload["name"]})).json()
            else:
                result = (await client.post(endpoint, json=payload)).json()
            if operation == "calculate":
                for _ in range(100):
                    result = (await client.get(f"/api/calculation-jobs/{result['job_id']}")).json()
                    if result["status"] == "failed":
                        break
                    await asyncio.sleep(0.01)
                assert result["status"] == "failed"
            return result

    legacy = asyncio.run(legacy_error())
    error = json.loads(local.dispatch_response(operation, payload))["error"]
    if "detail" in legacy:
        assert error["code"] == "VALIDATION_FAILED"
        assert error["details"]["issues"] == [
            {"location": item["loc"][1:], "message": item["msg"], "type": item["type"]}
            for item in legacy["detail"]
        ]
    else:
        assert error["code"] == legacy["error"]["code"]
        assert error["message"] == legacy["error"]["message"]


@pytest.mark.parametrize("operation", ["calculate", "updateAndRecalculate"])
@pytest.mark.parametrize("failure", ["validation", "runtime"])
def test_internal_failure_semantics_match_legacy_and_preserve_draft(
    local, tmp_path, monkeypatch, caplog, operation, failure
):
    from pydantic import BaseModel

    from autonavlog.web.app import create_app
    from autonavlog.web.runtime import WebRuntimeConfig

    calculate(local)
    old_outcome = local.session.outcome.model_dump(mode="json")
    update = {
        "flight_date": "2026-09-11",
        "departure_time_jst": "09:00",
        "total_usable_fuel_gal": 75,
        "default_variation_deg_east": 8,
        "weather_mode": "FTD",
        "ftd_weather": local.session.project.ftd_weather.model_dump(mode="json"),
    }
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "legacy",
            weather_mode="fake",
            trusted_local_identity="issue118-internal",
            session_cookie_secure=False,
        )
    )
    # Share the prepared input/result with the real HTTP facade without recalculating a fixture.
    web = app.state.web_application
    legacy_session = web.create_session("issue118-internal")
    legacy_session.project = local.session.project.model_copy(deep=True)
    legacy_session.outcome = local.session.outcome.model_copy(deep=True)
    legacy_session.readiness = local.session.readiness

    class InternalResult(BaseModel):
        internal_count: int

    def fail(*args, **kwargs):
        if failure == "validation":
            InternalResult.model_validate({"internal_count": "secret internal detail"})
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(local.session.calculation_service, "calculate", fail)
    monkeypatch.setattr(legacy_session.calculation_service, "calculate", fail)

    async def legacy_response():
        from autonavlog.web.app import SESSION_COOKIE_NAME

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://test",
            cookies={SESSION_COOKIE_NAME: legacy_session.token},
        ) as client:
            if operation == "updateAndRecalculate":
                response = await client.post("/api/project/recalculate", json=update)
                assert response.status_code == 500
                assert response.text == "Internal Server Error"
                # LegacyApplication's non-JSON HTTP normalization is covered by its TS test.
                return {"code": "REQUEST_FAILED", "message": "処理に失敗しました。", "details": {}}
            job = (await client.post("/api/calculation-jobs")).json()
            for _ in range(100):
                job = (await client.get(f"/api/calculation-jobs/{job['job_id']}")).json()
                if job["status"] == "failed":
                    break
                await asyncio.sleep(0.01)
            assert job["status"] == "failed"
            assert job["error"] == {
                "code": "CALCULATION_JOB_FAILED",
                "message": "計算に失敗しました。",
                "status": 500,
            }
            return {"code": job["error"]["code"], "message": job["error"]["message"], "details": {}}

    try:
        legacy = asyncio.run(legacy_response())
        error = json.loads(local.dispatch_response(operation, update))["error"]
        assert error == legacy
        assert "secret" not in json.dumps(error)
        assert error["code"] != "VALIDATION_FAILED"
        if operation == "calculate":
            records = [
                record
                for record in caplog.records
                if record.name == "autonavlog.web.calculation_jobs"
            ]
            assert len(records) == 1
            assert records[0].getMessage().startswith("Unexpected calculation failure for job ")
            assert records[0].exc_info is not None
        for session in (local.session, legacy_session):
            assert session.project.total_usable_fuel_gal == (
                75 if operation == "updateAndRecalculate" else 90
            )
            after = session.outcome.model_dump(mode="json")
            for key in old_outcome.keys() - {"status"}:
                assert after[key] == old_outcome[key]
    finally:
        app.state.calculation_jobs.shutdown()


def test_reload_working_recovery_restores_import_and_last_good_without_calculation(
    local, monkeypatch
):
    imported = json.loads(
        local.dispatch(
            "importRoute",
            {
                "filename": "route.kml",
                "kml_text": (ROOT / "tests/fixtures/issue_43_golden.kml").read_text(),
            },
        )
    )
    replacement = LocalApplication(ROOT / "data", forecast_fixture=ROOT / "tests/fixtures/msm")
    try:
        restored = json.loads(replacement.dispatch("bootstrap", imported["workingRecovery"]))
        assert restored["import"] == imported["import"]
        confirmed = json.loads(replacement.dispatch("confirmRoute", INPUTS["confirm"]))
        assert confirmed["project"]
    finally:
        replacement.close()

    calculated = calculate(local)
    replacement = LocalApplication(ROOT / "data", forecast_fixture=ROOT / "tests/fixtures/msm")
    try:

        def forbidden(*args, **kwargs):
            raise AssertionError("reload must never calculate")

        monkeypatch.setattr(replacement.app, "calculate", forbidden)
        restored = json.loads(replacement.dispatch("bootstrap", calculated["workingRecovery"]))
        assert restored["project"] == calculated["project"]
        assert restored["outcome"] == calculated["outcome"]
        assert restored["readiness"] == calculated["readiness"]
        assert restored["savedProjects"] == []
    finally:
        replacement.close()


def test_recovery_rejects_last_good_from_another_project(local):
    from uuid import uuid4

    state = calculate(local)
    state["workingRecovery"]["outcome"]["project_id"] = str(uuid4())
    result = json.loads(local.dispatch_response("bootstrap", state["workingRecovery"]))
    assert result["error"]["code"] == "VALIDATION_FAILED"


@pytest.mark.parametrize("forecast", [False, True])
def test_durable_record_load_keeps_original_calculation_and_run(local, monkeypatch, forecast):
    from uuid import uuid4

    from autonavlog.local_persistence import migrate_record

    calculated = calculate(local, forecast=forecast)
    working = calculated["workingRecovery"]
    last = working["last_calculation"]
    assert last is not None
    draft = dict(working["project"])
    draft["total_usable_fuel_gal"] = 73
    record = migrate_record(
        {
            "schemaVersion": 2,
            "id": draft["id"],
            "token": str(uuid4()),
            "draft": draft,
            "checkpoint": working["project"],
            "lastCalculation": last,
            "updatedAt": draft["updated_at"],
        }
    ).model_dump(mode="json")
    assert "web_owner_id" not in record["draft"]["metadata"]
    assert "web_owner_id" not in record["lastCalculation"]["project"]["metadata"]
    with_local = LocalApplication(ROOT / "data", forecast_fixture=ROOT / "tests/fixtures/msm")
    try:

        def unexpected_calculation(*args, **kwargs):
            raise AssertionError("opening durable data must not calculate")

        monkeypatch.setattr(
            with_local.session.calculation_service, "calculate", unexpected_calculation
        )
        restored = json.loads(
            with_local.dispatch(
                "bootstrap",
                {
                    "version": 1,
                    "project": record["draft"],
                    "last_calculation": record["lastCalculation"],
                    "outcome": last["outcome"],
                    "destination_wind": last["destination_wind"],
                    "import_result": None,
                    "import_filename": None,
                },
            )
        )
        assert restored["workingRecovery"]["last_calculation"] == last
        assert restored["project"]["selected_forecast_run_id"] == draft["selected_forecast_run_id"]
        assert restored["project"]["total_usable_fuel_gal"] == 73
        assert restored["outcome"] is not None
        assert not restored["readiness"]["calculationIsCurrent"]
    finally:
        with_local.close()


def test_local_kmz_uses_shared_importer_and_preserves_document_selection(local):
    kml = (ROOT / "tests/fixtures/issue_43_golden.kml").read_bytes()
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("one.kml", kml)
        archive.writestr("folder/two.kml", kml)
    request = {
        "filename": "routes.kmz",
        "content_base64": base64.b64encode(buffer.getvalue()).decode(),
    }
    error = json.loads(local.dispatch_response("importRoute", request))["error"]
    assert error["code"] == "KMZ_DOCUMENT_SELECTION_REQUIRED"
    assert set(error["details"]["candidates"]) == {"one.kml", "folder/two.kml"}
    selected = json.loads(
        local.dispatch("importRoute", {**request, "kmz_kml_filename": "folder/two.kml"})
    )
    plain = json.loads(
        local.dispatch("importRoute", {"filename": "route.kml", "kml_text": kml.decode()})
    )
    assert selected["import"]["candidates"] == plain["import"]["candidates"]
    invalid = json.loads(
        local.dispatch_response("importRoute", {**request, "kmz_kml_filename": "missing.kml"})
    )
    assert invalid["error"]["code"] == "KML_IMPORT_FAILED"
    assert json.loads(local.dispatch("bootstrap"))["import"] == plain["import"]
