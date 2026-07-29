#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from autonavlog.performance.repository import PerformanceRepository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--require-verified", action="store_true")
    args = parser.parse_args()
    repository = PerformanceRepository.from_directory(args.directory)
    if args.require_verified:
        repository.require_verified()
    print(
        f"{repository.manifest.validation_status}: "
        f"{len(repository.climb_rows)} climb rows, "
        f"{len(repository.cruise_rows)} cruise rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
