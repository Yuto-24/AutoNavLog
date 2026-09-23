from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jma_gpv_weather import Bounds, MsmClient, MsmPreparedData
from jma_gpv_weather.errors import CacheIntegrityError

from scripts import prepare_msm_feed

FEED = Path(__file__).resolve().parents[1] / "fixtures/msm-portable"
START = datetime(2026, 9, 16, 3, tzinfo=UTC)


@pytest.fixture
def producer(monkeypatch):
    catalog = json.loads((FEED / "catalog.json").read_text())
    payloads = {
        a["run"]: MsmPreparedData.from_bytes((FEED / a["file"]).read_bytes())
        for a in catalog["assets"]
    }
    monkeypatch.setattr(
        prepare_msm_feed,
        "urlopen",
        lambda url, **kw: io.BytesIO(catalog["listings"][url.rstrip("/")].encode()),
    )
    original = MsmClient.prepare_run

    def prepared(self, run, requirements, **kwargs):
        kwargs.setdefault("prepared_data", payloads[str(run)])
        return original(self, run, requirements, **kwargs)

    monkeypatch.setattr(MsmClient, "prepare_run", prepared)


def test_producer_publishes_library_payload_and_manifest_last(producer, tmp_path):
    output = tmp_path / "feed"
    report = prepare_msm_feed.produce(output, tmp_path / "cache", START, 3, Bounds())
    assert len(report["runs"]) == 2
    catalog = json.loads((output / "catalog.json").read_text())
    for asset in catalog["assets"]:
        data = MsmPreparedData.from_bytes(
            (output / asset["file"]).read_bytes(),
            expected_sha256=asset["sha256"],
        )
        assert str(data.selection.run_utc.year) == "2026"
    previous = (output / "catalog.json").read_bytes()
    # A failed generation must not replace the existing catalog with an empty success.
    with pytest.raises(CacheIntegrityError):
        prepare_msm_feed.produce(output, tmp_path / "cache", START, 12, Bounds())
    assert (output / "catalog.json").read_bytes() == previous


def test_listing_failure_does_not_publish_empty_coverage(producer, monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise OSError("source communication failure")

    monkeypatch.setattr(prepare_msm_feed, "urlopen", fail)
    with pytest.raises(OSError, match="communication"):
        prepare_msm_feed.produce(tmp_path / "feed", tmp_path / "cache", START, 3, Bounds())
    assert not (tmp_path / "feed/catalog.json").exists()


def test_rollover_retains_source_listing_and_rejects_corrupt_asset(producer, monkeypatch, tmp_path):
    from autonavlog.domain.project import Project
    from autonavlog.domain.weather import ForecastRequirement
    from autonavlog.weather.local_msm import LocalMsmWeather
    from scripts.local_reference import reference_state

    output = tmp_path / "feed"
    prepare_msm_feed.produce(output, tmp_path / "cache", START, 3, Bounds(), now=START)
    with monkeypatch.context() as rollover:
        # Isolate the publisher's retention contract from upstream Run discovery:
        # the next acquisition window no longer includes the old source directory.
        original_listing_urls = MsmClient.listing_urls
        calls = 0

        def listing_urls(self, requirements):
            nonlocal calls
            calls += 1
            return (["https://example.test/current"] if calls == 1
                    else original_listing_urls(self, requirements))

        rollover.setattr(MsmClient, "listing_urls", listing_urls)
        rollover.setattr(prepare_msm_feed, "urlopen", lambda *args, **kw: io.BytesIO(b""))
        rollover.setattr(MsmClient, "discover_runs", lambda *args, **kw: [])
        prepare_msm_feed.produce(output, tmp_path / "cache", START, 3, Bounds(), now=START)
    weather = LocalMsmWeather()
    project = Project.model_validate(reference_state(feed=str(FEED), pinned=True)["project"])
    # The captured Runs must stay within the seven-day retention window regardless
    # of the wall clock. Freeze the consumer clock after constructing its Project.
    from autonavlog.weather import local_msm

    class DeviceClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return START.astimezone(tz)

    monkeypatch.setattr(local_msm, "datetime", DeviceClock)
    requirement = ForecastRequirement(valid_times_utc=(START,), require_surface_temperature=True)
    asset = json.loads(weather.plan(project, requirement, (output / "catalog.json").read_text()))
    assert asset["run"] == "20260915180000"
    weather.accept((output / asset["file"]).read_bytes(), asset["sha256"])
    before = (output / "catalog.json").read_bytes()
    catalog = json.loads(before)
    catalog["assets"][0]["bytes"] += 1
    (output / "catalog.json").write_text(json.dumps(catalog))
    damaged_catalog = (output / "catalog.json").read_bytes()
    with pytest.raises(ValueError, match="size mismatch"):
        prepare_msm_feed.produce(output, tmp_path / "cache", START, 3, Bounds(), now=START)
    assert (output / "catalog.json").read_bytes() == damaged_catalog
    (output / "catalog.json").write_bytes(before)
    (output / asset["file"]).write_bytes(b"x" * asset["bytes"])
    with pytest.raises(CacheIntegrityError):
        prepare_msm_feed.produce(output, tmp_path / "cache", START, 3, Bounds(), now=START)
    assert (output / "catalog.json").read_bytes() == before
