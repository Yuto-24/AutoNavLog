#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from autonavlog.release_validation import validate_runtime_data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate source-backed airport and SR22 G6 performance release data."
    )
    parser.add_argument("data_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate_runtime_data(args.data_root)
    except BaseException as error:
        report = {
            "schema_version": 1,
            "status": "FAIL",
            "failure": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }
        exit_code = 1
    else:
        exit_code = 0
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
