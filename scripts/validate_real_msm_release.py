#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autonavlog.weather.msm_adapter import MsmWeatherProvider
from autonavlog.weather.real_msm_acceptance import (
    RealMsmAcceptanceConfig,
    run_real_msm_acceptance,
)


def _parse_utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "valid time must be ISO 8601, for example 2026-07-30T03:00:00Z"
        ) from error
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("valid time must include a UTC offset")
    return parsed.astimezone(UTC)


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the pinned real-MSM package and weather queries. "
            "Network access occurs only with --live."
        )
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/msm-cache"),
        help="jma-gpv-weather raw/normalized cache directory",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="resolve, prepare, download/cache, and query a real MSM run",
    )
    parser.add_argument(
        "--valid-time",
        type=_parse_utc_datetime,
        help="forecast valid time; required with --live",
    )
    parser.add_argument(
        "--altitude-ft",
        type=float,
        default=5_000.0,
        help="MSL altitude for RJFM/RJFO aloft acceptance probes",
    )
    parser.add_argument("--output", type=Path, help="optional JSON report destination")
    args = parser.parse_args()
    if args.live and args.valid_time is None:
        parser.error("--valid-time is required with --live")
    if not args.live and args.valid_time is not None:
        parser.error("--valid-time is meaningful only with --live")

    try:
        provider = MsmWeatherProvider(cache_dir=args.cache_dir)
        report = run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(
                valid_time_utc=args.valid_time,
                altitude_ft_msl=args.altitude_ft,
                live=args.live,
            ),
        )
    except BaseException as error:
        report = {
            "schema_version": 1,
            "status": "FAIL",
            "mode": "LIVE" if args.live else "OFFLINE_PREFLIGHT",
            "live_executed": args.live,
            "failure": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }
        _write_report(report, args.output)
        return 1

    _write_report(report, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
