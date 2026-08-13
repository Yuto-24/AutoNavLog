from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, cast

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import (
    ForecastRequirement,
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)

from .msm_metar_provider import (
    _MAXIMUM_FUTURE_CLOCK_SKEW,
    MANUAL_QNH_WARNING,
    MsmMetarWeatherProvider,
)
from .provider import WeatherProvider

QNH_LABEL = "METAR補正付きMSM QNH推定値"
QNH_WARNINGS: tuple[str, ...] = ()
DEFAULT_METAR_STATIONS = ("RJFM", "RJFO")


class MsmMetarTrendQnhProvider:
    """MSM forecast weather with METAR-anchored MSLP pressure tendency."""

    def __init__(
        self,
        delegate: WeatherProvider,
        *,
        metar_provider: MsmMetarWeatherProvider | None = None,
        cache_ttl_seconds: float = 300.0,
        maximum_observation_age: timedelta = timedelta(hours=2),
    ) -> None:
        self._delegate = delegate
        self._metar = metar_provider or MsmMetarWeatherProvider(
            delegate,
            cache_ttl_seconds=cache_ttl_seconds,
            maximum_observation_offset=maximum_observation_age,
        )
        self._maximum_observation_age = maximum_observation_age
        self._prepared: set[str] = set()
        self.package_version = getattr(delegate, "package_version", None)

    def _now(self) -> datetime:
        return self._metar.current_time_utc()

    def _lookups(self, now: datetime) -> dict[str, Any]:
        return self._metar.lookup_metars(DEFAULT_METAR_STATIONS, now)

    def _augment_requirement(
        self,
        requirement: ForecastRequirement,
    ) -> tuple[ForecastRequirement, dict[str, Any]]:
        if not requirement.require_estimated_qnh:
            return requirement, {}
        lookups = self._lookups(self._now())
        metar_times = tuple(
            lookup.observation.observation_time_utc
            for lookup in lookups.values()
            if lookup.observation is not None
        )
        return (
            ForecastRequirement(
                valid_times_utc=(*requirement.valid_times_utc, *metar_times),
                require_aloft_wind=requirement.require_aloft_wind,
                require_aloft_temperature=requirement.require_aloft_temperature,
                require_estimated_qnh=True,
            ),
            lookups,
        )

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        """Select one MSM run covering target and accepted METAR times."""
        augmented, _ = self._augment_requirement(requirement)
        return self._delegate.resolve_run(augmented)

    def inspect_run_status(
        self,
        selected_run_id: str,
        requirement: ForecastRequirement,
    ) -> RunSelectionStatus:
        """Inspect whether the selected run covers the augmented requirement."""
        augmented, _ = self._augment_requirement(requirement)
        return self._delegate.inspect_run_status(selected_run_id, augmented)

    def prepare_run(
        self,
        forecast_run_id: str,
        requirement: ForecastRequirement,
    ) -> PreparedForecastRun:
        """Prepare a run shared by aloft and trend-corrected QNH queries."""
        augmented, _ = self._augment_requirement(requirement)
        delegated = self._delegate.prepare_run(forecast_run_id, augmented)
        self._prepared.add(forecast_run_id)
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={
                "provider": "msm-metar-trend",
                "aloft_provider": "MSM",
                "qnh_provider": QNH_LABEL,
                "qnh_formula": (
                    "METAR_QNH + MSM_MSLP(target_time) - MSM_MSLP(metar_observation_time)"
                ),
                "metar_stations": list(DEFAULT_METAR_STATIONS),
                "metar_cache_ttl_seconds": self._metar.cache_ttl_seconds,
                "terrain_required": False,
                "delegate": delegated.metadata,
            },
        )

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        """Query aloft weather and estimated QNH while preserving request order."""
        if forecast_run_id not in self._prepared:
            raise RuntimeError("MSM/METAR trend run must be prepared before querying")

        results: list[WeatherResult | None] = [None] * len(requests)
        aloft = tuple(
            (index, request)
            for index, request in enumerate(requests)
            if request.kind == WeatherRequestKind.ALOFT
        )
        if aloft:
            delegated = tuple(
                self._delegate.query_batch(
                    forecast_run_id,
                    tuple(request for _, request in aloft),
                )
            )
            if len(delegated) != len(aloft):
                raise RuntimeError("MSM delegate batch result count does not match request count")
            for (index, request), result in zip(aloft, delegated, strict=True):
                if result.request_id != request.request_id or result.kind != request.kind:
                    raise RuntimeError("MSM delegate changed weather request identity or order")
                results[index] = result

        qnh = tuple(
            (index, request)
            for index, request in enumerate(requests)
            if request.kind == WeatherRequestKind.ESTIMATED_QNH
        )
        if qnh:
            now = self._now()
            lookups = self._lookups(now)
            for index, request in qnh:
                results[index] = self._estimate_qnh(
                    forecast_run_id,
                    request,
                    now,
                    lookups,
                )

        if any(result is None for result in results):
            raise RuntimeError("unsupported weather request kind")
        return tuple(cast(WeatherResult, result) for result in results)

    @staticmethod
    def _station(request: WeatherRequest) -> str | None:
        value = request.metadata.get("station_icao")
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        return normalized if normalized in DEFAULT_METAR_STATIONS else None

    def _mslp_request(
        self,
        source: WeatherRequest,
        *,
        request_id: str,
        valid_time: datetime,
    ) -> WeatherRequest:
        return WeatherRequest(
            request_id=request_id,
            kind=WeatherRequestKind.ESTIMATED_QNH,
            latitude_deg=source.latitude_deg,
            longitude_deg=source.longitude_deg,
            valid_time_utc=valid_time,
            elevation_ft_msl=source.elevation_ft_msl,
            metadata=source.metadata,
        )

    def _estimate_qnh(
        self,
        forecast_run_id: str,
        request: WeatherRequest,
        now: datetime,
        lookups: dict[str, Any],
    ) -> WeatherResult:
        station = self._station(request)
        lookup = None if station is None else lookups.get(station)
        observation = None if lookup is None else lookup.observation
        fallback_reason = "METAR_STATION_UNSUPPORTED" if station is None else None
        if lookup is not None and observation is None:
            fallback_reason = lookup.reason_code or "METAR_QNH_UNAVAILABLE"

        baseline_request = None
        if observation is not None:
            age = now - observation.observation_time_utc
            if age < -_MAXIMUM_FUTURE_CLOCK_SKEW:
                fallback_reason = "METAR_OBSERVATION_FROM_FUTURE"
            elif age > self._maximum_observation_age:
                fallback_reason = "METAR_OBSERVATION_STALE"
            else:
                baseline_request = self._mslp_request(
                    request,
                    request_id=f"{request.request_id}:metar-mslp",
                    valid_time=observation.observation_time_utc,
                )

        target_request = self._mslp_request(
            request,
            request_id=f"{request.request_id}:target-mslp",
            valid_time=request.valid_time_utc,
        )
        delegated_requests = (
            (target_request,) if baseline_request is None else (target_request, baseline_request)
        )
        delegated = tuple(self._delegate.query_batch(forecast_run_id, delegated_requests))
        if len(delegated) != len(delegated_requests):
            raise RuntimeError("MSM delegate batch result count does not match QNH request count")
        for delegated_request, delegated_result in zip(
            delegated_requests,
            delegated,
            strict=True,
        ):
            if (
                delegated_result.request_id != delegated_request.request_id
                or delegated_result.kind != delegated_request.kind
            ):
                raise RuntimeError("MSM delegate changed QNH request identity or order")

        target = delegated[0]
        target_mslp = self._mslp_hpa(target)
        if target_mslp is None:
            return WeatherResult(
                request_id=request.request_id,
                availability=Availability.UNAVAILABLE,
                kind=request.kind,
                values={
                    "label": QNH_LABEL,
                    "qnh_method": "MANUAL",
                    "qnh_hpa": None,
                    "forecast_time_utc": request.valid_time_utc.isoformat(),
                },
                reason_code=target.reason_code or "MSM_MSLP_UNAVAILABLE",
                warnings=(*QNH_WARNINGS, MANUAL_QNH_WARNING),
                metadata={
                    "provider": "msm-metar-trend",
                    "msm_target": target.metadata,
                },
            )

        if observation is not None and baseline_request is not None:
            age = now - observation.observation_time_utc
            baseline = delegated[1]
            baseline_mslp = self._mslp_hpa(baseline)
            if baseline_mslp is not None:
                tendency = target_mslp - baseline_mslp
                estimate = round(observation.qnh_hpa + tendency, 1)
                if 800 < estimate < 1100:
                    provenance = self._metar.observation_provenance(
                        observation,
                        age,
                        request.valid_time_utc - observation.observation_time_utc,
                        request.valid_time_utc,
                    )
                    return WeatherResult(
                        request_id=request.request_id,
                        availability=Availability.AVAILABLE,
                        kind=request.kind,
                        values={
                            "label": QNH_LABEL,
                            "qnh_method": "METAR_TREND_CORRECTED",
                            "qnh_hpa": estimate,
                            "metar_qnh_hpa": observation.qnh_hpa,
                            "metar_observation_time_utc": (
                                observation.observation_time_utc.isoformat()
                            ),
                            "forecast_time_utc": request.valid_time_utc.isoformat(),
                            "msm_target_mslp_hpa": target_mslp,
                            "msm_metar_time_mslp_hpa": baseline_mslp,
                            "msm_tendency_hpa": round(tendency, 1),
                        },
                        warnings=QNH_WARNINGS,
                        metadata={
                            "provider": "msm-metar-trend",
                            "qnh_method": "METAR_TREND_CORRECTED",
                            "metar": provenance,
                            "msm_target": target.metadata,
                            "msm_baseline": baseline.metadata,
                        },
                    )
                fallback_reason = "ESTIMATED_QNH_OUT_OF_RANGE"
            else:
                fallback_reason = baseline.reason_code or "MSM_BASELINE_MSLP_UNAVAILABLE"

        effective_fallback_reason = fallback_reason or "METAR_QNH_UNAVAILABLE"
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values={
                "label": "MSM MSLP単独推定QNH",
                "qnh_method": "MSM_MSLP_ONLY",
                "qnh_hpa": round(target_mslp, 1),
                "forecast_time_utc": request.valid_time_utc.isoformat(),
                "msm_target_mslp_hpa": target_mslp,
                "fallback_reason": effective_fallback_reason,
            },
            reason_code=effective_fallback_reason,
            warnings=QNH_WARNINGS,
            metadata={
                "provider": "msm-metar-trend",
                "qnh_method": "MSM_MSLP_ONLY",
                "metar_fallback_reason": effective_fallback_reason,
                "metar": {} if lookup is None else lookup.provenance,
                "msm_target": target.metadata,
            },
        )

    @staticmethod
    def _mslp_hpa(result: WeatherResult) -> float | None:
        if result.availability != Availability.AVAILABLE:
            return None
        value = result.values.get("mslp_hpa")
        if isinstance(value, (int, float)):
            return float(value)
        value = result.values.get("mslp_pa")
        if isinstance(value, (int, float)):
            return float(value) / 100.0
        value = result.values.get("qnh_hpa")
        return float(value) if isinstance(value, (int, float)) else None
