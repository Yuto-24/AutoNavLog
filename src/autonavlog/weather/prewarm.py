from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

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
    ) -> None:
        self._provider = provider
        self._horizon = horizon
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
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
            self._stop.wait(self._interval.total_seconds())

    def run_once(self) -> str:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        hours = int(self._horizon.total_seconds() // 3600)
        requirement = ForecastRequirement(
            valid_times_utc=tuple(now + timedelta(hours=offset) for offset in range(hours + 1)),
            require_aloft_wind=True,
            require_aloft_temperature=True,
            require_estimated_qnh=True,
        )
        run = self._provider.resolve_run(requirement)
        self._provider.prepare_run(run.id, requirement)
        return run.id

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
