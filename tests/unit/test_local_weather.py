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
from autonavlog.weather.local_msm import LocalMsmWeather, LocalWeatherError
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
        ("expired", "WEATHER_CATALOG_EXPIRED"),
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
    elif kind == "expired":
        catalog["generated_at"] = "2026-01-01T00:00:00Z"
        catalog["expires_at"] = "2026-01-01T01:00:00Z"
    elif kind == "invalid":
        catalog["schema_version"] = 2
    else:
        project.route_nodes[0].latitude_deg = 80
    with pytest.raises(LocalWeatherError) as error:
        LocalMsmWeather().plan(project, requirement, json.dumps(catalog))
    assert error.value.code == code


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
