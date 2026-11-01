"""Convert pytest JUnit testcase times to source-file weights, including unittest classes."""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def weights(report: Path, root: Path) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for case in ET.parse(report).iter("testcase"):
        parts = case.attrib["classname"].split(".")
        while parts:
            source = Path(*parts).with_suffix(".py")
            if (root / source).is_file():
                totals[source.as_posix()] += float(case.attrib["time"])
                break
            parts.pop()
        else:
            raise ValueError(f"No source file for {case.attrib['classname']}")
    return {name: round(seconds, 3) for name, seconds in sorted(totals.items())}


if __name__ == "__main__":
    print(json.dumps(weights(Path(sys.argv[1]), Path(__file__).resolve().parents[1]), indent=2))
