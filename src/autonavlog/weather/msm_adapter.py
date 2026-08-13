from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
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
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class MsmWeatherProvider:
    """Adapter for jma-msm-wind v0.2.1; no GRIB or cache internals leak upstream."""

    package_version = "0.2.1"

    def __init__(
        self,
        cache_dir: str | Path,
        terrain_cache_path: str | Path | None = None,
        client: Any | None = None,
    ):
        try:
            import msm_wind
        except ImportError as error:
            raise RuntimeError("jma-msm-wind 0.2.1 is required for MsmWeatherProvider") from error
        if getattr(msm_wind, "__version__", None) != self.package_version:
            raise RuntimeError("MsmWeatherProvider requires jma-msm-wind 0.2.1 exactly")
        self._msm = msm_wind
        self.client = client or msm_wind.MsmClient(cache_dir=cache_dir)
        self.terrain_cache_path = None if terrain_cache_path is None else Path(terrain_cache_path)
        self._prepared: dict[str, Any] = {}

    def _requirement(self, requirement: ForecastRequirement) -> Any:
        variables = set()
        if requirement.require_aloft_wind:
            variables.add(self._msm.WeatherVariable.ALOFT_WIND)
        if requirement.require_aloft_temperature:
            variables.add(self._msm.WeatherVariable.ALOFT_TEMPERATURE)
        if requirement.require_estimated_qnh:
            variables.add(self._msm.WeatherVariable.ESTIMATED_QNH)
        return self._msm.ForecastRequirements(
            valid_times=requirement.valid_times_utc,
            variables=frozenset(variables),
        )

    def _run_id(self, value: str) -> Any:
        parsed = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
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
        terrain = self._load_terrain(requirement)
        prepared = self.client.prepare_run(
            self._run_id(forecast_run_id),
            self._requirement(requirement),
            terrain_provider=terrain,
        )
        self._prepared[forecast_run_id] = prepared
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={
                "provider": "jma-msm-wind",
                "package_version": self.package_version,
                "terrain_cache": None
                if self.terrain_cache_path is None
                else str(self.terrain_cache_path),
                "terrain_required": requirement.require_estimated_qnh,
                "terrain_loaded": terrain is not None,
            },
        )

    def _load_terrain(self, requirement: ForecastRequirement) -> Any | None:
        if not requirement.require_estimated_qnh:
            return None
        if self.terrain_cache_path is None:
            raise RuntimeError(
                "MSM estimated QNH requires terrain data. Configure terrain_cache_path "
                "with an existing jma-msm-wind GridTerrainProvider cache before "
                "preparing the forecast run."
            )
        if not self.terrain_cache_path.is_file():
            raise RuntimeError(
                "MSM estimated QNH terrain cache is missing or is not a file: "
                f"{self.terrain_cache_path}. Provide a valid cache via "
                "terrain_cache_path before preparing the forecast run."
            )
        try:
            terrain = self._msm.GridTerrainProvider.load(self.terrain_cache_path)
        except Exception as error:
            raise RuntimeError(
                "MSM estimated QNH terrain cache could not be loaded: "
                f"{self.terrain_cache_path}. Replace or regenerate the terrain cache "
                "before preparing the forecast run."
            ) from error
        if terrain is None:
            raise RuntimeError(
                "MSM estimated QNH terrain cache loader returned no terrain provider: "
                f"{self.terrain_cache_path}. Replace or regenerate the terrain cache "
                "before preparing the forecast run."
            )
        return terrain

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        if forecast_run_id not in self._prepared:
            raise RuntimeError("MSM forecast run must be prepared before querying")
        queries = [self._to_query(request) for request in requests]
        results = self._prepared[forecast_run_id].query_many(queries)
        if len(results) != len(requests):
            raise RuntimeError("MSM batch result count does not match request count")
        return tuple(
            self._from_result(request, result)
            for request, result in zip(requests, results, strict=True)
        )

    def _to_query(self, request: WeatherRequest) -> Any:
        if request.kind == WeatherRequestKind.ALOFT:
            if request.altitude_ft_msl is None:
                raise ValueError("aloft weather request requires altitude")
            return self._msm.AloftQuery(
                request.latitude_deg,
                request.longitude_deg,
                request.valid_time_utc,
                altitude_msl_m=request.altitude_ft_msl * FT_TO_M,
            )
        if request.elevation_ft_msl is None:
            raise ValueError("estimated QNH request requires airport elevation")
        return self._msm.EstimatedQnhQuery(
            request.latitude_deg,
            request.longitude_deg,
            request.valid_time_utc,
            elevation_msl_m=request.elevation_ft_msl * FT_TO_M,
        )

    def _from_result(self, request: WeatherRequest, result: Any) -> WeatherResult:
        available = result.availability == self._msm.Availability.AVAILABLE
        values = dict(result.values)
        if request.kind == WeatherRequestKind.ESTIMATED_QNH and available:
            values["provider_label"] = values.get("label")
            values["label"] = "MSM推定QNH"
        warnings = tuple(result.warnings)
        if request.kind == WeatherRequestKind.ESTIMATED_QNH:
            warnings = tuple(
                warning
                for warning in warnings
                if warning
                not in {
                    "ESTIMATED_QNH_NOT_OFFICIAL",
                    "VERIFY_WITH_OFFICIAL_AERODROME_QNH",
                }
            )
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE if available else Availability.UNAVAILABLE,
            kind=request.kind,
            values=values,
            reason_code=result.reason_code,
            warnings=warnings,
            metadata={"provenance": _jsonable(result.provenance)},
        )
