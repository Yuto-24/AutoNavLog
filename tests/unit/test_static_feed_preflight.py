"""Scheduled metadata-only decisions fail closed and cannot refresh old forecasts."""

import hashlib
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


def test_new_run_uses_only_catalog_and_listings_without_mutation(source, capsys):
    before = json.dumps(source["catalog"])
    result = gate.preflight("https://canonical.test", NOW)
    assert result["status"] == "NEW_RUN" and result["proceed"] is True
    assert result["upstream_run"] == "20261013030000"
    assert result["generation_hours"] == 25
    assert json.dumps(source["catalog"]) == before
    assert len(source["calls"]) == 3
    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    assert captured.out == ""
    assert diagnostic["stage"] == "preflight"
    assert diagnostic["discovered_run"] == result["upstream_run"]
    assert diagnostic["expected_run"] is None
    assert diagnostic["hours"] == 25
    assert diagnostic["listing_count"] == 2


def short_horizon_published_run(source, now, *, include_long_run=True):
    """A 03 UTC Run covers 24h from 12 UTC, but not the producer's 25h."""
    source["catalog"].update(
        generated_at=(now - timedelta(hours=1)).isoformat(),
        expires_at=(now + timedelta(hours=5)).isoformat(),
    )
    source["catalog"]["assets"][0]["run"] = "20261013030000"
    source["catalog"]["listings"][DIRECTORY] = listing(3)
    long_run = listing(0).replace("16-33", "16-78") + "\n" if include_long_run else ""
    source["listing"] = long_run + listing(3)


@pytest.mark.parametrize("include_long_run", [True, False])
def test_short_run_ages_out_of_25h_but_remains_healthy_for_24h(
        source, capsys, include_long_run):
    now = datetime(2026, 10, 13, 12, 56, tzinfo=UTC)
    short_horizon_published_run(source, now, include_long_run=include_long_run)
    result = gate.preflight("https://canonical.test", now)
    assert result["status"] == "NO_NEW_RUN"
    assert result["proceed"] is False
    assert result["upstream_run"] == "20261013030000"
    assert result["weather_seconds_remaining"] == 5 * 3600
    assert len(source["calls"]) == 3
    diagnostic = json.loads(capsys.readouterr().err)
    assert diagnostic["discovered_run"] == ("20261013000000" if include_long_run else None)
    assert diagnostic["hours"] == 25


@pytest.mark.parametrize("failure", [
    "upstream_missing", "upstream_incomplete", "catalog_incomplete", "newer_incomplete",
])
def test_short_run_noop_rejects_missing_or_incomplete_sources(source, failure):
    now = datetime(2026, 10, 13, 12, 56, tzinfo=UTC)
    short_horizon_published_run(source, now)
    if failure == "upstream_missing":
        source["listing"] = listing(0).replace("16-33", "16-78")
    elif failure == "upstream_incomplete":
        source["listing"] = listing(0).replace("16-33", "16-78") + "\n" + listing(3).splitlines()[0]
    elif failure == "catalog_incomplete":
        source["catalog"]["listings"][DIRECTORY] = listing(3).splitlines()[0]
    else:
        source["listing"] += "\n" + listing(6).splitlines()[0]
    with pytest.raises((ValueError, NoCompatibleRunError)):
        gate.preflight("https://canonical.test", now)


def test_short_run_noop_never_hides_freshness_warning(source):
    now = datetime(2026, 10, 13, 12, 56, tzinfo=UTC)
    short_horizon_published_run(source, now, include_long_run=False)
    source["catalog"].update(
        generated_at=(now - timedelta(hours=5)).isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )
    with pytest.raises(ValueError, match="freshness warning"):
        gate.preflight("https://canonical.test", now)


def test_short_run_still_admits_a_newer_complete_25h_run(source):
    now = datetime(2026, 10, 13, 12, 56, tzinfo=UTC)
    short_horizon_published_run(source, now)
    source["listing"] += "\n" + listing(6)
    result = gate.preflight("https://canonical.test", now)
    assert result["status"] == "NEW_RUN"
    assert result["upstream_run"] == "20261013060000"
    assert result["proceed"] is True


def test_local_catalog_reuses_same_gates_without_fetching_remote_catalog(source):
    raw = json.dumps(source["catalog"]).encode()
    remote = gate.preflight("https://canonical.test", NOW)
    source["calls"].clear()
    assert gate.preflight_catalog(raw, NOW) == remote
    assert "https://canonical.test/weather/msm/catalog.json" not in source["calls"]
    assert len(source["calls"]) == 2
    assert json.dumps(source["catalog"]).encode() == raw


def test_local_catalog_rejects_oversize_before_parsing():
    with pytest.raises(ValueError, match="byte budget"):
        gate.preflight_catalog(b" " * (8 * 1024 * 1024 + 1))


def test_no_new_run_is_positive_healthy_noop(source, capsys):
    source["listing"] = listing(0)
    result = gate.preflight("https://canonical.test", NOW)
    assert result["status"] == "NO_NEW_RUN" and result["proceed"] is False
    assert result["current_catalog_expired"] is False
    assert json.loads(capsys.readouterr().err)["discovered_run"] == "20261013000000"


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


def test_preupload_rechecks_live_run_and_rejects_same_run_or_regression(source, tmp_path):
    """Only a candidate newer than the latest canonical Run may reach upload."""
    candidate = tmp_path / "catalog.json"
    proposed = json.loads(json.dumps(source["catalog"]))
    proposed["assets"][0]["run"] = "20261013030000"
    candidate.write_text(json.dumps(proposed))
    result = gate.require_newer_candidate("https://canonical.test", candidate, NOW)
    assert result["status"] == "NEWER_CANDIDATE"
    source["catalog"]["assets"][0]["run"] = "20261013030000"
    with pytest.raises(ValueError, match="timestamp-only"):
        gate.require_newer_candidate("https://canonical.test", candidate, NOW)
    proposed["assets"][0]["run"] = "20261013000000"
    candidate.write_text(json.dumps(proposed))
    with pytest.raises(ValueError, match="timestamp-only"):
        gate.require_newer_candidate("https://canonical.test", candidate, NOW)


def test_preupload_fetch_failure_cannot_publish(source, tmp_path, monkeypatch):
    """An unavailable canonical catalog prevents the final publication verdict."""
    monkeypatch.setattr(gate, "fetch", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(TimeoutError):
        gate.require_newer_candidate("https://canonical.test", tmp_path / "catalog.json", NOW)


def test_admitted_detected_run_is_pinned_before_payload_and_upload(source, tmp_path):
    """An approval delay cannot silently change the Run recorded for retry accounting."""
    with pytest.raises(ValueError, match="changed since admission"):
        prepare_msm_feed.produce(tmp_path, tmp_path / "cache", NOW, 25,
                                 prepare_msm_feed.Bounds(), expected_run="20261013000000")
    proposed = json.loads(json.dumps(source["catalog"]))
    proposed["assets"][0]["run"] = "20261013030000"
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(proposed))
    with pytest.raises(ValueError, match="admitted detected"):
        gate.require_newer_candidate("https://canonical.test", candidate, NOW,
                                     expected_run="20261013000000")
    assert gate.require_newer_candidate("https://canonical.test", candidate, NOW,
                                       expected_run="20261013030000")["status"] == "NEWER_CANDIDATE"


@pytest.mark.parametrize("change,discovered", [
    ("newer", "20261013060000"),
    ("older", "20261013000000"),
    ("incomplete", None),
    ("hour_boundary", "20261013000000"),
])
def test_discovery_change_is_diagnosable_before_any_payload(
        source, tmp_path, capsys, change, discovered):
    start = NOW.replace(minute=0)
    if change == "hour_boundary":
        start = NOW.replace(hour=8, minute=0)
        source["listing"] = (listing(0).replace("16-33", "16-48") + "\n"
                             + listing(3).replace("16-33", "16-30"))
    admitted = gate.preflight("https://canonical.test", start + timedelta(minutes=56))
    before = json.loads(capsys.readouterr().err)
    expected = admitted["upstream_run"]
    assert expected == "20261013030000"
    if change == "newer":
        source["listing"] += "\n" + listing(6)
    elif change == "older":
        source["listing"] = listing(0) + "\n" + listing(3).splitlines()[0]
    elif change == "incomplete":
        source["listing"] = listing(3).splitlines()[0]
    else:
        start += timedelta(hours=1)
    output = tmp_path / "feed"
    output.mkdir()
    previous = output / "catalog.json"
    previous.write_bytes(b"existing catalog must not be touched")
    source["calls"].clear()
    error_type = NoCompatibleRunError if discovered is None else ValueError
    with pytest.raises(error_type) as error:
        prepare_msm_feed.produce(output, tmp_path / "cache", start, 25,
                                 prepare_msm_feed.Bounds(), expected_run=expected)
    assert previous.read_bytes() == b"existing catalog must not be touched"
    assert list(output.iterdir()) == [previous]
    assert len(source["calls"]) == 2  # One acquisition only; no retry or extra diagnostic fetch.
    after = json.loads(capsys.readouterr().err)
    assert after["stage"] == "generation"
    assert after["expected_run"] == expected
    assert after["discovered_run"] == discovered
    assert after["status"] == ("FAILED" if discovered is None else "DISCOVERED")
    if discovered is not None:
        assert f"expected_run={expected}; discovered_run={discovered}" in str(error.value)
    if change == "hour_boundary":
        assert before["listings"] == after["listings"]
        assert before["valid_start"] != after["valid_start"]
    else:
        assert before["listings"] != after["listings"]
        assert before["valid_start"] == after["valid_start"]


def test_discovery_diagnostics_hash_inputs_without_exposing_them(capsys):
    url = "https://user:password@example.test/listing?token=secret"
    body = "<html>private response containing credentials</html>"
    prepare_msm_feed.log_run_discovery(
        prepare_msm_feed.forecast_requirements(NOW, 25), {url: body},
        stage="generation", expected_run="secret-cli-value", failed=True)
    captured = capsys.readouterr()
    report = json.loads(captured.err)
    assert captured.out == ""
    assert report["expected_run"] is None
    assert report["discovered_run"] is None
    assert report["listing_count"] == 1
    assert report["listing_bytes"] == len(body.encode())
    assert report["listings"] == [{
        "url_sha256": hashlib.sha256(url.encode()).hexdigest(),
        "bytes": len(body.encode()), "sha256": hashlib.sha256(body.encode()).hexdigest(),
    }]
    for secret in (url, body, "password", "token", "secret-cli-value"):
        assert secret not in captured.err
    assert datetime.fromisoformat(report["checked_at"]).tzinfo is not None


def test_preflight_cli_keeps_stdout_json_and_github_outputs(source, monkeypatch, tmp_path, capsys):
    original = gate.preflight
    monkeypatch.setattr(gate, "preflight", lambda origin: original(origin, NOW))
    output = tmp_path / "github-output"
    monkeypatch.setattr(sys, "argv", ["preflight", "--origin", "https://canonical.test",
                                     "--github-output", str(output)])
    gate.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "NEW_RUN"
    assert json.loads(captured.err)["stage"] == "preflight"
    assert output.read_text() == "upstream_run=20261013030000\nproceed=true\n"


@pytest.mark.parametrize("returncode", [0, 1])
def test_refresh_forwards_diagnostics_once_and_preserves_report(
        monkeypatch, tmp_path, capsys, returncode):
    diagnostic = '{"event":"run_discovery","stage":"generation"}\n'
    report = {"runs": [{"run": "20261013030000"}]}
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        [], returncode, stdout=json.dumps(report) if not returncode else "", stderr=diagnostic))
    if returncode:
        with pytest.raises(subprocess.CalledProcessError):
            refresh(tmp_path / "producer", tmp_path, tmp_path / "cache")
    else:
        assert refresh(tmp_path / "producer", tmp_path, tmp_path / "cache") == report
    captured = capsys.readouterr()
    assert captured.err == diagnostic
    assert captured.out == ""
