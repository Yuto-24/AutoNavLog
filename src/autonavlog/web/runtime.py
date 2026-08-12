from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from autonavlog.weather.msm_metar_trend_provider import MsmMetarTrendQnhProvider
from autonavlog.weather.msm_mslp_provider import MsmMslpWeatherProvider
from autonavlog.weather.prewarm import WeatherPrewarmer
from autonavlog.weather.provider import WeatherProvider

from .cloudflare_access import CloudflareAccessVerifier
from .facade import AutoNavLogWebApplication

WeatherMode = Literal["fake", "msm", "msm-metar", "msm-metar-trend"]
LOGGER = logging.getLogger(__name__)


def environment_bool(name: str, *, default: bool) -> bool:
    """Read one strict boolean environment setting."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


@dataclass(frozen=True)
class WebRuntimeConfig:
    data_root: Path
    storage_root: Path
    weather_mode: WeatherMode = "fake"
    msm_cache_dir: Path | None = None
    terrain_cache_path: Path | None = None
    maximum_sessions: int = 256
    trusted_local_identity: str | None = None
    session_cookie_secure: bool = True
    cloudflare_team_domain: str | None = None
    cloudflare_access_audience: str | None = None

    def __post_init__(self) -> None:
        if self.weather_mode not in {"fake", "msm", "msm-metar", "msm-metar-trend"}:
            raise ValueError(f"unsupported weather mode: {self.weather_mode}")
        if self.maximum_sessions < 1:
            raise ValueError("maximum_sessions must be positive")

        if bool(self.cloudflare_team_domain) != bool(self.cloudflare_access_audience):
            raise ValueError(
                "cloudflare_team_domain and cloudflare_access_audience must be set together"
            )

        trimmed_identity = (
            self.trusted_local_identity.strip() if self.trusted_local_identity else ""
        )
        if not self.session_cookie_secure and (not trimmed_identity or self.cloudflare_team_domain):
            raise ValueError(
                "insecure session cookies require trusted local identity without Cloudflare Access"
            )


def _prune_msm_cache(
    cache_dir: Path,
    *,
    maximum_bytes: int = 20 * 1024**3,
    maximum_age: timedelta = timedelta(days=7),
) -> tuple[int, int]:
    """Prune stale/oversized MSM files and return deleted count and remaining bytes."""
    if not cache_dir.is_dir():
        LOGGER.info("MSM cache cleanup completed: deleted_files=0 remaining_bytes=0")
        return 0, 0
    deleted = 0
    cutoff = datetime.now(timezone.utc).timestamp() - maximum_age.total_seconds()
    files: list[tuple[float, int, Path]] = []
    for path in cache_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            try:
                path.unlink()
            except OSError:
                files.append((stat.st_mtime, stat.st_size, path))
            else:
                deleted += 1
            continue
        files.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= maximum_bytes:
            break
        try:
            path.unlink()
        except OSError:
            continue
        total -= size
        deleted += 1
    LOGGER.info(
        "MSM cache cleanup completed: deleted_files=%s remaining_bytes=%s",
        deleted,
        total,
    )
    return deleted, total


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

    if config.weather_mode == "msm-metar":

        def create_msm_metar() -> WeatherProvider:
            delegate = MsmWeatherProvider(
                cache_dir=cache_dir,
                terrain_cache_path=config.terrain_cache_path,
            )
            return MsmMetarWeatherProvider(delegate)

        return create_msm_metar, "MSM予報・METAR観測QNH", False

    if config.weather_mode != "msm-metar-trend":
        raise ValueError(f"unsupported weather mode: {config.weather_mode}")

    _prune_msm_cache(cache_dir)
    shared_provider = MsmMetarTrendQnhProvider(
        MsmMslpWeatherProvider(cache_dir=cache_dir),
        cache_ttl_seconds=300,
    )

    def shared_msm_metar_trend() -> WeatherProvider:
        """Return the process-wide provider with shared runs and METAR cache."""
        return shared_provider

    return shared_msm_metar_trend, "MSM予報・METAR補正付きMSM QNH推定", False


def build_web_application(config: WebRuntimeConfig) -> AutoNavLogWebApplication:
    """Build the production web facade and optional weather prewarmer."""
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
    weather_prewarmer = None
    if config.weather_mode == "msm-metar-trend":
        weather_prewarmer = WeatherPrewarmer(
            weather_factory(),
            cleanup=lambda: _prune_msm_cache(
                config.msm_cache_dir or (config.storage_root / "msm-cache")
            ),
        )
    access_verifier = None
    if config.cloudflare_team_domain and config.cloudflare_access_audience:
        access_verifier = CloudflareAccessVerifier(
            team_domain=config.cloudflare_team_domain,
            audience=config.cloudflare_access_audience,
        )
    return AutoNavLogWebApplication(
        project_service=project_service,
        airports=airports,
        performance=performance,
        reference_repository=reference_repository,
        trusted_local_identity=config.trusted_local_identity,
        reference_catalog=reference_catalog,
        weather_factory=weather_factory,
        weather_label=weather_label,
        development_weather=development_weather,
        maximum_sessions=config.maximum_sessions,
        access_verifier=access_verifier,
        weather_prewarmer=weather_prewarmer,
    )
