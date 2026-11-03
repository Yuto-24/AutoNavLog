"""Explicit loopback-only test server: real API/calculator, historical portable MSM."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn

from autonavlog.forecast_backup import BackupRequest
from autonavlog.local import LocalApplication
from autonavlog.weather.local_msm import LocalMsmWeather
from autonavlog.web.app import create_app
from autonavlog.web.forecast_backup import calculator
from autonavlog.web.runtime import WebRuntimeConfig

ROOT = Path(__file__).resolve().parents[2]
FEED = ROOT / "tests/fixtures/msm-portable"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--storage", type=Path, required=True)
    args = parser.parse_args()
    os.environ["AUTONAVLOG_FORECAST_BACKUP_ORIGIN"] = args.origin
    app = create_app(WebRuntimeConfig(ROOT / "data", args.storage))

    def compute(request: BackupRequest):
        def weather():
            portable = LocalMsmWeather()
            catalog = json.loads((FEED / "catalog.json").read_text())
            catalog.update(
                generated_at=datetime.now(UTC).isoformat(),
                expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            )
            local = LocalApplication(ROOT / "data")
            try:
                requirement = (
                    local.session.calculation_service.forecast_service.build_initial_requirement(
                        request.project
                    )
                )
                asset = json.loads(
                    portable.plan(
                        request.project,
                        requirement,
                        json.dumps(catalog),
                        refresh_forecast=request.refresh_forecast,
                    )
                )
                portable.accept((FEED / asset["file"]).read_bytes(), asset["sha256"])
                return portable.provider
            finally:
                local.close()

        return calculator(ROOT / "data", weather, None)(request)

    app.state.forecast_backup_jobs.compute = compute
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
