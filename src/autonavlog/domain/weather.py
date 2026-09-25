from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import Availability, WeatherRequestKind

ForecastModel = Literal["MSM", "GSM"]


class ForecastCoverageError(Exception):
    """Affirmative model/Run exclusion; never an acquisition failure."""

    def __init__(self, reason_codes: Sequence[str]):
        self.reason_codes = tuple(reason_codes)
        if not self.reason_codes or any(not reason.strip() for reason in self.reason_codes):
            raise ValueError("coverage exclusion requires reason codes")
        super().__init__(", ".join(self.reason_codes))


def legacy_forecast_model(run: str | None) -> ForecastModel | None:
    """Historic forecast Run-only records mean MSM; FTD is not a model."""
    return "MSM" if run and run != "ftd-fixed-v1" else None


class WeatherModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForecastRequirement(WeatherModel):
    valid_times_utc: tuple[datetime, ...]
    require_aloft_wind: bool = True
    require_aloft_temperature: bool = True
    require_surface_temperature: bool = False
    coverage_requests: tuple[WeatherRequest, ...] = ()
    route_points: tuple[tuple[float, float], ...] = ()

    @field_validator("valid_times_utc")
    @classmethod
    def validate_times(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        if not values:
            raise ValueError("at least one valid time is required")
        if any(value.tzinfo is None for value in values):
            raise ValueError("valid times must be timezone-aware")
        return tuple(sorted({value.astimezone(UTC) for value in values}))


class ForecastRun(WeatherModel):
    id: str
    initial_time_utc: datetime


class RunSelectionStatus(WeatherModel):
    selected_run_id: str | None
    latest_compatible_run_id: str | None
    selected_run_covers_requirement: bool
    update_available: bool = False
    warnings: tuple[str, ...] = ()


class PreparedForecastRun(WeatherModel):
    forecast_run_id: str
    requirement: ForecastRequirement
    metadata: dict[str, Any] = Field(default_factory=dict)


class WeatherRequest(WeatherModel):
    request_id: str
    kind: WeatherRequestKind
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    valid_time_utc: datetime
    altitude_ft_msl: float | None = None
    elevation_ft_msl: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("valid_time_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("weather request time must be timezone-aware")
        return value.astimezone(UTC)


class WeatherResult(WeatherModel):
    request_id: str
    availability: Availability
    kind: WeatherRequestKind
    values: dict[str, float | str | None] = Field(default_factory=dict)
    reason_code: str | None = None
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
