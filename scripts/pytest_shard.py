"""Partition all collected tests by measured file time without splitting file fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
from statistics import median

import pytest

TIMINGS = Path(__file__).with_name("pytest_file_durations.json")


def partition(files: list[str], durations: dict[str, float], count: int) -> list[list[str]]:
    """Greedy longest-first bins; new files still run, with a median default weight."""
    if count < 1:
        raise ValueError("shard count must be positive")
    bins: list[list[str]] = [[] for _ in range(count)]
    totals = [0.0] * count
    default = median(durations.values()) if durations else 1.0
    for file in sorted(set(files), key=lambda f: (-durations.get(f, default), f)):
        index = min(range(count), key=lambda i: (totals[i], i))
        bins[index].append(file)
        totals[index] += durations.get(file, default)
    return bins


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    raw = os.environ.get("CI_PYTEST_SHARD")
    if raw is None:
        return
    try:
        index, count = (int(value) for value in raw.split("/"))
        if not 1 <= index <= count:
            raise ValueError
    except ValueError as exc:
        message = "CI_PYTEST_SHARD must be index/count (1 <= index <= count)"
        raise pytest.UsageError(message) from exc
    durations = json.loads(TIMINGS.read_text())
    files = [item.nodeid.split("::", 1)[0] for item in items]
    selected = set(partition(files, durations, count)[index - 1])
    kept, removed = [], []
    for item, file in zip(items, files, strict=True):
        (kept if file in selected else removed).append(item)
    manifest = os.environ.get("CI_PYTEST_MANIFEST")
    if manifest:
        Path(manifest).write_text(json.dumps({
            "shard": index, "count": count,
            "collected": [item.nodeid for item in items],
            "selected": [item.nodeid for item in kept],
        }))
    config.hook.pytest_deselected(items=removed)
    items[:] = kept
