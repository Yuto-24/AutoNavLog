"""Deterministic #161 fixtures, NOT operational weather.

GSM uses the upstream native prepare path with a synthetic GRIB decoder. The
published bytes use its public portable codec. MSM variants start from the
captured #144 records and deliberately change HGT or source values for policy
acceptance. No weather algorithms from this file ship in the application.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
from jma_gpv_weather import (
    Bounds,
    ForecastRequirements,
    GsmClient,
    GsmPreparedData,
    MsmPreparedData,
    RunId,
    WeatherVariable,
)
from jma_gpv_weather.gsm import spec

ROOT = Path(__file__).resolve().parent
INITIAL = datetime(2026, 9, 15, tzinfo=UTC)
BOUNDS = Bounds(31, 35, 130, 133)


class Source:
    def directory_url(self, day):
        return GsmClient().source.directory_url(day)

    def read_listing(self, url):
        def fd(hour):
            return f"{hour // 24:02d}{hour % 24:02d}"

        names = []
        for run in (INITIAL, INITIAL + timedelta(hours=6)):
            if url != self.directory_url(run.date()):
                continue
            for kind, intervals in spec.FILE_INTERVALS.items():
                for first, last in intervals:
                    if last <= spec.horizon(run):
                        names.append(
                            f"Z__C_RJTD_{run:%Y%m%d%H%M%S}_GSM_GPV_Rjp_Gll0p1deg_"
                            f"{kind}_FD{fd(first)}-{fd(last)}_grib2.bin"
                        )
        return "\n".join(names)

    def download(self, remote, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"GRIB" + remote.name.encode() + b"7777")
        return destination


def records(paths, target_date, bounds, valid_times, *, pressure_levels, initial):
    lat = np.array([[35., 35.], [31., 31.]])
    lon = np.array([[130., 133.], [130., 133.]])
    surface, pressure = {}, {}
    for valid in valid_times:
        # Source scheduling and required levels are supplied by the native client.
        hour = (valid - initial).total_seconds() / 3600
        if hour in spec.forecast_hours(initial, "Lsurf"):
            surface[valid, 2, "tmp_surface"] = (np.full_like(lat, 293.15), lat, lon)
        if hour not in spec.forecast_hours(initial, "L-pall"):
            continue
        for level in pressure_levels:
            height = (1000 - level) * 20 + 100
            for name, value in (("hgt", height), ("u", 4.), ("v", -3.),
                                ("tmp", 293.15 - height * .0065)):
                pressure[valid, level, name] = (np.full_like(lat, value), lat, lon)
    return surface, pressure


def write_feed(directory, catalog, payloads):
    directory.mkdir(parents=True, exist_ok=True)
    assets = []
    for run, payload in payloads:
        digest = hashlib.sha256(payload).hexdigest()
        filename = f"{digest}.npz"
        (directory / filename).write_bytes(payload)
        assets.append({"run": run, "file": filename, "sha256": digest, "bytes": len(payload)})
    catalog = {**catalog, "assets": assets}
    (directory / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")


def generate():
    req = ForecastRequirements(
        tuple(datetime(2026, 9, 16, hour, tzinfo=UTC) for hour in range(0, 10)),
        frozenset({WeatherVariable.ALOFT_WIND, WeatherVariable.ALOFT_TEMPERATURE,
                   WeatherVariable.SURFACE_TEMPERATURE}),
    )
    with TemporaryDirectory() as cache:
        client = GsmClient(cache, BOUNDS, source=Source())
        runs = client.discover_runs(req)
        payloads = []
        for selection in runs:
            run = RunId(selection.run_utc)
            with patch("jma_gpv_weather.grib.read_grib_records",
                       side_effect=partial(records, initial=selection.run_utc)):
                forecast = client.prepare_run(run, req, available_runs=runs)
                payloads.append((str(run), GsmPreparedData.from_forecast(forecast).to_bytes()))
        write_feed(ROOT / "forecast-policy/gsm", {
            "schema_version": 1, "generated_at": "2026-09-16T00:00:00Z",
            "expires_at": "2026-09-16T06:00:00Z",
            "listings": {url: client.source.read_listing(url) for url in client.listing_urls(req)},
        }, payloads)
    original = json.loads((ROOT / "msm-portable/catalog.json").read_text())
    # A synthetic source offers exactly the two recorded Runs. Production must
    # never filter discovery by generated assets: a missing older candidate is a
    # delivery failure, not evidence that all MSM Runs are out of coverage.
    fixture_runs = {asset["run"] for asset in original["assets"]}
    original["listings"] = {
        url: "\n".join(line for line in text.splitlines()
                       if any(run in line for run in fixture_runs))
        for url, text in original["listings"].items()
    }
    for variant in ("msm-outside-hgt", "msm-source-unavailable", "msm-newest-outside-hgt"):
        payloads = []
        for asset in original["assets"]:
            data = MsmPreparedData.from_bytes((ROOT / "msm-portable" / asset["file"]).read_bytes())
            pressure = dict(data.pressure)
            for key, (values, lat, lon) in pressure.items():
                if variant == "msm-source-unavailable" and key[2] == "u":
                    pressure[key] = (np.full_like(values, np.nan), lat, lon)
                elif key[2] == "hgt" and (variant == "msm-outside-hgt" or
                        (variant == "msm-newest-outside-hgt" and asset["run"] == "20260915210000")):
                    pressure[key] = (values / 1000, lat, lon)
            changed = MsmPreparedData(data.selection, data.surface, pressure, data.source_hashes)
            payloads.append((asset["run"], changed.to_bytes()))
        write_feed(ROOT / "forecast-policy" / variant, original, payloads)


if __name__ == "__main__":
    generate()
