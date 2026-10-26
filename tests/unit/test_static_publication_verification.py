"""Canonical publication waits for exact identity and never advances after failure."""

import copy
import hashlib
import importlib.util
import json
import os
import runpy
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "scripts/static_ops"
spec = importlib.util.spec_from_file_location(
    "wait_for_candidate", SCRIPTS / "wait_for_candidate.py"
)
poll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(poll)
ops_spec = importlib.util.spec_from_file_location("operations", SCRIPTS / "operations.py")
ops = importlib.util.module_from_spec(ops_spec)
ops_spec.loader.exec_module(ops)


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=0, sleeps=[], calls=[])

    def sleep(seconds):
        state.sleeps.append(seconds)
        state.now += seconds

    monkeypatch.setattr(poll, "time", SimpleNamespace(monotonic=lambda: state.now, sleep=sleep))
    return state


def checker(monkeypatch, clock, results):
    def run(command, **kwargs):
        clock.calls.append((command, kwargs))
        duration, result = next(results)
        clock.now += duration
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(command, result, '{"status":"OK"}\n', "mismatch")

    monkeypatch.setattr(poll.subprocess, "run", run)


@pytest.mark.parametrize("failures", [[], [1], [1, 1]])
def test_poll_checks_immediately_and_retries_until_exact_checker_success(
    monkeypatch, clock, failures
):
    checker(monkeypatch, clock, iter([(0, code) for code in [*failures, 0]]))
    poll.wait_for_candidate(Path("release.json"))
    assert clock.sleeps == [10] * len(failures)
    assert len(clock.calls) == len(failures) + 1
    for command, kwargs in clock.calls:
        assert command == [
            sys.executable,
            str(SCRIPTS / "check_candidate.py"),
            "release.json",
            "--remote",
        ]
        assert kwargs["timeout"] == 30


def test_mismatch_exhausts_budget_without_extra_attempt(monkeypatch, clock, capsys):
    checker(monkeypatch, clock, iter([(0, 1)] * 3))
    with pytest.raises(TimeoutError, match="25s"):
        poll.wait_for_candidate(Path("release.json"), timeout=25)
    assert clock.sleeps == [10, 10, 5]
    assert [kw["timeout"] for _, kw in clock.calls] == [25, 15, 5]
    assert capsys.readouterr().out == ""


def test_attempt_timeout_retries_with_remaining_budget(monkeypatch, clock):
    checker(monkeypatch, clock, iter([(30, subprocess.TimeoutExpired("check", 30)), (0, 0)]))
    poll.wait_for_candidate(Path("release.json"), timeout=45)
    assert clock.sleeps == [10]
    assert [kw["timeout"] for _, kw in clock.calls] == [30, 5]


def test_match_at_deadline_is_not_success(monkeypatch, clock, capsys):
    checker(monkeypatch, clock, iter([(5, 0)]))
    with pytest.raises(TimeoutError):
        poll.wait_for_candidate(Path("release.json"), timeout=5)
    assert clock.sleeps == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
@pytest.mark.parametrize("argument", ["timeout", "interval", "attempt_timeout"])
def test_invalid_budgets_fail_before_request(value, argument):
    with pytest.raises(ValueError, match="finite positive"):
        poll.wait_for_candidate(Path("unused"), **{argument: value})


def test_hung_checker_is_killed_within_overall_budget(monkeypatch, tmp_path):
    (tmp_path / "check_candidate.py").write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(poll, "__file__", str(tmp_path / "wait_for_candidate.py"))
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        poll.wait_for_candidate(Path("unused"), timeout=0.2, interval=0.01, attempt_timeout=0.1)
    assert time.monotonic() - start < 5


@pytest.fixture
def remote_checker(monkeypatch, tmp_path):
    now = datetime.now(UTC)
    catalog = {
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=6)).isoformat(),
        "assets": [{}],
    }
    catalog_raw = json.dumps(catalog).encode()
    candidate = {
        "commit": "a" * 40,
        "dirty": False,
        "configuration": {},
        "files": {"weather/msm/catalog.json": {
            "sha256": hashlib.sha256(catalog_raw).hexdigest(), "bytes": len(catalog_raw),
        }},
    }
    state = SimpleNamespace(candidate=candidate, catalog=catalog, catalog_raw=catalog_raw,
                            canonical_raw=json.dumps(candidate).encode(), calls=[])

    def fetch(url, limit):
        state.calls.append((url, limit))
        return state.canonical_raw if url.endswith("/release.json") else state.catalog_raw

    monkeypatch.setattr(ops, "fetch", fetch)
    monkeypatch.setitem(sys.modules, "operations", ops)
    monkeypatch.setenv("EXPECTED_SOURCE", candidate["commit"])
    monkeypatch.setenv("STATIC_ORIGIN", "https://example.com/")
    path = tmp_path / "release.json"
    path.write_text(json.dumps(candidate))
    monkeypatch.setattr(sys, "argv", ["check_candidate.py", str(path), "--remote"])

    def run():
        path.write_text(json.dumps(state.candidate))
        runpy.run_path(str(SCRIPTS / "check_candidate.py"), run_name="__main__")

    state.run = run
    return state


@pytest.mark.parametrize("change", [None, "commit", "dirty", "configuration", "files", "invalid"])
def test_remote_checker_keeps_exact_inventory_equality(remote_checker, change):
    state = remote_checker
    canonical = copy.deepcopy(state.candidate)
    if change and change != "invalid":
        canonical[change] = "different"
    state.canonical_raw = ("not json" if change == "invalid" else json.dumps(canonical)).encode()
    if change:
        with pytest.raises(ValueError):
            state.run()
    else:
        state.run()
    assert state.calls == [("https://example.com/release.json", 8 * 1024 * 1024)] + (
        [] if change else [("https://example.com/weather/msm/catalog.json", 8 * 1024 * 1024)]
    )


def test_matching_release_with_old_catalog_cannot_pass(remote_checker):
    state = remote_checker
    state.catalog["generated_at"] = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    state.catalog["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    state.catalog_raw = json.dumps(state.catalog).encode()
    with pytest.raises(ValueError, match="catalog.*inventory") as error:
        state.run()
    evidence = json.loads("{" + str(error.value).split(": {", 1)[1])
    assert evidence["catalog_sha256"] == hashlib.sha256(state.catalog_raw).hexdigest()
    assert evidence["candidate_catalog_sha256"] == state.candidate["files"][
        "weather/msm/catalog.json"
    ]["sha256"]
    assert evidence["catalog_matches_candidate"] is False
    assert evidence["catalog_generated_at"] == state.catalog["generated_at"]
    assert evidence["catalog_expires_at"] == state.catalog["expires_at"]
    assert evidence["weather_seconds_remaining"] < 0
    assert datetime.fromisoformat(evidence["catalog_received_at"]).tzinfo == UTC
    assert evidence["catalog_checked_at"] == evidence["catalog_received_at"]


def test_matching_release_and_fresh_but_different_catalog_cannot_pass(remote_checker):
    state = remote_checker
    state.catalog["assets"] = [{"different": True}]
    state.catalog_raw = json.dumps(state.catalog).encode()
    with pytest.raises(ValueError, match="catalog.*inventory"):
        state.run()


def seal_catalog(state):
    state.catalog_raw = json.dumps(state.catalog).encode()
    state.candidate["files"]["weather/msm/catalog.json"] = {
        "sha256": hashlib.sha256(state.catalog_raw).hexdigest(), "bytes": len(state.catalog_raw),
    }
    state.canonical_raw = json.dumps(state.candidate).encode()


@pytest.mark.parametrize("change", ["expires_at", "generated_at", "assets"])
def test_exact_catalog_must_still_pass_freshness(remote_checker, change):
    state = remote_checker
    state.catalog[change] = {
        "expires_at": (datetime.now(UTC) + timedelta(minutes=119)).isoformat(),
        "generated_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        "assets": [],
    }[change]
    seal_catalog(state)
    with pytest.raises(ValueError, match="freshness|future") as error:
        state.run()
    evidence = json.loads("{" + str(error.value).split(": {", 1)[1])
    assert evidence["catalog_matches_candidate"] is True


def test_catalog_byte_length_is_checked_even_when_hash_matches(remote_checker):
    state = remote_checker
    state.candidate["files"]["weather/msm/catalog.json"]["bytes"] += 1
    state.canonical_raw = json.dumps(state.candidate).encode()
    with pytest.raises(ValueError, match="catalog.*inventory"):
        state.run()


def test_catalog_hash_is_checked_even_when_byte_length_matches(remote_checker):
    state = remote_checker
    state.catalog_raw = state.catalog_raw.replace(b'"assets": [{}]', b'"assets": [42]')
    assert len(state.catalog_raw) == state.candidate["files"]["weather/msm/catalog.json"]["bytes"]
    with pytest.raises(ValueError, match="catalog.*inventory"):
        state.run()


def test_publication_freshness_uses_catalog_receipt_time(remote_checker, monkeypatch):
    state = remote_checker
    before = datetime.now(UTC)
    state.catalog["expires_at"] = (before + timedelta(seconds=7210)).isoformat()
    seal_catalog(state)
    received = before + timedelta(seconds=20)
    monkeypatch.setattr(
        ops, "datetime",
        SimpleNamespace(now=lambda zone: received, fromisoformat=datetime.fromisoformat),
    )
    with pytest.raises(ValueError, match="freshness") as error:
        state.run()
    evidence = json.loads("{" + str(error.value).split(": {", 1)[1])
    assert evidence["weather_seconds_remaining"] == 7190
    assert evidence["catalog_received_at"] == received.isoformat()


@pytest.mark.parametrize("raw", [b"not json", b"{}", b"null", b"[]"])
def test_bad_catalog_cannot_pass(remote_checker, raw):
    remote_checker.catalog_raw = raw
    with pytest.raises(ValueError, match="Invalid MSM catalog"):
        remote_checker.run()


def test_exact_catalog_success_includes_diagnostics(remote_checker, capsys):
    remote_checker.run()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "OK"
    assert result["catalog_matches_candidate"] is True
    assert result["catalog_sha256"] == result["candidate_catalog_sha256"]
    assert result["catalog_bytes"] == result["candidate_catalog_bytes"]
    assert result["catalog_generated_at"] == remote_checker.catalog["generated_at"]
    assert result["catalog_expires_at"] == remote_checker.catalog["expires_at"]
    assert 7200 < result["weather_seconds_remaining"] <= 21600


def test_actual_catalog_mismatch_retries_within_existing_budget(
    remote_checker, monkeypatch, clock, capsys
):
    state = remote_checker
    fresh = state.catalog_raw
    state.catalog_raw = b'{"assets":[],"expires_at":"2020-01-01T00:00:00Z"}'

    def run(command, **kwargs):
        clock.calls.append((command, kwargs))
        try:
            state.run()
        except ValueError as error:
            state.catalog_raw = fresh
            return subprocess.CompletedProcess(command, 1, "", str(error))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(poll.subprocess, "run", run)
    poll.wait_for_candidate(Path("release.json"))
    assert clock.sleeps == [10]
    assert len(clock.calls) == 2
    output = capsys.readouterr()
    assert '"catalog_matches_candidate": false' in output.err
    assert "verified after 2 attempt(s)" in output.err


def test_standalone_monitor_failure_identifies_separate_response(remote_checker):
    state = remote_checker
    state.catalog["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    state.catalog_raw = json.dumps(state.catalog).encode()
    with pytest.raises(ValueError, match="freshness") as error:
        ops.monitor("https://example.com", datetime.now(UTC))
    evidence = json.loads("{" + str(error.value).split(": {", 1)[1])
    assert evidence["catalog_expires_at"] == state.catalog["expires_at"]
    assert evidence["catalog_sha256"] == hashlib.sha256(state.catalog_raw).hexdigest()
    assert "candidate_catalog_sha256" not in evidence


@pytest.mark.parametrize("identity_ok", [False, True])
def test_workflow_only_monitors_after_identity_success(tmp_path, identity_ok):
    workflow = (ROOT / ".github/workflows/static-production.yml").read_text()
    verification = workflow.split("- name: Verify canonical deployment and fresh catalog\n")[1]
    script = textwrap.dedent(verification.split("        run: |\n")[1])
    assert "--timeout-seconds 300 --interval-seconds 10 --attempt-timeout-seconds 30" in script
    fake_python = tmp_path / "python3"
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$1" >> "$CALL_LOG"\n'
        'case "$1" in *wait_for_candidate.py) exit "$IDENTITY_EXIT";; esac\n'
    )
    fake_python.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "STATIC_ORIGIN": "https://example.com",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "CALL_LOG": str(tmp_path / "calls"),
            "IDENTITY_EXIT": "0" if identity_ok else "1",
            "EXPECTED_SOURCE": "a" * 40,
        },
    )
    assert (result.returncode == 0) == identity_ok
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls == ["tooling/scripts/static_ops/wait_for_candidate.py"] + (
        ["tooling/scripts/static_ops/freshness.py",
         "tooling/scripts/static_ops/operations.py"] if identity_ok else []
    )
