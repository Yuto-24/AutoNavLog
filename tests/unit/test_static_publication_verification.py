"""Canonical publication waits for exact identity and never advances after failure."""

import copy
import importlib.util
import json
import os
import runpy
import subprocess
import sys
import textwrap
import time
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


@pytest.mark.parametrize("change", [None, "commit", "dirty", "configuration", "files", "invalid"])
def test_remote_checker_keeps_exact_inventory_equality(monkeypatch, tmp_path, change):
    candidate = {
        "commit": "a" * 40,
        "dirty": False,
        "configuration": {},
        "files": {"weather/msm/catalog.json": {"sha256": "old", "bytes": 10}},
    }
    canonical = copy.deepcopy(candidate)
    if change and change != "invalid":
        canonical[change] = "different"
    raw = "not json" if change == "invalid" else json.dumps(canonical)
    calls = []

    def fetch(*args):
        calls.append(args)
        return raw

    monkeypatch.setitem(sys.modules, "operations", SimpleNamespace(fetch=fetch, release=None))
    monkeypatch.setenv("EXPECTED_SOURCE", candidate["commit"])
    monkeypatch.setenv("STATIC_ORIGIN", "https://example.com/")
    path = tmp_path / "release.json"
    path.write_text(json.dumps(candidate))
    monkeypatch.setattr(sys, "argv", ["check_candidate.py", str(path), "--remote"])
    if change:
        with pytest.raises(ValueError):
            runpy.run_path(str(SCRIPTS / "check_candidate.py"), run_name="__main__")
    else:
        runpy.run_path(str(SCRIPTS / "check_candidate.py"), run_name="__main__")
    assert calls == [("https://example.com/release.json", 8 * 1024 * 1024)]


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
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "CALL_LOG": str(tmp_path / "calls"),
            "IDENTITY_EXIT": "0" if identity_ok else "1",
        },
    )
    assert (result.returncode == 0) == identity_ok
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls == ["tooling/scripts/static_ops/wait_for_candidate.py"] + (
        ["tooling/scripts/static_ops/operations.py"] if identity_ok else []
    )
