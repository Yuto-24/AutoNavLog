"""Exercise the real producer/portable decoder across a publication hour boundary."""

import io
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jma_gpv_weather import Bounds, MsmPreparedData
from jma_gpv_weather.errors import MissingVariableError

from scripts import prepare_msm_feed
from scripts.static_ops import refresh_feed
from scripts.static_ops.validate_feed import validate_feed


def test_publication_refresh_covers_next_hour_with_pressure_interpolation(monkeypatch, tmp_path):
    # Reproduce the 12:56 generation -> 13:01 post-E2E check, without network/GRIB.
    generated = datetime(2026, 10, 12, 12, 56, tzinfo=UTC)
    start = generated.replace(minute=0)
    checked = generated + timedelta(minutes=5)
    fixture = Path(__file__).parents[1] / "fixtures/msm-portable"
    asset = json.loads((fixture / "catalog.json").read_text())["assets"][0]
    template = MsmPreparedData.from_bytes((fixture / asset["file"]).read_bytes())
    template_time = min(key[0] for key in template.surface)
    names = "\n".join(
        f"Z__C_RJTD_20261012{hour:02d}0000_MSM_GPV_Rjp_{kind}_FH{span}_grib2.bin"
        for hour in (6, 9) for kind in ("Lsurf", "L-pall")
        for span in ("00-15", "16-33", "34-39")
    )
    monkeypatch.setattr(prepare_msm_feed, "urlopen", lambda url, **kw: io.BytesIO(
        names.encode() if "/2026/10/12/" in url else b""))

    # Stub only acquisition/GRIB decode. Real discovery, interpolation requirements,
    # preparation, serialization, integrity and publication validation still run.
    monkeypatch.setattr("jma_gpv_weather.cache.acquire_files", lambda files, *a: (
        [], {remote.url: "a" * 64 for remote in files}))

    def records(cache, run, bounds, valid_times, paths, hashes, **kwargs):
        return tuple({
            (valid, level, name): arrays
            for valid in valid_times if not pressure or valid.hour % 3 == 0
            for (old_time, level, name), arrays in source.items() if old_time == template_time
        } for pressure, source in ((False, template.surface), (True, template.pressure)))

    monkeypatch.setattr("jma_gpv_weather.normalized.prepare_records", records)
    old_output = tmp_path / "old"
    old_report = prepare_msm_feed.produce(
        old_output, tmp_path / "cache", start, 24, Bounds(), now=generated)
    assert validate_feed(old_output, old_report, generated)["status"] == "OK"
    with pytest.raises(MissingVariableError, match="2026-10-13T13:00:00.*tmp_surface"):
        validate_feed(old_output, old_report, checked)

    output = tmp_path / "fresh"

    def run_producer(command, **kwargs):
        # Exercise refresh's actual CLI horizon; omitting it reproduces the old 24h default.
        hours = int(command[command.index("--hours") + 1]) if "--hours" in command else 24
        report = prepare_msm_feed.produce(
            output, tmp_path / "cache", start, hours, Bounds(), now=generated)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(report))

    monkeypatch.setattr(refresh_feed.subprocess, "run", run_producer)
    report = refresh_feed.refresh(Path(prepare_msm_feed.__file__), output, tmp_path / "cache")
    assert report["hours"] == 25
    result = validate_feed(output, report, checked)
    assert result["coverage_start"] == "2026-10-12T13:00:00+00:00"
    assert result["coverage_hours"] == 24
    assert result["new_runs"] == ["20261012060000", "20261012090000"]
    catalog = json.loads((output / "catalog.json").read_text())
    for asset in catalog["assets"]:
        data = MsmPreparedData.from_bytes((output / asset["file"]).read_bytes())
        assert (datetime(2026, 10, 13, 13, tzinfo=UTC), 2, "tmp_surface") in data.surface
        upper = datetime(2026, 10, 13, 15, tzinfo=UTC)
        assert (upper, 1000, "tmp") in data.pressure
        assert max(key[0] for key in data.pressure) == upper
    # The generation margin must not relax freshness or move the validator's clock back.
    with pytest.raises(ValueError, match="freshness"):
        validate_feed(output, report, generated + timedelta(minutes=31))
