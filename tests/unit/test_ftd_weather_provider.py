from __future__ import annotations

from datetime import datetime, timezone

import pytest

from autonavlog.domain.enums import WeatherRequestKind
from autonavlog.domain.project import FtdWeatherSettings, ManualWind
from autonavlog.domain.weather import ForecastRequirement, WeatherRequest
from autonavlog.nav.airspeed import isa_temperature_c
from autonavlog.weather.ftd_provider import (
    FTD_FORECAST_RUN_ID,
    FtdWeatherProvider,
)


def _settings(
    surface_direction: float = 350,
    surface_speed: float = 10,
    upper_direction: float = 10,
    upper_speed: float = 10,
) -> FtdWeatherSettings:
    return FtdWeatherSettings(
        surface_wind=ManualWind(
            direction_deg_from=surface_direction,
            speed_kt=surface_speed,
        ),
        wind_at_5000_ft=ManualWind(
            direction_deg_from=upper_direction,
            speed_kt=upper_speed,
        ),
    )


def _request(request_id: str, altitude: float) -> WeatherRequest:
    return WeatherRequest(
        request_id=request_id,
        kind=WeatherRequestKind.ALOFT,
        latitude_deg=32,
        longitude_deg=131,
        valid_time_utc=datetime(2026, 8, 16, tzinfo=timezone.utc),
        altitude_ft_msl=altitude,
    )


def _prepared_provider(settings: FtdWeatherSettings | None = None) -> FtdWeatherProvider:
    provider = FtdWeatherProvider(settings or _settings())
    requirement = ForecastRequirement(valid_times_utc=(datetime(2026, 8, 16, tzinfo=timezone.utc),))
    run = provider.resolve_run(requirement)
    assert run.id == FTD_FORECAST_RUN_ID
    provider.prepare_run(run.id, requirement)
    return provider


def test_ftd_wind_uses_vector_interpolation_and_caps_above_5000_ft() -> None:
    provider = _prepared_provider()

    ground, midpoint, upper, above = provider.query_batch(
        FTD_FORECAST_RUN_ID,
        (
            _request("ground", 0),
            _request("midpoint", 2500),
            _request("upper", 5000),
            _request("above", 10_000),
        ),
    )

    assert ground.values["wind_direction_deg_from"] == pytest.approx(350)
    assert ground.values["wind_speed_kt"] == pytest.approx(10)
    assert midpoint.values["wind_direction_deg_from"] == pytest.approx(0, abs=1e-9)
    assert midpoint.values["wind_speed_kt"] == pytest.approx(9.8480775)
    assert upper.values["wind_direction_deg_from"] == pytest.approx(10)
    assert above.values["wind_direction_deg_from"] == pytest.approx(10)
    assert above.values["wind_speed_kt"] == pytest.approx(10)
    assert above.values["temperature_c"] == pytest.approx(isa_temperature_c(10_000))
    assert above.metadata["interpolation_fraction"] == 1.0


def test_ftd_opposing_winds_interpolate_to_calm_with_undefined_direction() -> None:
    provider = _prepared_provider(_settings(90, 10, 270, 10))

    (midpoint,) = provider.query_batch(
        FTD_FORECAST_RUN_ID,
        (_request("midpoint", 2500),),
    )

    assert midpoint.values["wind_speed_kt"] == pytest.approx(0, abs=1e-9)
    assert midpoint.values["wind_direction_deg_from"] is None
    assert midpoint.warnings == ("CALM_WIND_DIRECTION_UNDEFINED",)


def test_ftd_provider_requires_preparation_and_preserves_request_order() -> None:
    provider = FtdWeatherProvider(_settings())
    requests = (_request("second", 5000), _request("first", 0))

    with pytest.raises(ValueError, match="prepared"):
        provider.query_batch(FTD_FORECAST_RUN_ID, requests)

    prepared = _prepared_provider()
    results = prepared.query_batch(FTD_FORECAST_RUN_ID, requests)
    assert [result.request_id for result in results] == ["second", "first"]
    assert all(result.metadata["provider"] == "ftd_fixed" for result in results)


def test_ftd_surface_temperature_uses_isa_at_airport_elevation() -> None:
    provider = _prepared_provider()
    request = WeatherRequest(
        request_id="departure:surface",
        kind=WeatherRequestKind.SURFACE_TEMPERATURE,
        latitude_deg=31.877,
        longitude_deg=131.448,
        valid_time_utc=datetime(2026, 8, 16, tzinfo=timezone.utc),
        elevation_ft_msl=123.0,
    )

    (result,) = provider.query_batch(FTD_FORECAST_RUN_ID, (request,))

    assert result.values == {"temperature_c": pytest.approx(isa_temperature_c(123.0))}
    assert result.metadata["temperature_policy"] == "ISA_AT_AIRPORT_ELEVATION_MSL"
    assert result.metadata["requested_elevation_ft_msl"] == 123.0
    assert result.warnings == ()
