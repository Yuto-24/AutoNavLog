from __future__ import annotations

import threading
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


class MsmMslpWeatherProvider:
    """MSM aloft weather plus forecast MSLP, without terrain/Pzs.

    jma-msm-wind 0.2.1 already decodes and normalizes MSLP but does not expose a
    public query.  The compatibility branch below is intentionally isolated and
    automatically uses ForecastMslpQuery when a newer package provides it.
    """

    package_version = "0.2.1"

    def __init__(self, cache_dir: str | Path, client: Any | None = None) -> None:
        try:
            import msm_wind
        except ImportError as error:
            raise RuntimeError("jma-msm-wind 0.2.1 is required for MSM weather") from error
        if getattr(msm_wind, "__version__", None) != self.package_version:
            raise RuntimeError("MsmMslpWeatherProvider requires jma-msm-wind 0.2.1 exactly")
        self._msm = msm_wind
        self.client = client or msm_wind.MsmClient(cache_dir=cache_dir)
        self._prepared: dict[str, Any] = {}
        self._requirements: dict[str, ForecastRequirement] = {}
        self._lock = threading.RLock()

    def _native_requirement(self, requirement: ForecastRequirement) -> Any:
        variables = set()
        if requirement.require_aloft_wind:
            variables.add(self._msm.WeatherVariable.ALOFT_WIND)
        if requirement.require_aloft_temperature:
            variables.add(self._msm.WeatherVariable.ALOFT_TEMPERATURE)
        if requirement.require_estimated_qnh:
            public_mslp = getattr(self._msm.WeatherVariable, "FORECAST_MSLP", None)
            variables.add(public_mslp or self._msm.WeatherVariable.ESTIMATED_QNH)
        return self._msm.ForecastRequirements(
            valid_times=requirement.valid_times_utc,
            variables=frozenset(variables),
        )

    def _run_id(self, value: str) -> Any:
        parsed = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return self._msm.RunId(parsed)

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        status = self.client.resolve_run(self._native_requirement(requirement), selected_run=None)
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
            self._native_requirement(requirement),
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

    @staticmethod
    def _combine(
        previous: ForecastRequirement | None,
        current: ForecastRequirement,
    ) -> ForecastRequirement:
        if previous is None:
            return current
        return ForecastRequirement(
            valid_times_utc=(*previous.valid_times_utc, *current.valid_times_utc),
            require_aloft_wind=previous.require_aloft_wind or current.require_aloft_wind,
            require_aloft_temperature=(
                previous.require_aloft_temperature or current.require_aloft_temperature
            ),
            require_estimated_qnh=(previous.require_estimated_qnh or current.require_estimated_qnh),
        )

    def prepare_run(
        self,
        forecast_run_id: str,
        requirement: ForecastRequirement,
    ) -> PreparedForecastRun:
        with self._lock:
            combined = self._combine(self._requirements.get(forecast_run_id), requirement)
            if self._requirements.get(forecast_run_id) != combined:
                self._prepared[forecast_run_id] = self.client.prepare_run(
                    self._run_id(forecast_run_id),
                    self._native_requirement(combined),
                    terrain_provider=None,
                )
                self._requirements[forecast_run_id] = combined
                while len(self._prepared) > 4:
                    oldest_run_id = next(iter(self._prepared))
                    if oldest_run_id == forecast_run_id and len(self._prepared) > 1:
                        oldest_run_id = next(iter(tuple(self._prepared)[1:]))
                    self._prepared.pop(oldest_run_id, None)
                    self._requirements.pop(oldest_run_id, None)
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={
                "provider": "jma-msm-wind",
                "package_version": self.package_version,
                "qnh_input": "forecast_mslp",
                "terrain_required": False,
                "prepared_valid_times_utc": [
                    item.isoformat() for item in self._requirements[forecast_run_id].valid_times_utc
                ],
            },
        )

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        with self._lock:
            prepared = self._prepared.get(forecast_run_id)
            if prepared is None:
                raise RuntimeError("MSM forecast run must be prepared before querying")
            return tuple(self._query(prepared, request) for request in requests)

    def _query(self, prepared: Any, request: WeatherRequest) -> WeatherResult:
        if request.kind == WeatherRequestKind.ALOFT:
            if request.altitude_ft_msl is None:
                raise ValueError("aloft weather request requires altitude")
            native = prepared.query(
                self._msm.AloftQuery(
                    request.latitude_deg,
                    request.longitude_deg,
                    request.valid_time_utc,
                    altitude_msl_m=request.altitude_ft_msl * FT_TO_M,
                )
            )
            return self._result(request, native)

        public_query = getattr(self._msm, "ForecastMslpQuery", None)
        if public_query is not None:
            native = prepared.query(
                public_query(
                    request.latitude_deg,
                    request.longitude_deg,
                    request.valid_time_utc,
                )
            )
            return self._result(request, native)

        sampled = prepared._surface_scalar(
            "mslp",
            request.latitude_deg,
            request.longitude_deg,
            request.valid_time_utc,
        )
        if sampled is None:
            return WeatherResult(
                request_id=request.request_id,
                availability=Availability.UNAVAILABLE,
                kind=request.kind,
                values={"label": "MSM MSLP単独推定QNH", "qnh_hpa": None},
                reason_code="MSM_MSLP_UNAVAILABLE",
                warnings=(
                    "ESTIMATED_QNH_NOT_OFFICIAL",
                    "VERIFY_WITH_OFFICIAL_AERODROME_QNH",
                ),
                metadata={"provider": "jma-msm-wind-mslp-compat"},
            )
        value_pa, trace = sampled
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values={
                "label": "MSM MSLP単独推定QNH",
                "qnh_method": "MSM_MSLP_ONLY",
                "qnh_hpa": round(float(value_pa) / 100.0, 1),
                "mslp_pa": float(value_pa),
                "mslp_hpa": float(value_pa) / 100.0,
            },
            warnings=(
                "ESTIMATED_QNH_NOT_OFFICIAL",
                "VERIFY_WITH_OFFICIAL_AERODROME_QNH",
            ),
            metadata={
                "provider": "jma-msm-wind-mslp-compat",
                "provenance": _jsonable(
                    prepared._provenance("bilinear,time-linear", {"mslp": trace})
                ),
            },
        )

    def _result(self, request: WeatherRequest, native: Any) -> WeatherResult:
        available = native.availability == self._msm.Availability.AVAILABLE
        values = dict(native.values)
        if available:
            mslp_pa = values.get("mslp_pa")
            if isinstance(mslp_pa, (int, float)):
                values["mslp_hpa"] = float(mslp_pa) / 100.0
                values["qnh_hpa"] = round(float(mslp_pa) / 100.0, 1)
            values["label"] = "MSM MSLP単独推定QNH"
            values["qnh_method"] = "MSM_MSLP_ONLY"
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE if available else Availability.UNAVAILABLE,
            kind=request.kind,
            values=values,
            reason_code=native.reason_code,
            warnings=tuple(
                dict.fromkeys(
                    (
                        *native.warnings,
                        "ESTIMATED_QNH_NOT_OFFICIAL",
                        "VERIFY_WITH_OFFICIAL_AERODROME_QNH",
                    )
                )
            ),
            metadata={
                "provider": "jma-msm-wind",
                "provenance": _jsonable(native.provenance),
            },
        )
