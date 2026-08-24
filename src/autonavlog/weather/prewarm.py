from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from autonavlog.domain.weather import ForecastRequirement
from autonavlog.weather.provider import WeatherProvider

LOGGER = logging.getLogger(__name__)


class WeatherPrewarmer:
    """Keep a shared 48-hour MSM run ready without blocking request threads."""

    def __init__(
        self,
        provider: WeatherProvider,
        *,
        horizon: timedelta = timedelta(hours=48),
        interval: timedelta = timedelta(minutes=15),
        cleanup: Callable[[], object] | None = None,
    ) -> None:
        self._provider = provider
        self._horizon = horizon
        self._interval = interval
        self._cleanup = cleanup
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start or restart the background prewarm loop."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="autonavlog-msm-prewarm",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                LOGGER.exception(
                    "MSM 48-hour prewarm failed; on-demand preparation remains available"
                )
            finally:
                self._cleanup_once()
            self._stop.wait(self._interval.total_seconds())

    def _cleanup_once(self) -> None:
        if self._cleanup is None:
            return
        try:
            self._cleanup()
        except Exception:
            LOGGER.exception("MSM cache cleanup failed")

    def run_once(self) -> str:
        """Prepare one run covering the configured UTC horizon."""
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        hours = int(self._horizon.total_seconds() // 3600)
        requirement = ForecastRequirement(
            valid_times_utc=tuple(now + timedelta(hours=offset) for offset in range(hours + 1)),
            require_aloft_wind=True,
            require_aloft_temperature=True,
            require_surface_temperature=True,
        )
        run = self._provider.resolve_run(requirement)
        self._provider.prepare_run(run.id, requirement)
        return run.id

    def shutdown(self) -> None:
        """Stop the background loop while allowing a later restart."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
