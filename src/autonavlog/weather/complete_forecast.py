"""Validate final requirements and actual queries through the provider's public API."""

from __future__ import annotations

from typing import Any

from autonavlog.domain.enums import Availability
from autonavlog.domain.weather import ForecastRequirement
from autonavlog.weather.local_msm import LocalWeatherError


class CompleteForecastProvider:
    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.failure_code: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    def _call(self, operation: str, *args: Any) -> Any:
        try:
            return getattr(self.provider, operation)(*args)
        except LocalWeatherError as error:
            self.failure_code = error.code
            raise

    def resolve_run(self, requirement: ForecastRequirement) -> Any:
        return self._call("resolve_run", requirement)

    def prepare_run(self, run: str, requirement: ForecastRequirement) -> Any:
        return self._call("prepare_run", run, requirement)

    def inspect_run_status(self, run: str, requirement: ForecastRequirement) -> Any:
        status = self._call("inspect_run_status", run, requirement)
        if status.selected_run_covers_requirement:
            self.prepare_run(run, requirement)
        else:
            self.failure_code = "FORECAST_RUN_OUT_OF_COVERAGE"
        return status

    def query_batch(self, run: str, requests: Any) -> Any:
        results = self._call("query_batch", run, requests)
        if any(item.availability != Availability.AVAILABLE for item in results):
            self.failure_code = "WEATHER_PREPARED_UNAVAILABLE"
            raise LocalWeatherError(self.failure_code, "選択したMSM Runの配信データがありません。")
        return results
