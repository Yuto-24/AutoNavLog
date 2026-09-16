"""Produce a rolling static MSM feed using the upstream desktop public API.

This offline publisher has no HTTP endpoint and receives no Project data. Serve the
output as ordinary static files; the browser owns calculation and interpolation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError
from urllib.request import urlopen

from jma_gpv_weather import Bounds, ForecastRequirements, MsmClient, MsmPreparedData, RunId
from jma_gpv_weather import WeatherVariable as Variable

from autonavlog.weather.local_msm import PreparedAsset, WeatherCatalog


def produce(
    output: Path,
    cache: Path,
    start: datetime,
    hours: int,
    bounds: Bounds,
    *,
    now: datetime | None = None,
    max_runs: int = 2,
) -> dict[str, object]:
    started = perf_counter()
    generated = now or datetime.now(UTC)
    requirements = ForecastRequirements(
        tuple(start + timedelta(hours=index) for index in range(hours + 1)),
        frozenset({Variable.ALOFT_WIND, Variable.ALOFT_TEMPERATURE, Variable.SURFACE_TEMPERATURE}),
    )
    client = MsmClient(cache, bounds)
    listings: dict[str, str] = {}
    for url in client.listing_urls(requirements):
        try:
            with urlopen(url + "/", timeout=60) as response:
                listings[url] = response.read().decode("ascii")
        except HTTPError as error:
            if error.code != 404:
                raise
            # An observed absent directory is distinct from a failed listing.
            listings[url] = ""
    runs = client.discover_runs(requirements, listings=listings)
    output.mkdir(parents=True, exist_ok=True)
    assets: dict[str, PreparedAsset] = {}
    retained_listings: dict[str, str] = {}
    cutoff = generated - timedelta(days=7)
    previous_path = output / "catalog.json"
    if previous_path.exists():
        previous = WeatherCatalog.model_validate_json(previous_path.read_text())
        for asset in previous.assets:
            if datetime.strptime(asset.run, "%Y%m%d%H%M%S").replace(tzinfo=UTC) < cutoff:
                continue
            path = output / asset.file
            if not path.is_file():
                continue
            if path.stat().st_size != asset.bytes:
                raise ValueError(f"Retained MSM payload size mismatch: {asset.file}")
            data = MsmPreparedData.from_bytes(path.read_bytes(), expected_sha256=asset.sha256)
            assets[asset.run] = asset
            retained_requirements = ForecastRequirements(
                tuple(sorted({key[0] for key in (*data.surface, *data.pressure)})),
                requirements.variables,
            )
            for url in client.listing_urls(retained_requirements):
                if url in previous.listings:
                    retained_listings[url] = previous.listings[url]
        del previous
    report_runs = []
    for selection in runs[:max_runs]:
        before = perf_counter()
        forecast = client.prepare_run(
            RunId(selection.run_utc),
            requirements,
            available_runs=runs,
        )
        payload = MsmPreparedData.from_forecast(forecast).to_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        # Apply the consumer's budgets before publishing an unusable payload.
        MsmPreparedData.from_bytes(payload, expected_sha256=digest)
        asset = PreparedAsset(
            run=str(RunId(selection.run_utc)),
            file=f"{digest}.npz",
            sha256=digest,
            bytes=len(payload),
        )
        temporary = output / f"{digest}.tmp"
        temporary.write_bytes(payload)
        temporary.replace(output / asset.file)
        assets[asset.run] = asset
        report_runs.append(
            {
                "run": asset.run,
                "bytes": asset.bytes,
                "sources": [remote.url for remote in selection.files],
                "prepare_seconds": perf_counter() - before,
            }
        )
        del forecast, payload
    assets = {
        run: asset
        for run, asset in assets.items()
        if datetime.strptime(run, "%Y%m%d%H%M%S").replace(tzinfo=UTC) >= cutoff
    }
    catalog = WeatherCatalog(
        schema_version=1,
        generated_at=generated,
        expires_at=generated + timedelta(hours=6),
        listings={**retained_listings, **listings},
        assets=list(assets.values()),
    )
    temporary_catalog = output / "catalog.json.tmp"
    temporary_catalog.write_text(catalog.model_dump_json(indent=2) + "\n")
    temporary_catalog.replace(previous_path)
    # Remove only content-addressed feed files no longer referenced by this index.
    retained = {asset.file for asset in assets.values()}
    for path in output.glob("*.npz"):
        if len(path.stem) == 64 and all(c in "0123456789abcdef" for c in path.stem):
            if path.name not in retained and path.stat().st_mtime < cutoff.timestamp():
                path.unlink()
    return {
        "generated_at": generated.isoformat(),
        "valid_start": start.isoformat(),
        "hours": hours,
        "bounds": bounds.__dict__,
        "runs": report_runs,
        "seconds": perf_counter() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--start", type=datetime.fromisoformat)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--max-runs", type=int, default=2)
    parser.add_argument(
        "--bounds", type=float, nargs=4, metavar=("LAT_MIN", "LAT_MAX", "LON_MIN", "LON_MAX")
    )
    args = parser.parse_args()
    start = args.start or datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    if start.tzinfo is None or not 1 <= args.hours <= 48 or not 1 <= args.max_runs <= 32:
        parser.error("start requires a timezone; hours must be 1–48; max-runs must be 1–32")
    bounds = Bounds(*args.bounds) if args.bounds else Bounds()
    try:
        report = produce(args.output, args.cache, start, args.hours, bounds, max_runs=args.max_runs)
    except Exception as error:
        print(
            json.dumps(
                {"status": "FAILED", "error_type": type(error).__name__, "message": str(error)}
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from error
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
