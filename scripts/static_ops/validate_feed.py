"""Decode every MSM payload and require current coverage from newly acquired real Runs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The workflow's tooling checkout owns the validation code, not the app candidate.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jma_gpv_weather import ForecastRequirements, MsmClient, MsmPreparedData, RunId  # noqa: E402
from jma_gpv_weather import WeatherVariable as Variable  # noqa: E402

from autonavlog.weather.local_msm import WeatherCatalog  # noqa: E402


def validate_feed(output: Path, report: dict, now: datetime, *, horizon_hours: int = 24) -> dict:
    catalog = WeatherCatalog.model_validate_json((output / "catalog.json").read_text())
    if (not now - timedelta(minutes=30) <= catalog.generated_at <= now
            or catalog.expires_at - catalog.generated_at != timedelta(hours=6)
            or catalog.expires_at < now + timedelta(hours=2)):
        raise ValueError("MSM catalog freshness/lifetime invalid")
    start = now.replace(minute=0, second=0, microsecond=0)
    requirements = ForecastRequirements(
        tuple(start + timedelta(hours=i) for i in range(horizon_hours + 1)),
        frozenset({Variable.ALOFT_WIND, Variable.ALOFT_TEMPERATURE,
                   Variable.SURFACE_TEMPERATURE}),
    )
    new_runs = {item["run"] for item in report.get("runs", [])}
    if not new_runs or not new_runs <= {a.run for a in catalog.assets}:
        raise ValueError("Newly prepared real MSM Runs required")
    for asset in catalog.assets:
        run = datetime.strptime(asset.run, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        if not now - timedelta(days=7) <= run <= now:
            raise ValueError("MSM Run outside retention or in future")
        path = output / asset.file
        if path.is_symlink():
            raise ValueError("MSM payload symlink forbidden")
        payload = path.read_bytes()
        if len(payload) != asset.bytes:
            raise ValueError("MSM payload byte length mismatch")
        data = MsmPreparedData.from_bytes(payload, expected_sha256=asset.sha256)
        if data.selection.run_utc != run:
            raise ValueError("MSM payload Run differs from catalog")
        if asset.run in new_runs:
            # Validate the real library's surface/pressure variables and time coverage.
            MsmClient().prepare_run(RunId(run), requirements,
                                    available_runs=(data.selection,), prepared_data=data)
    return {"status": "OK", "new_runs": sorted(new_runs), "assets": len(catalog.assets),
            "coverage_start": start.isoformat(), "coverage_hours": horizon_hours,
            "expires_at": catalog.expires_at.isoformat()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate_feed(args.output, json.loads(args.report.read_text()),
                                   datetime.now(UTC)), indent=2))
