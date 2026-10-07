"""GSM Japan projection of the same public weather-query contract as MSM."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autonavlog.domain.weather import ForecastModel, ForecastRequirement, PreparedForecastRun
from autonavlog.weather.msm_adapter import MsmWeatherProvider


class GsmWeatherProvider(MsmWeatherProvider):
    model: ForecastModel = "GSM"

    def __init__(self, cache_dir: str | Path, client: Any | None = None):
        import jma_gpv_weather

        super().__init__(cache_dir, client=client or jma_gpv_weather.GsmClient(cache_dir=cache_dir))
        self._native_bounds = self.client.bounds if client is None else None

    def _prepare_candidate(
        self, run: str, requirement: ForecastRequirement, selections: Any,
    ) -> Any:
        self._expand_native_bounds(requirement)
        return self.client.prepare_run(
            self._run_id(run), self._requirement(requirement), available_runs=selections,
        )

    def prepare_run(
        self, forecast_run_id: str, requirement: ForecastRequirement,
    ) -> PreparedForecastRun:
        if self._checked_requirements.get(forecast_run_id) != requirement:
            self._expand_native_bounds(requirement)
            self._prepared[forecast_run_id] = self.client.prepare_run(
                self._run_id(forecast_run_id), self._requirement(requirement),
            )
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={
                "provider": "jma-gpv-weather",
                "package_version": self.package_version,
                "model": self.model,
                "surface_temperature_required": requirement.require_surface_temperature,
            },
        )
