"""Live gates call fresh collection; shell pipelines cannot hide a blocked verdict."""

import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).parents[2]
MONITOR = (ROOT / ".github/workflows/static-monitor.yml").read_text()
PRODUCTION = (ROOT / ".github/workflows/static-production.yml").read_text()


def steps(workflow):
    return workflow.split("      - ")[1:]


COLLECTION = [step for workflow in (MONITOR, PRODUCTION) for step in steps(workflow)
              if "collection_gate.py" in step]


def test_all_live_gates_collect_without_operator_report_or_reusable_output():
    assert len(COLLECTION) == 3
    for workflow in (MONITOR, PRODUCTION):
        assert "STATIC_QUOTA_REPORT" not in workflow
        assert "operations.py quota" not in workflow
        assert "continue-on-error" not in workflow
        assert "upload-artifact" not in workflow
        assert "STATIC_GOOGLE_ACCESS_TOKEN" not in workflow
        assert "id-token: write" not in workflow
        assert "STATIC_CLOUDFLARE_READ_TOKEN" not in workflow.split("    steps:")[0]
        assert "STATIC_GITHUB_READ_TOKEN" not in workflow.split("    steps:")[0]
        for step in steps(workflow):
            if step not in COLLECTION:
                assert "STATIC_CLOUDFLARE_READ_TOKEN" not in step
                assert "STATIC_GITHUB_READ_TOKEN" not in step
    for step in COLLECTION:
        assert "shell: bash" in step and "set -euo pipefail" in step
        assert 'tee -a "$GITHUB_STEP_SUMMARY"' in step
        assert "GITHUB_REPOSITORY_OWNER: ${{ github.repository_owner }}" in step
        assert "GITHUB_REPOSITORY: ${{ github.repository }}" in step
        assert "${{ secrets.STATIC_CLOUDFLARE_READ_TOKEN }}" in step
        assert "${{ secrets.STATIC_GITHUB_READ_TOKEN }}" in step


def test_monitor_still_runs_evidence_after_weather_failure_without_enabling_schedule():
    assert "if: always()" in COLLECTION[0]
    assert "vars.STATIC_MONITOR_ENABLED == 'true'" in MONITOR
    assert "github.ref == 'refs/heads/main'" in MONITOR
    assert "--publication" not in COLLECTION[0]


def test_publication_collects_before_build_and_again_before_upload():
    assert PRODUCTION.count("collection_gate.py --publication") == 2
    assert PRODUCTION.index("collection_gate.py") < PRODUCTION.index("path: app")
    assert (
        PRODUCTION.index("Production Browser E2E")
        < PRODUCTION.index("Install independent upload CLI")
        < PRODUCTION.index("Repeat publication gates")
        < PRODUCTION.index("Collect fresh provider evidence before upload")
        < PRODUCTION.index("wrangler pages deploy")
    )
    assert "if: github.event_name == 'schedule' || inputs.publish" in COLLECTION[2]
    assert "vars.STATIC_AUTOMATION_ENABLED == 'true'" in PRODUCTION
    assert "default: false" in PRODUCTION


@pytest.mark.parametrize("step", COLLECTION, ids=["monitor", "pre-build", "pre-upload"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_collection_shell_preserves_exit_and_redacted_summary(tmp_path, step, exit_code):
    python = tmp_path / "python3"
    python.write_text(f'#!/bin/sh\nprintf \'{{"status":"BLOCKED"}}\\n\'\nexit {exit_code}\n')
    python.chmod(0o755)
    summary = tmp_path / "summary"
    command = dedent(step.split("        run: |\n", 1)[1])
    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
             "GITHUB_STEP_SUMMARY": str(summary), "STATIC_QUOTA_REPORT": "SECRET-OLD-REPORT"},
    )
    assert result.returncode == exit_code
    assert summary.read_text() == result.stdout == '{"status":"BLOCKED"}\n'
    assert result.stderr == ""
