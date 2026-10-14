"""First-run capability workflow cannot activate production or expose other secrets."""

import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).parents[2]
WORKFLOW = (ROOT / ".github/workflows/static-provider-probe.yml").read_text()
GUARD = dedent(WORKFLOW.split("        run: |\n", 1)[1].split("      - uses:", 1)[0])
SHA = "a" * 40


@pytest.mark.parametrize(
    "reviewed,actual,check,accepted",
    [
        (SHA, SHA, "cloudflare_pages_projects", True),
        (SHA, SHA, "cloudflare_subscriptions", True),
        (SHA, SHA, "cloudflare_worker_invocations", True),
        (SHA, SHA, "cloudflare_worker_settings", True),
        (SHA, SHA, "cloudflare_pages_target", True),
        ("", SHA, "cloudflare_pages_projects", False),
        (SHA, "b" * 40, "cloudflare_pages_projects", False),
        ("main", "main", "cloudflare_pages_projects", False),
        (SHA, SHA, "github_billing_usage", False),
        (SHA, SHA, "$(exit 0)", False),
        ("$(exit 0)", SHA, "cloudflare_pages_projects", False),
    ],
)
def test_reviewed_identity_guard_executes_before_secret_step(reviewed, actual, check, accepted):
    result = subprocess.run(
        ["bash", "-c", GUARD],
        env={**os.environ, "REVIEWED_SHA": reviewed, "GITHUB_SHA": actual, "PROBE_CHECK": check},
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is accepted
    assert result.stdout == result.stderr == ""


def test_workflow_is_manual_cloudflare_only_and_cannot_accept_quota():
    triggers = WORKFLOW.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "  workflow_dispatch:" in triggers
    assert all(event not in triggers for event in ("schedule:", "push:", "pull_request"))
    assert "contents: read" in WORKFLOW and "write" not in WORKFLOW
    assert "ref: ${{ github.sha }}" in WORKFLOW
    assert "uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.3.1" in WORKFLOW
    assert "persist-credentials: false" in WORKFLOW
    assert WORKFLOW.count("secrets.") == 1
    assert "${{ secrets.STATIC_CLOUDFLARE_READ_TOKEN }}" in WORKFLOW
    assert "secrets." not in WORKFLOW.split("      - name: Probe one", 1)[0]
    assert "STATIC_QUOTA_REPORT" not in WORKFLOW
    assert "environment:" not in WORKFLOW and "upload-artifact" not in WORKFLOW
    assert "continue-on-error" not in WORKFLOW
    command = 'python3 scripts/static_ops/probe_providers.py --check "$PROBE_CHECK"'
    assert f"set -euo pipefail\n          {command}" in WORKFLOW
    assert 'tee -a "$GITHUB_STEP_SUMMARY"' in WORKFLOW
