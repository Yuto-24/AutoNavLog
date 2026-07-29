from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import (
    ForecastRequirement,
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)


def _default_result(request: WeatherRequest) -> WeatherResult:
    if request.kind == WeatherRequestKind.ESTIMATED_QNH:
        values: dict[str, float | str | None] = {
            "label": "MSM推定QNH",
            "qnh_hpa": 1013.0,
        }
        warnings = ("ESTIMATED_QNH_NOT_OFFICIAL",)
    else:
        values = {
            "u_ms": 0.0,
            "v_ms": 0.0,
            "wind_speed_kt": 0.0,
            "wind_direction_deg_from": None,
            "temperature_c": 15.0,
        }
        warnings = ("CALM_WIND_DIRECTION_UNDEFINED",)
    return WeatherResult(
        request_id=request.request_id,
        availability=Availability.AVAILABLE,
        kind=request.kind,
        values=values,
        warnings=warnings,
        metadata={"provider": "fake"},
    )


class FakeWeatherProvider:
    def __init__(
        self,
        runs: Sequence[str] = ("20260728000000",),
        result_factory: Callable[[WeatherRequest], WeatherResult] = _default_result,
    ):
        if not runs:
            raise ValueError("fake provider requires at least one run")
        self.runs = tuple(runs)
        self.result_factory = result_factory
        self.prepared: dict[str, ForecastRequirement] = {}
        self.query_history: list[tuple[str, tuple[WeatherRequest, ...]]] = []

    @staticmethod
    def _run_datetime(run_id: str) -> datetime:
        return datetime.strptime(run_id, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        run_id = self.runs[0]
        return ForecastRun(id=run_id, initial_time_utc=self._run_datetime(run_id))

    def inspect_run_status(
        self,
        selected_run_id: str,
        requirement: ForecastRequirement,
    ) -> RunSelectionStatus:
        covers = selected_run_id in self.runs
        return RunSelectionStatus(
            selected_run_id=selected_run_id,
            latest_compatible_run_id=self.runs[0],
            selected_run_covers_requirement=covers,
            update_available=covers and self.runs.index(selected_run_id) > 0,
            warnings=()
            if covers
            else ("SELECTED_RUN_OUT_OF_COVERAGE",),
        )

    def prepare_run(
        self,
        forecast_run_id: str,
        requirement: ForecastRequirement,
    ) -> PreparedForecastRun:
        status = self.inspect_run_status(forecast_run_id, requirement)
        if not status.selected_run_covers_requirement:
            raise ValueError("selected fake run does not cover requirement")
        self.prepared[forecast_run_id] = requirement
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={"provider": "fake"},
        )

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        if forecast_run_id not in self.prepared:
            raise ValueError("forecast run must be prepared before querying")
        batch = tuple(requests)
        self.query_history.append((forecast_run_id, batch))
        return tuple(self.result_factory(request) for request in batch)
