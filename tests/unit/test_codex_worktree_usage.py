"""Allocated usage and unknown measurements must not hide retained data."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/codex/worktree_usage.py"
spec = importlib.util.spec_from_file_location("worktree_usage", SCRIPT)
assert spec and spec.loader
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


def test_measure_does_not_follow_symlinks_or_double_count_hardlinks(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    cache = root / "msm-cache"
    cache.mkdir()
    payload = cache / "weather"
    payload.write_bytes(b"x" * 8192)
    (cache / "duplicate").hardlink_to(payload)
    outside = tmp_path / "outside"
    outside.write_bytes(b"x" * 16384)
    (cache / "link").symlink_to(outside)
    (root / "projects").mkdir()
    code = usage.MEASURE.replace("Path('/data')", f"Path({str(root)!r})")
    result = json.loads(subprocess.check_output([sys.executable, "-c", code]))
    expected = sum(p.lstat().st_blocks * 512 for p in [cache, payload, cache / "link"])
    assert result["msm-cache"] == expected
    assert result["projects"] == (root / "projects").stat().st_blocks * 512


def test_unknown_volume_usage_is_not_reported_as_complete():
    summary = usage.summarize([
        {"usage_bytes": {"msm-cache": 100, "projects": 20}}, {"usage_bytes": None},
    ])
    assert summary == {"volumes": 2, "measured_volumes": 1, "known_total_bytes": 120,
                       "known_msm_cache_bytes": 100, "complete": False}


def test_only_exact_worktree_volume_names_match():
    assert usage.VOLUME.fullmatch("autonavlog-wt-0123abcd_autonavlog-data")
    assert not usage.VOLUME.fullmatch("autonavlog_autonavlog-data")
    assert not usage.VOLUME.fullmatch("autonavlog-wt-0123abcd_other-data")
