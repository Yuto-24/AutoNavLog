from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import (
    AdoptedWeatherSample,
    CalculationWeatherSnapshot,
    WeatherRequest,
    WeatherResult,
    saved_weather_snapshot,
)


def _sample() -> AdoptedWeatherSample:
    return AdoptedWeatherSample(
        request=WeatherRequest(
            request_id="section:example:cruise",
            kind=WeatherRequestKind.ALOFT,
            latitude_deg=32.0,
            longitude_deg=131.0,
            altitude_ft_msl=5500.0,
            valid_time_utc=datetime(2026, 10, 4, 0, tzinfo=UTC),
            metadata={"phase": "CRUISE"},
        ),
        result=WeatherResult(
            request_id="section:example:cruise",
            kind=WeatherRequestKind.ALOFT,
            availability=Availability.AVAILABLE,
            values={"wind_direction_deg_from": 264.1234567891234, "temperature_c": -12.5},
            metadata={"provenance": {"source": "fixture"}},
        ),
    )


def _snapshot(sample: AdoptedWeatherSample | None = None) -> CalculationWeatherSnapshot:
    return CalculationWeatherSnapshot.capture(
        weather_mode="FORECAST",
        forecast_run_id="20261004000000",
        calculation_fingerprint="a" * 64,
        samples=(sample or _sample(),),
    )


def test_snapshot_roundtrip_retains_precision_source_time_and_isolates_mutations():
    sample = _sample()
    snapshot = _snapshot(sample)
    before = snapshot.model_dump()
    sample.result.values["wind_direction_deg_from"] = 260
    sample.result.metadata["provenance"]["source"] = "changed"
    restored = CalculationWeatherSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored.model_dump() == before
    assert restored.samples[0].result.values["wind_direction_deg_from"] == 264.1234567891234
    assert restored.samples[0].request.valid_time_utc == datetime(2026, 10, 4, 0, tzinfo=UTC)


@pytest.mark.parametrize("field", ["wind_direction_deg_from", "temperature_c"])
def test_checksum_detects_sub_micro_precision_changes(field):
    payload = _snapshot().model_dump(mode="json")
    payload["samples"][0]["result"]["values"][field] += 1e-9
    with pytest.raises(ValidationError, match="checksum"):
        CalculationWeatherSnapshot.model_validate(payload)


@pytest.mark.parametrize("damage", ["identity", "kind", "duplicate", "missing", "version"])
def test_invalid_samples_are_rejected_without_modifying_payload(damage):
    payload = _snapshot().model_dump(mode="json")
    if damage == "identity":
        payload["samples"][0]["result"]["request_id"] = "another"
    elif damage == "kind":
        payload["samples"][0]["result"]["kind"] = "SURFACE_TEMPERATURE"
    elif damage == "duplicate":
        payload["samples"].append(copy.deepcopy(payload["samples"][0]))
    elif damage == "missing":
        payload["samples"] = []
    else:
        payload["schema_version"] = 999
    before = copy.deepcopy(payload)
    with pytest.raises(ValidationError):
        CalculationWeatherSnapshot.model_validate(payload)
    assert payload == before


def test_unavailable_response_and_warnings_are_preserved_without_fallback():
    sample = _sample()
    sample.result.availability = Availability.UNAVAILABLE
    sample.result.reason_code = "WEATHER_OUT_OF_COVERAGE"
    sample.result.values = {"temperature_c": None}
    sample.result.warnings = ("source-warning",)
    assert _snapshot(sample).samples[0].result == sample.result


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_cannot_enter_the_snapshot(value):
    sample = _sample()
    sample.result.values["temperature_c"] = value
    with pytest.raises(ValueError):
        _snapshot(sample)


@pytest.mark.parametrize("damage", ["weather_mode", "forecast_run_id", "calculation_fingerprint"])
def test_snapshot_must_belong_to_the_saved_calculation(damage):
    arguments = {
        "weather_mode": "FORECAST",
        "forecast_run_id": "20261004000000",
        "calculation_fingerprint": "a" * 64,
    }
    arguments[damage] = {
        "weather_mode": "FTD",
        "forecast_run_id": "20261004030000",
        "calculation_fingerprint": "b" * 64,
    }[damage]
    with pytest.raises(ValueError, match="binding"):
        saved_weather_snapshot({"weather_snapshot": _snapshot().model_dump(mode="json")},
                               **arguments)


def test_old_metadata_has_no_snapshot_and_null_is_not_valid_evidence():
    arguments = dict(weather_mode="FORECAST", forecast_run_id="20261004000000",
                     calculation_fingerprint="a" * 64)
    assert saved_weather_snapshot({"provider": "legacy"}, **arguments) is None
    with pytest.raises(ValidationError):
        saved_weather_snapshot({"weather_snapshot": None}, **arguments)
