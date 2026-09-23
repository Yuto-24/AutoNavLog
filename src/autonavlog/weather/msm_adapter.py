from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from math import isfinite
from numbers import Integral, Real
from pathlib import Path
from typing import Any

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import (
    ForecastRequirement,
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)

FT_TO_M = 0.3048


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value)
    return value


class MsmWeatherProvider:
    """Adapter for jma-gpv-weather v0.5.0; no GRIB or cache internals leak upstream."""

    package_version = "0.5.0"

    def __init__(
        self,
        cache_dir: str | Path,
        client: Any | None = None,
    ):
        try:
            import jma_gpv_weather
        except ImportError as error:
            raise RuntimeError(
                "jma-gpv-weather 0.5.0 is required for MsmWeatherProvider"
            ) from error
        if getattr(jma_gpv_weather, "__version__", None) != self.package_version:
            raise RuntimeError("MsmWeatherProvider requires jma-gpv-weather 0.5.0 exactly")
        self._msm = jma_gpv_weather
        self.client = client or jma_gpv_weather.MsmClient(cache_dir=cache_dir)
        self._prepared: dict[str, Any] = {}

    def _requirement(self, requirement: ForecastRequirement) -> Any:
        variables = set()
        if requirement.require_aloft_wind:
            variables.add(self._msm.WeatherVariable.ALOFT_WIND)
        if requirement.require_aloft_temperature:
            variables.add(self._msm.WeatherVariable.ALOFT_TEMPERATURE)
        if requirement.require_surface_temperature:
            variables.add(self._msm.WeatherVariable.SURFACE_TEMPERATURE)
        return self._msm.ForecastRequirements(
            valid_times=requirement.valid_times_utc,
            variables=frozenset(variables),
        )

    def _run_id(self, value: str) -> Any:
        parsed = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        return self._msm.RunId(parsed)

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        status = self.client.resolve_run(self._requirement(requirement), selected_run=None)
        if status.selected_run is None:
            raise RuntimeError("MSM did not select a compatible forecast run")
        selected = status.selected_run
        return ForecastRun(id=str(selected), initial_time_utc=selected.initial_time_utc)

    def inspect_run_status(
        self,
        selected_run_id: str,
        requirement: ForecastRequirement,
    ) -> RunSelectionStatus:
        status = self.client.resolve_run(
            self._requirement(requirement),
            selected_run=self._run_id(selected_run_id),
        )
        return RunSelectionStatus(
            selected_run_id=None if status.selected_run is None else str(status.selected_run),
            latest_compatible_run_id=(
                None if status.latest_compatible_run is None else str(status.latest_compatible_run)
            ),
            selected_run_covers_requirement=status.selected_run_covers_request,
            update_available=status.update_available,
            warnings=status.warnings,
        )

    def prepare_run(
        self,
        forecast_run_id: str,
        requirement: ForecastRequirement,
    ) -> PreparedForecastRun:
        prepared = self.client.prepare_run(
            self._run_id(forecast_run_id),
            self._requirement(requirement),
            terrain_provider=None,
        )
        self._prepared[forecast_run_id] = prepared
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={
                "provider": "jma-gpv-weather",
                "package_version": self.package_version,
                "surface_temperature_required": requirement.require_surface_temperature,
            },
        )

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        if forecast_run_id not in self._prepared:
            raise RuntimeError("MSM forecast run must be prepared before querying")
        prepared = self._prepared[forecast_run_id]
        results = prepared.query_many([self._to_query(request) for request in requests])
        if len(results) != len(requests):
            raise RuntimeError("MSM batch result count does not match request count")
        projected = tuple(
            self._from_result(request, result)
            for request, result in zip(requests, results, strict=True)
        )
        for request, result in zip(requests, projected, strict=True):
            if (
                request.kind == WeatherRequestKind.ALOFT
                and result.availability == Availability.UNAVAILABLE
            ):
                coverage = prepared.check_altitude_coverage(self._to_query(request))
                result.metadata["altitude_coverage"] = {
                    "reason_code": coverage.reason_code,
                    "provenance": _jsonable(coverage.provenance),
                }
        return projected

    def _to_query(self, request: WeatherRequest) -> Any:
        if request.kind == WeatherRequestKind.SURFACE_TEMPERATURE:
            return self._msm.SurfaceTemperatureQuery(
                request.latitude_deg,
                request.longitude_deg,
                request.valid_time_utc,
            )
        if request.kind == WeatherRequestKind.ALOFT:
            if request.altitude_ft_msl is None:
                raise ValueError("aloft weather request requires altitude")
            return self._msm.AloftQuery(
                request.latitude_deg,
                request.longitude_deg,
                request.valid_time_utc,
                altitude_msl_m=request.altitude_ft_msl * FT_TO_M,
            )
        raise ValueError(f"unsupported weather request kind: {request.kind}")

    def _from_result(self, request: WeatherRequest, result: Any) -> WeatherResult:
        available = result.availability == self._msm.Availability.AVAILABLE
        values = dict(result.values)
        warnings = tuple(result.warnings)
        reason = result.reason_code
        metadata = {"provenance": _jsonable(result.provenance)}
        speed_ms = values.get("wind_speed_ms")
        speed_kt = values.get("wind_speed_kt")
        if (
            request.kind == WeatherRequestKind.ALOFT
            and available
            and "CALM_WIND_DIRECTION_UNDEFINED" in warnings
            and values.get("wind_direction_deg_from") is None
            and isinstance(speed_ms, (int, float))
            and not isinstance(speed_ms, bool)
            and isfinite(speed_ms)
            and 0.0 <= speed_ms < 0.1
            and isinstance(speed_kt, (int, float))
            and not isinstance(speed_kt, bool)
            and isfinite(speed_kt)
            and 0.0 <= speed_kt < 0.1 / 0.514444
        ):
            # Keep the provider's vector and sampled magnitudes for provenance;
            # only the speed consumed by the navigation solver becomes CALM.
            metadata["calm_normalization"] = {
                "original_values": _jsonable(dict(values)),
                "policy": "MSM_CALM_WIND_DIRECTION_UNDEFINED_TO_ZERO_KT",
            }
            values["wind_speed_kt"] = 0.0
        if request.kind == WeatherRequestKind.SURFACE_TEMPERATURE:
            metadata.update(
                {
                    "provider": "jma-gpv-weather",
                    "source_variable": "tmp_surface",
                    "requested_valid_time_utc": request.valid_time_utc.isoformat(),
                    "requested_elevation_ft_msl": request.elevation_ft_msl,
                }
            )
            # Preserve the existing application input sanity check and error contract;
            # sampling, unit conversion and provenance come from SurfaceTemperatureQuery.
            if not available and reason == "MISSING_SOURCE_VALUE":
                values, reason = {"temperature_c": None}, "SURFACE_TEMPERATURE_UNAVAILABLE"
            elif available:
                temperature = values.get("temperature_k")
                if not isinstance(temperature, (int, float)) or not (
                    isfinite(temperature) and 150.0 <= temperature <= 350.0
                ):
                    available = False
                    values, reason = {"temperature_c": None}, "SURFACE_TEMPERATURE_INVALID"
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE if available else Availability.UNAVAILABLE,
            kind=request.kind,
            values=values,
            reason_code=reason,
            warnings=warnings,
            metadata=metadata,
        )
