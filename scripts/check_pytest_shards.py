"""Fail the aggregate gate if a shard omits, duplicates, or collects different tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def check(root: Path, count: int) -> None:
    manifests = [json.loads(path.read_text()) for path in sorted(root.glob("*/collection.json"))]
    if len(manifests) != count:
        raise ValueError("missing or extra shard manifests")
    if sorted(m["shard"] for m in manifests) != list(range(1, count + 1)):
        raise ValueError("duplicate or missing shard indices")
    collected = sorted(manifests[0]["collected"])
    if not collected:
        raise ValueError("empty collection")
    if any(m["count"] != count or sorted(m["collected"]) != collected for m in manifests):
        raise ValueError("shards collected different tests")
    if sorted(test for m in manifests for test in m["selected"]) != collected:
        raise ValueError("shard selection omitted or duplicated tests")
    if len(list(root.glob("*/.coverage"))) != count:
        raise ValueError("missing or extra coverage files")
    print(f"Verified {len(collected)} collected tests across {count} shards")


if __name__ == "__main__":
    check(Path(sys.argv[1]), int(sys.argv[2]))
