from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import Availability, WeatherRequestKind


class WeatherModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForecastRequirement(WeatherModel):
    valid_times_utc: tuple[datetime, ...]
    require_aloft_wind: bool = True
    require_aloft_temperature: bool = True
    require_surface_temperature: bool = False

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


class AdoptedWeatherSample(WeatherModel):
    """One original query and its final normalized response, before user overrides."""

    request: WeatherRequest
    result: WeatherResult

    @model_validator(mode="after")
    def validate_pair(self) -> AdoptedWeatherSample:
        if self.request.request_id != self.result.request_id:
            raise ValueError("weather snapshot request/result identity mismatch")
        if self.request.kind != self.result.kind:
            raise ValueError("weather snapshot request/result kind mismatch")
        return self


class CalculationWeatherSnapshot(WeatherModel):
    """Fixed sampled conditions for one calculation, independent of Weather caches.

    These are scalar phase samples, not a forecast field that can be queried at
    new locations or times. Unavailable responses retain their original status.
    """

    schema_version: Literal[1] = 1
    sampling_policy: Literal["FINAL_PHASE_SAMPLES_V1"] = "FINAL_PHASE_SAMPLES_V1"
    weather_mode: Literal["FORECAST", "FTD"]
    forecast_run_id: str = Field(min_length=1)
    calculation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    samples: tuple[AdoptedWeatherSample, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def _content_digest(self) -> str:
        # Keep full float precision; input fingerprints intentionally round
        # floats and therefore are unsuitable as a checksum of sampled values.
        payload = json.dumps(
            self.model_dump(mode="json", exclude={"content_sha256"}),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def validate_content(self) -> CalculationWeatherSnapshot:
        identities = [sample.request.request_id for sample in self.samples]
        if len(set(identities)) != len(identities):
            raise ValueError("weather snapshot contains duplicate request identities")
        if self.content_sha256 != self._content_digest():
            raise ValueError("weather snapshot content checksum mismatch")
        return self

    @classmethod
    def capture(
        cls,
        *,
        weather_mode: Literal["FORECAST", "FTD"],
        forecast_run_id: str,
        calculation_fingerprint: str,
        samples: tuple[AdoptedWeatherSample, ...],
    ) -> CalculationWeatherSnapshot:
        candidate = cls.model_construct(
            schema_version=1,
            sampling_policy="FINAL_PHASE_SAMPLES_V1",
            weather_mode=weather_mode,
            forecast_run_id=forecast_run_id,
            calculation_fingerprint=calculation_fingerprint,
            samples=tuple(sample.model_copy(deep=True) for sample in samples),
            content_sha256="",
        )
        candidate.content_sha256 = candidate._content_digest()
        return cls.model_validate_json(candidate.model_dump_json())


def saved_weather_snapshot(
    forecast_metadata: dict[str, Any],
    *,
    weather_mode: Literal["FORECAST", "FTD"],
    forecast_run_id: str | None,
    calculation_fingerprint: str,
) -> CalculationWeatherSnapshot | None:
    """Read optional v1 evidence without inventing samples for historical results."""

    if "weather_snapshot" not in forecast_metadata:
        return None
    snapshot = CalculationWeatherSnapshot.model_validate_json(
        json.dumps(forecast_metadata["weather_snapshot"], allow_nan=False)
    )
    if (
        snapshot.weather_mode != weather_mode
        or snapshot.forecast_run_id != forecast_run_id
        or snapshot.calculation_fingerprint != calculation_fingerprint
    ):
        raise ValueError("weather snapshot calculation binding mismatch")
    return snapshot
