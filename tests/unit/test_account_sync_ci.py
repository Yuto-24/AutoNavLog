from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def command_runner(tmp_path):
    binary = tmp_path / "npm"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
                      '[ "$*" != "${FAIL_COMMAND:-}" ]\n')
    binary.chmod(0o755)
    calls = tmp_path / "calls"

    def run(suite, fail=""):
        result = subprocess.run(
            ["sh", str(ROOT / "scripts/run_account_sync_suite.sh"), *suite],
            env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
                 "CALLS": str(calls), "FAIL_COMMAND": fail},
            capture_output=True, text=True, timeout=5,
        )
        return result, calls.read_text().splitlines() if calls.exists() else []
    return run


@pytest.mark.parametrize("project", ["chromium", "ipad-webkit", "iphone-webkit"])
def test_sync_projects_keep_all_tests_and_worker_setting(command_runner, project):
    result, calls = command_runner([project])
    assert result.returncode == 0
    assert calls == [f"--prefix web run test:sync -- --project={project} --workers=2"]


def test_contracts_keep_rules_storage_and_both_migration_projects(command_runner):
    result, calls = command_runner(["contracts"])
    assert result.returncode == 0
    assert calls == [f"--prefix web run test:{suite}"
                     for suite in ["firestore", "storage-upgrade", "migration"]]


@pytest.mark.parametrize("suite", [[], ["unknown"], ["chromium; true"]])
def test_unknown_suite_fails_without_running_a_test(command_runner, suite):
    result, calls = command_runner(suite)
    assert result.returncode == 2
    assert calls == []


def test_failed_rules_stop_remaining_contract_tests(command_runner):
    result, calls = command_runner(["contracts"], "--prefix web run test:firestore")
    assert result.returncode != 0
    assert calls == ["--prefix web run test:firestore"]


def test_workflow_requires_every_isolated_runner_and_complete_selection():
    source = (ROOT / ".github/workflows/test.yml").read_text()
    block = source.split("  account-sync-tests:\n")[1].split("  msm-trigger:\n")[0]
    assert "suite: [chromium, ipad-webkit, iphone-webkit, contracts]" in block
    assert "fail-fast: false" in block
    assert "needs: [release-sources, account-sync-tests]" in block
    assert "if: ${{ always() &&" in block
    assert 'run: test "$RESULT" = success' in block
    assert "emulators:exec --only firestore --project demo-autonavlog-sync" in block
    assert '"sh scripts/run_account_sync_suite.sh $SUITE"' in block
    assert "node web/scripts/check-account-sync-partition.mjs" in block
    assert "name: account-sync-${{ matrix.suite }}-failure" in block
