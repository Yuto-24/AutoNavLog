from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from math import atan2, cos, degrees, hypot, radians, sin
from typing import Any

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.project import FtdWeatherSettings, ManualWind
from autonavlog.domain.weather import (
    ForecastRequirement,
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)
from autonavlog.nav.airspeed import isa_temperature_c

FTD_FORECAST_RUN_ID = "ftd-fixed-v1"
FTD_WEATHER_POLICY_VERSION = "FTD_VECTOR_ISA_V1"
FTD_WIND_TOP_ALTITUDE_FT = 5_000.0
KNOT_TO_MS = 0.514444


def _wind_components_kt(wind: ManualWind) -> tuple[float, float]:
    """Return eastward/northward components for a meteorological FROM wind."""

    direction = radians(wind.direction_deg_from)
    return -wind.speed_kt * sin(direction), -wind.speed_kt * cos(direction)


def interpolate_ftd_wind(
    settings: FtdWeatherSettings,
    altitude_ft_msl: float,
) -> tuple[float | None, float, float, float]:
    ratio = min(1.0, max(0.0, altitude_ft_msl / FTD_WIND_TOP_ALTITUDE_FT))
    surface_u, surface_v = _wind_components_kt(settings.surface_wind)
    upper_u, upper_v = _wind_components_kt(settings.wind_at_5000_ft)
    u_kt = surface_u + (upper_u - surface_u) * ratio
    v_kt = surface_v + (upper_v - surface_v) * ratio
    speed_kt = hypot(u_kt, v_kt)
    direction = None
    if speed_kt > 1e-9:
        direction = (degrees(atan2(-u_kt, -v_kt)) + 360.0) % 360.0
    return direction, speed_kt, u_kt * KNOT_TO_MS, v_kt * KNOT_TO_MS


class FtdWeatherProvider:
    """Weather provider for repeatable FTD scenarios.

    Wind vectors are interpolated between sea-level and 5,000 ft MSL, then
    held constant above 5,000 ft. Temperature follows ISA at the requested
    MSL altitude. Time and horizontal position intentionally do not affect the
    result.
    """

    def __init__(self, settings: FtdWeatherSettings):
        self.settings = settings.model_copy(deep=True)
        self.prepared = False

    @staticmethod
    def _initial_time(requirement: ForecastRequirement) -> datetime:
        return min(requirement.valid_times_utc).astimezone(timezone.utc)

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        return ForecastRun(
            id=FTD_FORECAST_RUN_ID,
            initial_time_utc=self._initial_time(requirement),
        )

    def inspect_run_status(
        self,
        selected_run_id: str,
        requirement: ForecastRequirement,
    ) -> RunSelectionStatus:
        del requirement
        covers = selected_run_id == FTD_FORECAST_RUN_ID
        return RunSelectionStatus(
            selected_run_id=selected_run_id,
            latest_compatible_run_id=FTD_FORECAST_RUN_ID,
            selected_run_covers_requirement=covers,
            warnings=() if covers else ("SELECTED_RUN_OUT_OF_COVERAGE",),
        )

    def prepare_run(
        self,
        forecast_run_id: str,
        requirement: ForecastRequirement,
    ) -> PreparedForecastRun:
        if forecast_run_id != FTD_FORECAST_RUN_ID:
            raise ValueError("unsupported FTD forecast run")
        self.prepared = True
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={
                "provider": "ftd_fixed",
                "temperature_policy": "ISA_MSL",
                "wind_policy": "VECTOR_LINEAR_0_TO_5000_FT_MSL_THEN_CONSTANT",
            },
        )

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        if forecast_run_id != FTD_FORECAST_RUN_ID or not self.prepared:
            raise ValueError("FTD forecast run must be prepared before querying")
        return tuple(self._result(request) for request in requests)

    def _result(self, request: WeatherRequest) -> WeatherResult:
        if request.kind == WeatherRequestKind.ESTIMATED_QNH:
            values: dict[str, float | str | None] = {
                "label": "FTD標準大気",
                "qnh_hpa": 1013.25,
            }
            metadata: dict[str, Any] = {
                "provider": "ftd_fixed",
                "qnh_policy": "ISA_STANDARD",
            }
            warnings: tuple[str, ...] = ()
        elif request.kind == WeatherRequestKind.SURFACE_TEMPERATURE:
            altitude = float(
                request.elevation_ft_msl
                if request.elevation_ft_msl is not None
                else request.altitude_ft_msl or 0.0
            )
            values = {"temperature_c": isa_temperature_c(altitude)}
            metadata = {
                "provider": "ftd_fixed",
                "requested_elevation_ft_msl": altitude,
                "temperature_policy": "ISA_AT_AIRPORT_ELEVATION_MSL",
            }
            warnings = ()
        else:
            altitude = float(request.altitude_ft_msl or 0.0)
            direction, speed, u_ms, v_ms = interpolate_ftd_wind(
                self.settings,
                altitude,
            )
            values = {
                "u_ms": u_ms,
                "v_ms": v_ms,
                "wind_speed_kt": speed,
                "wind_direction_deg_from": direction,
                "temperature_c": isa_temperature_c(altitude),
            }
            metadata = {
                "provider": "ftd_fixed",
                "requested_altitude_ft_msl": altitude,
                "interpolation_fraction": min(
                    1.0,
                    max(0.0, altitude / FTD_WIND_TOP_ALTITUDE_FT),
                ),
                "surface_wind": self.settings.surface_wind.model_dump(),
                "wind_at_5000_ft": self.settings.wind_at_5000_ft.model_dump(),
                "temperature_policy": "ISA_MSL",
                "wind_policy": "VECTOR_LINEAR_0_TO_5000_FT_MSL_THEN_CONSTANT",
            }
            warnings = ("CALM_WIND_DIRECTION_UNDEFINED",) if direction is None else ()
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values=values,
            warnings=warnings,
            metadata=metadata,
        )
