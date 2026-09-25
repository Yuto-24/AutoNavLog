"""The same synchronous application action resumes at the portable IO boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autonavlog.local import LocalApplication
from scripts.local_reference import reference_state

ROOT = Path(__file__).resolve().parents[2]
FEED = ROOT / "tests/fixtures/msm-portable"


def catalog_text(feed=FEED):
    catalog = json.loads((feed / "catalog.json").read_text())
    now = datetime.now(UTC)
    catalog.update(generated_at=now.isoformat(), expires_at=(now + timedelta(hours=1)).isoformat())
    return json.dumps(catalog)


@pytest.fixture
def local():
    application = LocalApplication(ROOT / "data")
    state = reference_state(feed=str(FEED))
    application.dispatch("bootstrap", state["workingRecovery"])
    yield application
    application.close()


def resume(local, requests, feeds=None):
    for _ in range(20):
        response = json.loads(local.dispatch_response("calculate"))
        if "weather_request" not in response:
            return response
        request = response["weather_request"]
        requests.append(request)
        model = request["model"]
        if feeds is None:
            assert model == "MSM"
        feed = FEED if feeds is None else feeds[model]
        if request["kind"] == "catalog":
            assert local.weather_catalog(model, catalog_text(feed)) == "{}"
        else:
            asset = request["asset"]
            assert local.accept_weather(model, (feed / asset["file"]).read_bytes(),
                                        asset["sha256"]) == "{}"
    pytest.fail("portable IO replay failed to converge")


def test_msm_calculation_resumes_without_gsm_or_publishing_partial_result(local, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("native IO"))
    before = local.session.last_calculation
    local.begin_weather()
    suspended = json.loads(local.dispatch_response("calculate"))
    assert suspended["weather_request"] == {"model": "MSM", "kind": "catalog", "asset": None}
    assert local.session.last_calculation is before
    requests = []
    state = resume(local, requests)
    assert "error" not in state
    assert state["outcome"]["selected_forecast_model"] == "MSM"
    assert state["outcome"]["status"] == "READY_FOR_COPY"
    assert [r["kind"] for r in requests] == ["catalog", "prepared"]
    assert not local.local_weather.clients["GSM"].payloads
    local.begin_weather()
    assert not local.local_weather.clients["MSM"].payloads
    assert "weather_request" in json.loads(local.dispatch_response("calculate"))


def test_corrupt_or_missing_payload_keeps_last_good_and_never_requests_gsm(local):
    local.begin_weather()
    before = local.session.last_calculation.model_dump(mode="json")
    assert local.weather_catalog("MSM", catalog_text()) == "{}"
    request = json.loads(local.dispatch_response("calculate"))["weather_request"]
    error = json.loads(local.accept_weather("MSM", b"bad", request["asset"]["sha256"]))
    assert error["error"]["code"] == "WEATHER_PAYLOAD_INTEGRITY_FAILED"
    assert local.session.last_calculation.model_dump(mode="json") == before
    catalog = json.loads(catalog_text())
    catalog["assets"] = []
    assert local.weather_catalog("MSM", json.dumps(catalog)) == "{}"
    state = json.loads(local.dispatch_response("calculate"))
    assert "weather_request" not in state
    assert any(i["code"] == "FORECAST_PREPARE_FAILED" for i in state["outcome"]["issues"])
    assert local.session.last_calculation.model_dump(mode="json") == before
    assert local.local_weather.clients["GSM"].catalog is None


def test_portable_replay_does_not_consume_two_click_refresh_intent(local):
    local.session.project.selected_forecast_run_id = "20260915180000"
    local.session.project.selected_forecast_model = "MSM"
    local.begin_weather()
    requests = []
    first = resume(local, requests)
    assert first["outcome"]["selected_forecast_run_id"] == "20260915180000"
    assert any(i["code"] == "FORECAST_UPDATE_AVAILABLE" for i in first["outcome"]["issues"])
    assert [r["asset"]["run"] for r in requests if r["asset"]] == [
        "20260915180000", "20260915210000",
    ]
    intent = local.session.forecast_update_fingerprint
    last = local.session.last_calculation
    local.begin_weather()
    assert "weather_request" in json.loads(local.dispatch_response("calculate"))
    assert local.session.forecast_update_fingerprint == intent
    assert local.session.last_calculation is last
    second = resume(local, [])
    assert second["outcome"]["selected_forecast_run_id"] == "20260915210000"
    assert local.session.forecast_update_fingerprint is None


@pytest.mark.parametrize("pinned", [False, True])
def test_public_gsm_portable_fallback_preserves_one_model_and_last_calculation(local, pinned):
    if not pinned:
        local.session.project.selected_forecast_model = None
        local.session.project.selected_forecast_run_id = None
    feeds = {"MSM": ROOT / "tests/fixtures/forecast-policy/msm-outside-hgt",
             "GSM": ROOT / "tests/fixtures/forecast-policy/gsm"}
    local.begin_weather()
    requests = []
    state = resume(local, requests, feeds)
    outcome = state["outcome"]
    assert outcome["status"] == "READY_FOR_COPY"
    assert (outcome["selected_forecast_model"], outcome["selected_forecast_run_id"]) == (
        "GSM", "20260915060000",
    )
    assert outcome["forecast_provenance"]["fallback_from"] == "MSM"
    assert outcome["forecast_provenance"]["coverage_reason_codes"] == ["ALTITUDE_OUTSIDE_HGT_RANGE"]
    assert [r["model"] for r in requests] == ["MSM", "MSM", "MSM", "GSM", "GSM"]
    last = state["workingRecovery"]["last_calculation"]
    assert last["project"]["selected_forecast_model"] == "GSM"
    assert last["outcome"]["forecast_provenance"] == outcome["forecast_provenance"]
    assert last["forecast_metadata"]["model"] == "GSM"
    assert all(result.metadata["model"] == "GSM"
               for result in local.session.calculation_service.last_weather_results)


def test_actual_newest_msm_hgt_exclusion_still_prefers_older_msm(local):
    local.session.project.selected_forecast_model = None
    local.session.project.selected_forecast_run_id = None
    local.begin_weather()
    requests = []
    state = resume(local, requests, {
        "MSM": ROOT / "tests/fixtures/forecast-policy/msm-newest-outside-hgt",
    })
    assert state["outcome"]["selected_forecast_model"] == "MSM"
    assert state["outcome"]["selected_forecast_run_id"] == "20260915180000"
    assert state["outcome"]["status"] == "READY_FOR_COPY"
    assert all(r["model"] == "MSM" for r in requests)


def test_actual_missing_source_values_block_without_older_run_or_gsm(local):
    before = local.session.last_calculation.model_dump(mode="json")
    local.session.project.selected_forecast_model = None
    local.session.project.selected_forecast_run_id = None
    local.begin_weather()
    requests = []
    state = resume(local, requests, {
        "MSM": ROOT / "tests/fixtures/forecast-policy/msm-source-unavailable",
    })
    assert any(i["code"] == "FORECAST_PREPARE_FAILED" for i in state["outcome"]["issues"])
    assert [r["kind"] for r in requests] == ["catalog", "prepared"]
    assert local.session.last_calculation.model_dump(mode="json") == before


def test_portable_gsm_pin_only_returns_to_msm_on_second_click(local):
    local.session.project.selected_forecast_model = "GSM"
    local.session.project.selected_forecast_run_id = "20260915060000"
    feeds = {"MSM": FEED, "GSM": ROOT / "tests/fixtures/forecast-policy/gsm"}
    local.begin_weather()
    first = resume(local, [], feeds)
    assert first["outcome"]["selected_forecast_model"] == "GSM"
    assert any(i["code"] == "FORECAST_UPDATE_AVAILABLE" for i in first["outcome"]["issues"])
    local.begin_weather()
    second = resume(local, [], feeds)
    assert second["outcome"]["selected_forecast_model"] == "MSM"
    assert second["outcome"]["selected_forecast_run_id"] == "20260915210000"


def test_missing_older_msm_delivery_is_not_evidence_for_gsm_fallback(local, monkeypatch):
    import sys

    original_catalog_text = catalog_text

    def full_source_catalog(feed=FEED):
        value = json.loads(original_catalog_text(feed))
        value["listings"] = json.loads((FEED / "catalog.json").read_text())["listings"]
        return json.dumps(value)

    monkeypatch.setattr(sys.modules[__name__], "catalog_text", full_source_catalog)
    local.session.project.selected_forecast_model = None
    local.session.project.selected_forecast_run_id = None
    last = local.session.last_calculation.model_dump(mode="json")
    local.begin_weather()
    requests = []
    state = resume(local, requests, {
        "MSM": ROOT / "tests/fixtures/forecast-policy/msm-outside-hgt",
    })
    assert any(i["code"] == "FORECAST_PREPARE_FAILED" for i in state["outcome"]["issues"])
    assert len(requests) == 3  # catalog and two evaluated Runs; older asset is absent
    assert local.session.last_calculation.model_dump(mode="json") == last
    assert local.local_weather.clients["GSM"].catalog is None
