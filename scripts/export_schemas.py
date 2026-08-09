#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.project import Project
from autonavlog.domain.snapshot import CalculationSnapshot
from autonavlog.domain.weather import WeatherRequest, WeatherResult

MODELS = {
    "project.schema.json": Project,
    "calculation-outcome.schema.json": CalculationOutcome,
    "snapshot.schema.json": CalculationSnapshot,
    "weather-request.schema.json": WeatherRequest,
    "weather-result.schema.json": WeatherResult,
}


def serialized_schema(model: type) -> str:
    return (
        json.dumps(
            model.model_json_schema(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=Path("data/schemas"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    stale = []
    for filename, model in MODELS.items():
        path = args.directory / filename
        expected = serialized_schema(model)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                stale.append(filename)
        else:
            path.write_text(expected, encoding="utf-8")
    if stale:
        raise SystemExit(f"generated schemas are stale: {stale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
