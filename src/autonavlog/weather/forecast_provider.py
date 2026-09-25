"""Session-scoped dispatch; model selection policy lives in the application."""

from collections.abc import Mapping, Sequence

from autonavlog.domain.weather import (
    ForecastModel,
    ForecastRequirement,
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)
from autonavlog.weather.provider import ForecastCandidateProvider


class ForecastWeatherProvider:
    def __init__(self, models: Mapping[ForecastModel, ForecastCandidateProvider]):
        self.models = models
        self.selected_model: ForecastModel = "MSM"

    def begin_calculation(self) -> None:
        for provider in self.models.values():
            begin = getattr(provider, "begin_calculation", None)
            if begin is not None:
                begin()

    @property
    def current(self) -> ForecastCandidateProvider:
        return self.models[self.selected_model]

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        return self.current.resolve_run(requirement)

    def inspect_run_status(
        self, selected_run_id: str, requirement: ForecastRequirement,
    ) -> RunSelectionStatus:
        return self.current.inspect_run_status(selected_run_id, requirement)

    def prepare_run(
        self, forecast_run_id: str, requirement: ForecastRequirement,
    ) -> PreparedForecastRun:
        return self.current.prepare_run(forecast_run_id, requirement)

    def query_batch(
        self, forecast_run_id: str, requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        return self.current.query_batch(forecast_run_id, requests)
