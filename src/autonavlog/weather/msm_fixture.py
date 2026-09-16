"""Offline acceptance data for Local Calculation, not an MSM acquisition adapter.

Reuse the pinned library's run selection, interpolation, and AutoNavLog projection.
Only discovery/download are replaced by checked-in, decoded MSM subsets (#125).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jma_gpv_weather import MsmClient, PreparedForecast
from jma_gpv_weather.errors import NoCompatibleRunError, SelectedRunCoverageError
from jma_gpv_weather.models import RemoteFile
from jma_gpv_weather.msm.client import select_compatible_runs

from autonavlog.weather.msm_adapter import MsmWeatherProvider

FIXTURE_WEATHER_LABEL = "固定MSM fixture（2026-09-12 12:00–15:00 JST・実予報取得なし）"


class FixtureMsmClient(MsmClient):  # type: ignore[misc]
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.manifest = json.loads((directory / "manifest.json").read_text())
        self._loaded: dict[str, Any] = {}
        self._files = []
        for run in self.manifest["runs"]:
            initial = datetime.strptime(run["id"], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            for source in run["sources"]:
                self._files.append(
                    RemoteFile(
                        source["url"].rsplit("/", 1)[-1],
                        source["url"],
                        initial,
                        "L-pall" if source["url"].endswith("P.nc") else "Lsurf",
                        3 - initial.hour,
                        6 - initial.hour,
                    )
                )

    def discover_runs(self, requirements: Any) -> Any:
        runs = select_compatible_runs(self._files, requirements)
        if not runs:
            raise NoCompatibleRunError(
                "固定MSM fixtureの時間範囲は2026-09-12 12:00–15:00 JSTです。"
            )
        return runs

    def prepare_run(self, run: Any, requirements: Any, terrain_provider: Any = None) -> Any:
        selection = next(
            (
                item
                for item in self.discover_runs(requirements)
                if item.run_utc == run.initial_time_utc
            ),
            None,
        )
        if selection is None:
            raise SelectedRunCoverageError(f"selected run {run} is outside fixture coverage")
        run_id = str(run)
        if run_id not in self._loaded:
            import numpy as np

            entry = next(item for item in self.manifest["runs"] if item["id"] == run_id)
            path = self.directory / entry["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise ValueError("MSM fixture SHA-256 mismatch")
            records: dict[str, dict[Any, Any]] = {"P": {}, "S": {}}
            with np.load(path, allow_pickle=False) as archive:
                data = {name: archive[name] for name in archive.files}
                for kind in records:
                    lon, lat = np.meshgrid(data[f"{kind}_lon"], data[f"{kind}_lat"])
                    for ti, time in enumerate(data[f"{kind}_times"]):
                        valid = datetime.fromisoformat(str(time))
                        if kind == "P":
                            for li, level in enumerate(data["levels"]):
                                for source, target in (
                                    ("z", "hgt"),
                                    ("u", "u"),
                                    ("v", "v"),
                                    ("temp", "tmp"),
                                ):
                                    records[kind][valid, int(level), target] = (
                                        data[f"P_{source}"][ti, li],
                                        lat,
                                        lon,
                                    )
                        else:
                            records[kind][valid, 0, "tmp_surface"] = (
                                data["S_temp"][ti],
                                lat,
                                lon,
                            )
            self._loaded[run_id] = PreparedForecast(
                selection,
                records["S"],
                records["P"],
                {entry["file"]: entry["sha256"]},
            )
        return self._loaded[run_id]


def fixture_weather_provider(directory: Path) -> MsmWeatherProvider:
    return MsmWeatherProvider(directory, client=FixtureMsmClient(directory))
