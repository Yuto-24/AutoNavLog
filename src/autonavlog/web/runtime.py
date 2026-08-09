from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from autonavlog.application.project_service import ProjectService
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.storage.airports import AirportRepository
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.storage.reference_data import ReferenceDataCatalogRepository
from autonavlog.weather.fake_provider import FakeWeatherProvider
from autonavlog.weather.msm_adapter import MsmWeatherProvider
from autonavlog.weather.msm_metar_provider import MsmMetarWeatherProvider
from autonavlog.weather.provider import WeatherProvider

from .facade import AutoNavLogWebApplication

WeatherMode = Literal["fake", "msm", "msm-metar"]


@dataclass(frozen=True)
class WebRuntimeConfig:
    data_root: Path
    storage_root: Path
    weather_mode: WeatherMode = "fake"
    msm_cache_dir: Path | None = None
    terrain_cache_path: Path | None = None
    maximum_sessions: int = 128


def _weather_factory(
    config: WebRuntimeConfig,
) -> tuple[Callable[[], WeatherProvider], str, bool]:
    if config.weather_mode == "fake":
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d000000")

        def create_fake() -> WeatherProvider:
            return FakeWeatherProvider(runs=(run_id,))

        return create_fake, "開発用固定気象（出力不可）", True

    cache_dir = config.msm_cache_dir or (config.storage_root / "msm-cache")
    if config.weather_mode == "msm":

        def create_msm() -> WeatherProvider:
            return MsmWeatherProvider(
                cache_dir=cache_dir,
                terrain_cache_path=config.terrain_cache_path,
            )

        return create_msm, "MSM予報・MSM推定QNH", False

    def create_msm_metar() -> WeatherProvider:
        delegate = MsmWeatherProvider(cache_dir=cache_dir)
        return MsmMetarWeatherProvider(delegate)

    return create_msm_metar, "MSM予報・METAR観測QNH", False


def build_web_application(config: WebRuntimeConfig) -> AutoNavLogWebApplication:
    data_root = config.data_root.resolve()
    storage_root = config.storage_root.resolve()
    reference_default = data_root / "reference" / "default"
    performance_root = data_root / "performance"
    if not reference_default.is_dir():
        raise RuntimeError(f"reference data directory is unavailable: {reference_default}")
    if not performance_root.is_dir():
        raise RuntimeError(f"performance data directory is unavailable: {performance_root}")

    reference_repository = ReferenceDataCatalogRepository(
        storage_root / "reference",
        bundled_default=reference_default,
    )
    reference_catalog = reference_repository.open_active()
    airports = AirportRepository.from_reference_catalog(reference_catalog)
    performance = PerformanceRepository.from_directory_for_application(performance_root)
    project_service = ProjectService(LocalProjectRepository(storage_root))
    weather_factory, weather_label, development_weather = _weather_factory(config)
    return AutoNavLogWebApplication(
        project_service=project_service,
        airports=airports,
        performance=performance,
        reference_repository=reference_repository,
        reference_catalog=reference_catalog,
        weather_factory=weather_factory,
        weather_label=weather_label,
        development_weather=development_weather,
        maximum_sessions=config.maximum_sessions,
    )
