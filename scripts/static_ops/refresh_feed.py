"""Require newly prepared coverage before a retained catalog can be published."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# The production job is bounded to 45 minutes, so it can cross one hour boundary.
# Keep validation at 24 hours from its own clock; prepare one extra hour instead.
PUBLICATION_GENERATION_HOURS = 25


def refresh(producer: Path, output: Path, cache: Path, expected_run: str | None = None) -> dict:
    previous = output / "catalog.json"
    retained_runs = {asset["run"] for asset in json.loads(previous.read_text())["assets"]} if (
        previous.exists()
    ) else set()
    result = subprocess.run(
        [sys.executable, str(producer), "--output", str(output), "--cache", str(cache),
         "--hours", str(PUBLICATION_GENERATION_HOURS),
         *(["--expected-run", expected_run] if expected_run else [])],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        print(result.stderr, file=sys.stderr, end="")
        result.check_returncode()
    report = json.loads(result.stdout)
    if not report.get("runs"):
        raise ValueError(
            "No newly prepared MSM Run; do not publish a renewed retained-only catalog"
        )
    if expected_run and max(item["run"] for item in report["runs"]) != expected_run:
        raise ValueError("Producer Run differs from the admitted detected Run")
    if retained_runs and not any(item["run"] > max(retained_runs) for item in report["runs"]):
        raise ValueError("No newer real MSM Run; timestamps alone cannot renew weather")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--expected-run")
    args = parser.parse_args()
    print(json.dumps(refresh(args.producer, args.output, args.cache, args.expected_run), indent=2))
