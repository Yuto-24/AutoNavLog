"""Production feed coverage uses the real library; only GRIB acquisition is stubbed."""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jma_gpv_weather import Bounds, MsmPreparedData, RunId

from autonavlog.weather.local_msm import LocalMsmClient, LocalWeatherError, WeatherCatalog
from scripts import prepare_msm_feed
from scripts.static_ops.validate_feed import validate_feed

START = datetime(2026, 10, 31, 14, tzinfo=UTC)
RUN = datetime(2026, 10, 31, 0, tzinfo=UTC)


@pytest.fixture
def full_source(monkeypatch):
    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = START.astimezone(tz)
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    # Discovery must use the same fixed date as feed generation, including future requests.
    monkeypatch.setattr("jma_gpv_weather.msm.client.datetime", FixtureClock)
    fixture = Path(__file__).parents[1] / "fixtures/msm-portable"
    asset = json.loads((fixture / "catalog.json").read_text())["assets"][0]
    template = MsmPreparedData.from_bytes((fixture / asset["file"]).read_bytes())
    first = min(key[0] for key in template.surface)
    names = []
    for hour in (0, 6, 9):
        for kind, spans in (
            ("Lsurf", ("00-15", "16-33", "34-39", "40-51", "52-78")),
            ("L-pall", ("00-15", "18-33", "36-39", "42-51", "54-78")),
        ):
            for span in spans if hour == 0 else spans[:3]:
                names.append(f"Z__C_RJTD_20261031{hour:02d}0000_MSM_GPV_Rjp_{kind}"
                             f"_FH{span}_grib2.bin")
    listing = "\n".join(names)
    monkeypatch.setattr(prepare_msm_feed, "urlopen", lambda url, **kw: io.BytesIO(
        listing.encode() if "/2026/10/31/" in url else b""))
    monkeypatch.setattr("jma_gpv_weather.cache.acquire_files", lambda files, *a: (
        [], {remote.url: "a" * 64 for remote in files}))

    def records(cache, run, bounds, valid_times, paths, hashes, **kwargs):
        return tuple({
            (valid, level, name): tuple(array[:2, :2] for array in arrays)
            for valid in valid_times if not pressure or valid.hour % 3 == 0
            for (old, level, name), arrays in source.items() if old == first
        } for pressure, source in ((False, template.surface), (True, template.pressure)))

    monkeypatch.setattr("jma_gpv_weather.normalized.prepare_records", records)


def consumer(output):
    client = LocalMsmClient()
    client.catalog = WeatherCatalog.model_validate_json((output / "catalog.json").read_text())
    return client


def prepare_at(client, output, valid, selected=None):
    requirements = prepare_msm_feed.forecast_requirements(valid, 0)
    status = client.resolve_run(requirements, selected_run=selected)
    asset = next(a for a in client.catalog.assets if a.run == str(status.selected_run))
    client.data = MsmPreparedData.from_bytes((output / asset.file).read_bytes(),
                                            expected_sha256=asset.sha256)
    return status, client.prepare_run(status.selected_run, requirements)


def test_full_run_keeps_latest_short_and_long_horizon_and_segment_boundaries(full_source, tmp_path):
    report = prepare_msm_feed.produce(tmp_path, tmp_path / "cache", START, 25,
                                      Bounds(), now=START, full_run=True)
    assert [r["run"] for r in report["runs"]] == [
        "20261031090000", "20261031060000", "20261031000000"]
    assert validate_feed(tmp_path, report, START)["status"] == "OK"
    client = consumer(tmp_path)
    status, _ = prepare_at(client, tmp_path, START)
    assert str(status.selected_run) == "20261031090000"
    # Beyond both short Runs, the long Run remains discoverable and prepared.
    status, _ = prepare_at(client, tmp_path, RUN + timedelta(hours=60))
    assert str(status.selected_run) == "20261031000000"
    for hour in (15, 15.5, 16, 33, 33.5, 34, 39, 39.5, 40, 48, 51, 51.5, 52, 72, 77.5, 78):
        _, prepared = prepare_at(client, tmp_path, RUN + timedelta(hours=hour), RunId(RUN))
        assert prepared.selection.run_utc == RUN
    with pytest.raises(LocalWeatherError) as error:
        prepare_at(client, tmp_path, RUN + timedelta(hours=78, minutes=1), RunId(RUN))
    assert error.value.code == "WEATHER_OUT_OF_COVERAGE"
    assert str(error.value) == "選択した日時のMSM予報は利用できません。"


def test_partial_feed_reports_unprepared_coverage_not_corruption(full_source, tmp_path):
    prepare_msm_feed.produce(tmp_path, tmp_path / "cache", START, 25,
                            Bounds(), now=START, max_runs=3)
    client = consumer(tmp_path)
    # L-pall FH42-51 exists in the listing, but neither records nor source exist in NPZ.
    with pytest.raises(LocalWeatherError) as error:
        prepare_at(client, tmp_path, RUN + timedelta(hours=48), RunId(RUN))
    assert error.value.code == "WEATHER_PREPARED_UNAVAILABLE"


def test_source_identity_mismatch_still_fails_integrity(full_source, tmp_path):
    prepare_msm_feed.produce(tmp_path, tmp_path / "cache", START, 25,
                            Bounds(), now=START, full_run=True)
    client = consumer(tmp_path)
    client.catalog.listings = {
        url: listing.replace("FH00-15", "FH00-18")
        for url, listing in client.catalog.listings.items()
    }
    with pytest.raises(LocalWeatherError) as error:
        prepare_at(client, tmp_path, START)
    assert error.value.code == "WEATHER_PAYLOAD_INTEGRITY_FAILED"


def test_full_generation_failure_keeps_published_catalog(full_source, monkeypatch, tmp_path):
    prepare_msm_feed.produce(tmp_path, tmp_path / "cache", START, 25, Bounds(), now=START)
    before = (tmp_path / "catalog.json").read_bytes()
    original = prepare_msm_feed.urlopen

    def incomplete(url, **kwargs):
        return io.BytesIO(original(url, **kwargs).read().replace(b"FH54-78", b"FH54-75"))

    monkeypatch.setattr(prepare_msm_feed, "urlopen", incomplete)
    from jma_gpv_weather.errors import NoCompatibleRunError

    with pytest.raises(NoCompatibleRunError):
        prepare_msm_feed.produce(tmp_path, tmp_path / "cache", START, 25,
                                Bounds(), now=START, full_run=True)
    assert (tmp_path / "catalog.json").read_bytes() == before


def test_validator_direct_entrypoint_imports_producer_contract():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/static_ops/validate_feed.py", "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
