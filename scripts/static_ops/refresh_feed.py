"""Require newly prepared coverage before a retained catalog can be published."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def refresh(producer: Path, output: Path, cache: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(producer), "--output", str(output), "--cache", str(cache)],
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
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(refresh(args.producer, args.output, args.cache), indent=2))
