"""Scheduled metadata-only decisions fail closed and cannot refresh old forecasts."""

import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError

import pytest
from jma_gpv_weather import MsmClient
from jma_gpv_weather.errors import NoCompatibleRunError

from scripts import prepare_msm_feed
from scripts.static_ops import preflight_feed as gate
from scripts.static_ops.refresh_feed import refresh

NOW = datetime(2026, 10, 13, 6, 56, tzinfo=UTC)
DIRECTORY = "http://example.test/2026/10/13"


def listing(hour):
    return "\n".join(f"Z__C_RJTD_20261013{hour:02d}0000_MSM_GPV_Rjp_{kind}_"
                     f"FH{span}_grib2.bin" for kind in ("Lsurf", "L-pall")
                     for span in ("00-15", "16-33"))


@pytest.fixture
def source(monkeypatch):
    catalog = {"schema_version": 1, "generated_at": (NOW - timedelta(hours=1)).isoformat(),
               "expires_at": (NOW + timedelta(hours=5)).isoformat(),
               "listings": {DIRECTORY: listing(0), DIRECTORY + "/absent": ""}, "assets": [
                   {"run": "20261013000000", "file": "a" * 64 + ".npz",
                    "sha256": "a" * 64, "bytes": 123}]}
    state = {"catalog": catalog, "listing": listing(0) + "\n" + listing(3), "calls": []}

    def fetch(url, limit):
        state["calls"].append(url)
        assert url == "https://canonical.test/weather/msm/catalog.json"
        return json.dumps(state["catalog"]).encode()

    def acquire(url, **kwargs):
        state["calls"].append(url)
        if url != DIRECTORY + "/":
            raise HTTPError(url, 404, "Absent directory", {}, None)
        if isinstance(state["listing"], Exception):
            raise state["listing"]
        return io.BytesIO(state["listing"].encode())

    monkeypatch.setattr(gate, "fetch", fetch)
    monkeypatch.setattr(prepare_msm_feed, "urlopen", acquire)
    monkeypatch.setattr(MsmClient, "listing_urls", lambda *args: (DIRECTORY, DIRECTORY + "/absent"))
    monkeypatch.setattr(MsmClient, "prepare_run",
                        lambda *a, **k: pytest.fail("Payload acquisition"))
    return state


def test_new_run_uses_only_catalog_and_listings_without_mutation(source):
    before = json.dumps(source["catalog"])
    result = gate.preflight("https://canonical.test", NOW)
    assert result["status"] == "NEW_RUN" and result["proceed"] is True
    assert result["upstream_run"] == "20261013030000"
    assert result["generation_hours"] == 25
    assert json.dumps(source["catalog"]) == before
    assert len(source["calls"]) == 3


def test_no_new_run_is_positive_healthy_noop(source):
    source["listing"] = listing(0)
    result = gate.preflight("https://canonical.test", NOW)
    assert result["status"] == "NO_NEW_RUN" and result["proceed"] is False
    assert result["current_catalog_expired"] is False


@pytest.mark.parametrize("failure", [OSError("listing unavailable"),
    HTTPError(DIRECTORY, 503, "upstream", {}, None), "", "<html>service unavailable</html>",
    listing(0) + "\n" + listing(3).splitlines()[0]])
def test_unknown_failed_empty_or_incomplete_listing_never_skips(source, failure):
    source["listing"] = failure
    with pytest.raises((OSError, ValueError)):
        gate.preflight("https://canonical.test", NOW)


@pytest.mark.parametrize("change", [
    {"assets": []}, {"listings": {}}, {"schema_version": 2},
    {"generated_at": (NOW + timedelta(minutes=1)).isoformat()},
    {"expires_at": (NOW + timedelta(days=1)).isoformat()},
    {"listings": {DIRECTORY: "broken"}},
])
def test_corrupt_catalog_blocks_before_listing(source, change):
    source["catalog"].update(change)
    with pytest.raises(ValueError):
        gate.preflight("https://canonical.test", NOW)
    assert len(source["calls"]) == 1


@pytest.mark.parametrize("remaining", [-1, 0, 7199])
def test_no_new_run_never_hides_expiry_or_monitor_warning(source, remaining):
    expiry = NOW + timedelta(seconds=remaining)
    source["catalog"].update(generated_at=(expiry - timedelta(hours=6)).isoformat(),
                             expires_at=expiry.isoformat())
    source["listing"] = listing(0)
    with pytest.raises(ValueError, match="freshness warning"):
        gate.preflight("https://canonical.test", NOW)


def test_expired_catalog_can_recover_only_through_new_run_and_existing_gates(source):
    source["catalog"].update(generated_at="2026-10-13T00:00:00Z",
                             expires_at="2026-10-13T06:00:00Z")
    result = gate.preflight("https://canonical.test", NOW)
    assert result["proceed"] and result["current_catalog_expired"]


def test_upstream_cannot_silently_regress_behind_published_run(source):
    source["catalog"]["assets"][0]["run"] = "20261013030000"
    source["catalog"]["listings"][DIRECTORY] = listing(3)
    source["listing"] = listing(0)
    with pytest.raises(ValueError, match="missing from complete"):
        gate.preflight("https://canonical.test", NOW)


def test_queued_schedule_rereads_canonical_and_race_after_preflight_fails_closed(
        source, monkeypatch, tmp_path):
    assert gate.preflight("https://canonical.test", NOW)["proceed"]
    source["catalog"]["assets"][0]["run"] = "20261013030000"
    source["catalog"]["listings"][DIRECTORY] = listing(3)
    assert not gate.preflight("https://canonical.test", NOW)["proceed"]
    (tmp_path / "catalog.json").write_text(json.dumps(source["catalog"]))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        [], 0, stdout=json.dumps({"runs": [{"run": "20261013030000"}]})))
    with pytest.raises(ValueError, match="No newer real"):
        refresh(tmp_path / "producer", tmp_path, tmp_path / "cache")


def test_hour_rollover_keeps_25_hour_discovery(source):
    assert gate.preflight("https://canonical.test", NOW)["proceed"]
    assert gate.preflight("https://canonical.test", NOW + timedelta(minutes=5))["proceed"]
    # 07:00 + 25h needs next-day 09:00 pressure, outside this truncated new Run.
    source["listing"] = listing(0) + "\n" + listing(3).replace("16-33", "16-27")
    with pytest.raises(ValueError, match="incomplete"):
        gate.preflight("https://canonical.test", NOW + timedelta(minutes=5))


def test_failure_writes_no_success_output(source, monkeypatch, tmp_path):
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", ["preflight", "--origin", "https://canonical.test",
                                     "--github-output", str(output)])
    source["catalog"]["assets"] = []
    with pytest.raises(ValueError):
        gate.main()
    assert not output.exists()


def test_noop_rejects_incomplete_published_listings_even_when_upstream_is_complete(source):
    source["listing"] = listing(0)
    source["catalog"]["listings"][DIRECTORY] = listing(0).splitlines()[0]
    with pytest.raises(NoCompatibleRunError):
        gate.preflight("https://canonical.test", NOW)
