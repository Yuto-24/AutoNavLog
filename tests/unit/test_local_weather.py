from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jma_gpv_weather import (
    AloftQuery,
    MsmClient,
    MsmPreparedData,
    RunId,
    SurfaceTemperatureQuery,
)

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.project import Project
from autonavlog.domain.weather import ForecastRequirement, WeatherRequest
from autonavlog.weather.local_msm import LocalMsmWeather, LocalWeatherError, coverage_message
from scripts.local_reference import reference_state

FEED = Path(__file__).resolve().parents[1] / "fixtures/msm-portable"
OLD, NEW = "20260915180000", "20260915210000"


@pytest.fixture
def catalog():
    value = json.loads((FEED / "catalog.json").read_text())
    now = datetime.now(UTC)
    value.update(generated_at=now.isoformat(), expires_at=(now + timedelta(hours=1)).isoformat())
    return value


@pytest.fixture
def project():
    return Project.model_validate(reference_state(feed=str(FEED))["project"])


@pytest.fixture
def requirement():
    return ForecastRequirement(
        valid_times_utc=(datetime(2026, 9, 16, 4, 30, tzinfo=UTC),),
        require_surface_temperature=True,
    )


def setup_weather(project, requirement, catalog, run=None):
    project = project.model_copy(update={"selected_forecast_run_id": run})
    weather = LocalMsmWeather()
    asset = json.loads(weather.plan(project, requirement, json.dumps(catalog)))
    weather.accept((FEED / asset["file"]).read_bytes(), asset["sha256"])
    return weather, asset


@pytest.mark.parametrize(
    "options",
    [
        {"strong_wind": True, "forecast": True},
        {"strong_wind": True, "feed": str(FEED)},
    ],
)
def test_reference_rejects_strong_wind_with_forecast_sources(options):
    with pytest.raises(ValueError, match="strong_wind is available only for FTD weather"):
        reference_state(**options)


def test_portable_provider_keeps_run_selection_and_upstream_values(project, requirement, catalog):
    for run in [None, OLD]:
        weather, asset = setup_weather(project, requirement, catalog, run)
        assert asset["run"] == (run or NEW)
        status = weather.provider.inspect_run_status(asset["run"], requirement)
        assert status.selected_run_id == asset["run"]
        assert status.update_available == (run == OLD)
        weather.provider.prepare_run(asset["run"], requirement)
        requests = [
            WeatherRequest(
                request_id=kind.value,
                kind=kind,
                latitude_deg=32.1,
                longitude_deg=131.2,
                valid_time_utc=requirement.valid_times_utc[0],
                altitude_ft_msl=6500,
                elevation_ft_msl=20,
            )
            for kind in [WeatherRequestKind.ALOFT, WeatherRequestKind.SURFACE_TEMPERATURE]
        ]
        results = weather.provider.query_batch(asset["run"], requests)
        data = MsmPreparedData.from_bytes((FEED / asset["file"]).read_bytes())
        native = MsmClient().prepare_run(
            RunId(data.selection.run_utc),
            weather.provider._requirement(requirement),
            prepared_data=data,
        )
        expected = native.query_many(
            [
                AloftQuery(32.1, 131.2, requirement.valid_times_utc[0], 6500 * 0.3048),
                SurfaceTemperatureQuery(32.1, 131.2, requirement.valid_times_utc[0]),
            ]
        )
        for actual, reference in zip(results, expected, strict=True):
            assert actual.availability == Availability.AVAILABLE
            assert actual.values == reference.values
            provenance = actual.metadata["provenance"]
            assert provenance["source_hashes"] == reference.provenance.source_hashes
            assert provenance["source_urls"] == list(reference.provenance.source_urls)
            assert provenance["initial_time_utc"].startswith("2026-09-15")


@pytest.mark.parametrize(
    "kind,code",
    [
        ("missing_listing", "WEATHER_DISCOVERY_FAILED"),
        ("empty_listing", "WEATHER_RUN_UNAVAILABLE"),
        ("missing_asset", "WEATHER_PREPARED_UNAVAILABLE"),
        ("invalid", "WEATHER_CATALOG_INVALID"),
        ("outside_model", "WEATHER_OUT_OF_COVERAGE"),
    ],
)
def test_failure_kinds_do_not_become_model_coverage(project, requirement, catalog, kind, code):
    if kind == "missing_listing":
        catalog["listings"] = {}
    elif kind == "empty_listing":
        catalog["listings"] = dict.fromkeys(catalog["listings"], "")
    elif kind == "missing_asset":
        catalog["assets"] = []
    elif kind == "invalid":
        catalog["schema_version"] = 2
    else:
        project.route_nodes[0].latitude_deg = 80
    with pytest.raises(LocalWeatherError) as error:
        LocalMsmWeather().plan(project, requirement, json.dumps(catalog))
    assert error.value.code == code


@pytest.mark.parametrize(
    "generated_offset,expires_offset,accepted",
    [(180, 21780, True), (300, 21900, True), (301, 21901, False),
     (-21600, 0, True), (-21601, -1, True), (-21599, 1, True)],
)
def test_catalog_clock_skew_distinguishes_expiry(
    project, requirement, catalog, monkeypatch, generated_offset, expires_offset, accepted
):
    from autonavlog.weather import local_msm

    now = datetime(2026, 9, 16, 4, tzinfo=UTC)

    class DeviceClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(local_msm, "datetime", DeviceClock)
    catalog.update(
        generated_at=(now + timedelta(seconds=generated_offset)).isoformat(),
        expires_at=(now + timedelta(seconds=expires_offset)).isoformat(),
    )
    weather = LocalMsmWeather()
    if accepted:
        asset = json.loads(weather.plan(project, requirement, json.dumps(catalog)))
        assert asset["run"] == NEW
        weather.accept((FEED / asset["file"]).read_bytes(), asset["sha256"])
        assert weather.client.delivery["catalog_expired"] == (expires_offset <= 0)
    else:
        with pytest.raises(LocalWeatherError) as error:
            weather.plan(project, requirement, json.dumps(catalog))
        assert error.value.code == "WEATHER_CATALOG_EXPIRED"
        assert weather.client.catalog is None


@pytest.mark.parametrize("damage", ["hash", "zip", "wrong_run", "missing_field"])
def test_payload_failure_never_reuses_previous_arrays(project, requirement, catalog, damage):
    weather, asset = setup_weather(project, requirement, catalog, OLD)
    asset = json.loads(
        weather.plan(
            project.model_copy(update={"selected_forecast_run_id": NEW}),
            requirement,
            json.dumps(catalog),
        )
    )
    assert weather.client.data is None
    payload = (FEED / asset["file"]).read_bytes()
    digest = asset["sha256"]
    expected = "WEATHER_PAYLOAD_INTEGRITY_FAILED"
    if damage == "hash":
        digest = "0" * 64
    elif damage == "zip":
        payload = b"invalid portable archive"
        digest = hashlib.sha256(payload).hexdigest()
    elif damage == "wrong_run":
        other = next(item for item in catalog["assets"] if item["run"] == OLD)
        payload, digest = (FEED / other["file"]).read_bytes(), other["sha256"]
    else:
        data = MsmPreparedData.from_bytes(payload)
        data = MsmPreparedData(data.selection, {}, data.pressure, data.source_hashes)
        payload = data.to_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        expected = "WEATHER_PREPARED_UNAVAILABLE"
    with pytest.raises(LocalWeatherError) as error:
        weather.accept(payload, digest)
    assert error.value.code == expected


def test_hgt_exclusion_remains_distinct_from_unavailable_source(project, requirement, catalog):
    weather, asset = setup_weather(project, requirement, catalog)
    weather.provider.prepare_run(asset["run"], requirement)
    request = WeatherRequest(
        request_id="hgt",
        kind=WeatherRequestKind.ALOFT,
        latitude_deg=32.1,
        longitude_deg=131.2,
        valid_time_utc=requirement.valid_times_utc[0],
        altitude_ft_msl=30000,
    )
    result = weather.provider.query_batch(asset["run"], [request])[0]
    assert result.metadata["altitude_coverage"]["reason_code"] == "ALTITUDE_OUTSIDE_HGT_RANGE"
    outside = request.model_copy(update={"latitude_deg": 34.5, "altitude_ft_msl": 6500})
    result = weather.provider.query_batch(asset["run"], [outside])[0]
    assert result.metadata["altitude_coverage"]["reason_code"] == "SOURCE_VALUE_UNAVAILABLE"


@pytest.mark.parametrize("reasons, expected", [
    (("NO_SPEC_COMPATIBLE_RUN",), "選択した日時のMSM予報は利用できません。"),
    (("UNKNOWN_REASON",), "UNKNOWN_REASON"),
    (("NO_SPEC_COMPATIBLE_RUN", "UNKNOWN_REASON"),
     "選択した日時のMSM予報は利用できません。, UNKNOWN_REASON"),
])
def test_time_coverage_message_preserves_other_reasons(reasons, expected):
    assert coverage_message(reasons) == expected


def test_plan_outside_selected_run_time_uses_approved_message(project, requirement, catalog):
    project = project.model_copy(update={"selected_forecast_run_id": NEW})
    requirement = requirement.model_copy(update={
        "valid_times_utc": (datetime(2026, 9, 20, tzinfo=UTC),),
    })
    with pytest.raises(LocalWeatherError) as error:
        LocalMsmWeather().plan(project, requirement, json.dumps(catalog))
    assert error.value.code == "WEATHER_OUT_OF_COVERAGE"
    assert str(error.value) == "選択した日時のMSM予報は利用できません。"


def expired_catalog(catalog):
    now = datetime.now(UTC)
    return {**catalog, "generated_at": (now - timedelta(hours=7)).isoformat(),
            "expires_at": (now - timedelta(hours=1)).isoformat()}


@pytest.mark.parametrize("pinned", [None, OLD])
def test_expired_public_feed_validates_and_records_fallback(project, requirement, catalog, pinned):
    weather, asset = setup_weather(project, requirement, expired_catalog(catalog), pinned)
    prepared = weather.provider.prepare_run(asset["run"], requirement)
    delivery = prepared.metadata["weather_delivery"]
    assert asset["run"] == (pinned or NEW)
    assert delivery["fallback_reason"] == "WEATHER_CATALOG_EXPIRED"
    assert delivery["source"] == "public_feed"
    assert delivery["selected_run"] == asset["run"]
    assert delivery["forecast_initial_time_utc"]
    assert delivery["forecast_age_seconds"] > 0
    assert delivery["catalog_generated_at"] and delivery["catalog_expires_at"]
    assert "時間経過" in prepared.metadata["delivery_warning"]


def test_expired_feed_can_select_available_older_run_but_does_not_replace_fixed_run(
    project, requirement, catalog
):
    catalog = expired_catalog(catalog)
    catalog["assets"] = [asset for asset in catalog["assets"] if asset["run"] == OLD]
    weather, asset = setup_weather(project, requirement, catalog)
    assert asset["run"] == OLD
    assert weather.provider.resolve_run(requirement).id == OLD
    with pytest.raises(LocalWeatherError):
        setup_weather(project, requirement, catalog, NEW)


@pytest.mark.parametrize("reason", [
    "WEATHER_COMMUNICATION_FAILED", "WEATHER_DISCOVERY_FAILED",
    "WEATHER_SOURCE_UNAVAILABLE", "WEATHER_DOWNLOAD_FAILED",
])
def test_retained_pair_selects_only_its_run_and_fresh_acquisition_clears_warning(
    project, requirement, catalog, reason
):
    weather = LocalMsmWeather()
    asset = json.loads(weather.plan(
        project.model_copy(update={"selected_forecast_run_id": None}),
        requirement, json.dumps(catalog),
        fallback_reason=reason, retained_run=OLD,
    ))
    weather.accept((FEED / asset["file"]).read_bytes(), asset["sha256"])
    assert asset["run"] == OLD
    assert weather.provider.resolve_run(requirement).id == OLD
    metadata = weather.provider.prepare_run(OLD, requirement).metadata
    assert metadata["weather_delivery"]["source"] == "retained_pair"
    assert metadata["weather_delivery"]["fallback_reason"] == reason
    asset = json.loads(weather.plan(project, requirement, json.dumps(catalog)))
    weather.accept((FEED / asset["file"]).read_bytes(), asset["sha256"])
    metadata = weather.provider.prepare_run(asset["run"], requirement).metadata
    assert metadata["weather_delivery"]["fallback_reason"] is None
    assert "delivery_warning" not in metadata


@pytest.mark.parametrize(
    "reason", ["secret-network-diagnostic", "WEATHER_PAYLOAD_INTEGRITY_FAILED"],
)
def test_untrusted_or_integrity_reason_does_not_enable_fallback(
    project, requirement, catalog, reason
):
    with pytest.raises(LocalWeatherError, match="MSM"):
        LocalMsmWeather().plan(
            project, requirement, json.dumps(catalog), fallback_reason=reason, retained_run=OLD,
        )


@pytest.mark.parametrize("expired", [False, True])
def test_final_requirement_checks_payload_not_only_source_listing(
    project, requirement, catalog, expired
):
    weather, asset = setup_weather(
        project, requirement, expired_catalog(catalog) if expired else catalog,
    )
    final = requirement.model_copy(update={
        "valid_times_utc": (datetime(2026, 9, 16, 7, tzinfo=UTC),),
    })
    # The source listing covers 07Z; this intentionally small portable fixture does not.
    assert MsmClient().check_coverage(
        weather.provider._requirement(final),
        run=weather.provider._run_id(asset["run"]),
    ).outside_spec is False
    with pytest.raises(LocalWeatherError) as error:
        weather.provider.inspect_run_status(asset["run"], final)
    assert error.value.code == "WEATHER_PREPARED_UNAVAILABLE"


@pytest.mark.parametrize("latitude,altitude", [(34.5, 6500), (32.1, 30000)])
def test_fallback_requires_actual_point_and_altitude_coverage(
    project, requirement, catalog, latitude, altitude
):
    weather, asset = setup_weather(project, requirement, expired_catalog(catalog))
    weather.provider.prepare_run(asset["run"], requirement)
    request = WeatherRequest(
        request_id="full-requirement", kind=WeatherRequestKind.ALOFT,
        latitude_deg=latitude, longitude_deg=131.2,
        valid_time_utc=requirement.valid_times_utc[0], altitude_ft_msl=altitude,
    )
    with pytest.raises(LocalWeatherError) as error:
        weather.provider.query_batch(asset["run"], [request])
    assert error.value.code == "WEATHER_PREPARED_UNAVAILABLE"


@pytest.mark.parametrize("damage", ["schema", "future", "hash", "identity", "variables"])
def test_expiry_does_not_bypass_catalog_or_payload_validation(
    project, requirement, catalog, damage
):
    catalog = expired_catalog(catalog)
    if damage == "schema":
        catalog["schema_version"] = 2
    if damage == "future":
        catalog["generated_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        catalog["expires_at"] = (datetime.now(UTC) + timedelta(hours=7)).isoformat()
    weather = LocalMsmWeather()
    with pytest.raises(LocalWeatherError):
        asset = json.loads(weather.plan(project, requirement, json.dumps(catalog)))
        payload = (FEED / asset["file"]).read_bytes()
        if damage == "hash":
            payload += b"damage"
        if damage in {"identity", "variables"}:
            data = MsmPreparedData.from_bytes(payload)
            if damage == "identity":
                other = next(item for item in catalog["assets"] if item["run"] == OLD)
                data = MsmPreparedData.from_bytes((FEED / other["file"]).read_bytes())
            else:
                data = MsmPreparedData(data.selection, {}, data.pressure, data.source_hashes)
            payload = data.to_bytes()
            asset["sha256"] = hashlib.sha256(payload).hexdigest()
        weather.accept(payload, asset["sha256"])


def test_fallback_result_persists_warning_and_final_failure_preserves_last_calculation(
    catalog, monkeypatch
):
    from autonavlog.local import LocalApplication

    root = FEED.parents[2]
    recovery = reference_state(feed=str(FEED))["workingRecovery"]
    local = LocalApplication(root / "data")
    restored = LocalApplication(root / "data")
    try:
        local.dispatch("bootstrap", recovery)
        catalog = expired_catalog(catalog)
        asset = json.loads(local.weather_plan(json.dumps(catalog)))
        assert "error" not in asset
        assert local.accept_weather((FEED / asset["file"]).read_bytes(), asset["sha256"]) == "{}"
        result = json.loads(local.dispatch_response("calculate"))
        assert "error" not in result
        assert result["readiness"]["calculationIsCurrent"]
        last = result["workingRecovery"]["last_calculation"]
        assert last["forecast_metadata"]["weather_delivery"]["fallback_reason"] == (
            "WEATHER_CATALOG_EXPIRED"
        )
        assert any(issue["code"] == "WEATHER_DELIVERY_FALLBACK"
                   for issue in last["outcome"]["issues"])
        reloaded = json.loads(restored.dispatch("bootstrap", result["workingRecovery"]))
        assert reloaded["workingRecovery"]["last_calculation"] == last
        assert any(issue["code"] == "WEATHER_DELIVERY_FALLBACK"
                   for issue in reloaded["outcome"]["issues"])

        service = local.session.calculation_service.forecast_service
        final_requirement = service.build_final_requirement
        def beyond_payload(*args, **kwargs):
            requirement = final_requirement(*args, **kwargs)
            return requirement.model_copy(update={
                "valid_times_utc": (*requirement.valid_times_utc,
                                   datetime(2026, 9, 16, 7, tzinfo=UTC)),
            })
        monkeypatch.setattr(service, "build_final_requirement", beyond_payload)
        asset = json.loads(local.weather_plan(json.dumps(catalog)))
        local.accept_weather((FEED / asset["file"]).read_bytes(), asset["sha256"])
        failed = json.loads(local.dispatch_response("calculate"))
        assert failed["error"]["code"] == "CALCULATION_JOB_FAILED"
        assert local.session.last_calculation.model_dump(mode="json") == last
    finally:
        local.close()
        restored.close()
