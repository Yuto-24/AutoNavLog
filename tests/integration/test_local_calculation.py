from __future__ import annotations

import json
from pathlib import Path

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
        "/api/import",
        {
            "filename": "issue_43_golden.kml",
            "kml_text": (ROOT / "tests/fixtures/issue_43_golden.kml").read_text(),
        },
    )
    state = json.loads(local.dispatch("/api/route/confirm", inputs["confirm"]))
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
    local.dispatch("/api/project", update)
    return json.loads(local.dispatch("/api/calculate"))


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
        local.dispatch("/api/calculate")
    assert local.session.outcome is previous


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/projects/save", {"name": "unsupported"}),
        ("/api/import", {"filename": "route.kmz", "kml_text": "<kml/>"}),
        ("/api/import", {"filename": "route.kml", "kml_text": "<broken"}),
        ("/api/calculate", {}),
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
        state = json.loads(another.dispatch("/api/state"))
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
