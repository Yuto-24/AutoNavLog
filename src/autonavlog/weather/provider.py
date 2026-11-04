from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from autonavlog.domain.weather import (
    ForecastRequirement,
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)


@runtime_checkable
class WeatherProvider(Protocol):
    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun: ...

    def inspect_run_status(
        self,
        selected_run_id: str,
        requirement: ForecastRequirement,
    ) -> RunSelectionStatus: ...

    def prepare_run(
        self,
        forecast_run_id: str,
        requirement: ForecastRequirement,
    ) -> PreparedForecastRun: ...

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]: ...


class ForecastCandidateProvider(WeatherProvider, Protocol):
    def candidate_runs(self, requirement: ForecastRequirement) -> Sequence[str]:
        """Newest first; distinguish offline exclusion from discovery failure."""
        ...

    def check_run(self, run: str, requirement: ForecastRequirement) -> None:
        """Prepare and check the entire request, including actual HGT coverage."""
        ...
