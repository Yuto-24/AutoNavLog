from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import Availability, WeatherRequestKind


class WeatherModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForecastRequirement(WeatherModel):
    valid_times_utc: tuple[datetime, ...]
    require_aloft_wind: bool = True
    require_aloft_temperature: bool = True
    require_estimated_qnh: bool = True

    @field_validator("valid_times_utc")
    @classmethod
    def validate_times(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        if not values:
            raise ValueError("at least one valid time is required")
        if any(value.tzinfo is None for value in values):
            raise ValueError("valid times must be timezone-aware")
        return tuple(sorted({value.astimezone(timezone.utc) for value in values}))


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

    @field_validator("valid_time_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("weather request time must be timezone-aware")
        return value.astimezone(timezone.utc)


class WeatherResult(WeatherModel):
    request_id: str
    availability: Availability
    kind: WeatherRequestKind
    values: dict[str, float | str | None] = Field(default_factory=dict)
    reason_code: str | None = None
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
