"""Catalog monitoring is independent; opted-in quota failures remain failures."""

import os
import re
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
    assert len(COLLECTION) == 1
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


def monitor_jobs():
    return dict(re.findall(
        r"^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)",
        MONITOR.split("jobs:\n", 1)[1], re.MULTILINE | re.DOTALL,
    ))


@pytest.mark.parametrize("weather,quota,ref,expected", [
    ("", "", "refs/heads/main", (False, False)),
    ("true", "", "refs/heads/main", (True, False)),
    ("true", "false", "refs/heads/main", (True, False)),
    ("true", "unknown", "refs/heads/main", (True, False)),
    ("", "true", "refs/heads/main", (False, True)),
    ("true", "true", "refs/heads/main", (True, True)),
    ("true", "true", "refs/heads/feature", (False, False)),
])
def test_monitor_jobs_have_independent_explicit_opt_in(weather, quota, ref, expected):
    jobs = monitor_jobs()
    assert set(jobs) == {"monitor", "quota-monitor"}
    for (name, enabled) in zip(("monitor", "quota-monitor"), expected, strict=True):
        job = jobs[name]
        assert "needs:" not in job  # either job may fail/skip without suppressing the other
        condition = re.search(r"^    if: (.+)$", job, re.MULTILINE)
        assert condition is not None
        expression = condition[1].replace("vars.STATIC_MONITOR_ENABLED", repr(weather))
        expression = expression.replace("vars.STATIC_QUOTA_MONITOR_ENABLED", repr(quota))
        expression = expression.replace("github.ref", repr(ref)).replace("&&", "and")
        assert bool(eval(expression, {"__builtins__": {}})) == enabled


def test_catalog_job_has_no_provider_credentials_or_collection():
    jobs = monitor_jobs()
    assert "collection_gate.py" not in jobs["monitor"]
    assert "secrets." not in jobs["monitor"]
    assert "operations.py monitor" not in jobs["quota-monitor"]
    assert "if: always()" not in COLLECTION[0]  # failed checkout must not run collection
    assert "--publication" not in COLLECTION[0]


@pytest.mark.parametrize("exit_code", [0, 1])
def test_catalog_failure_is_preserved_and_does_not_claim_quota_acceptance(tmp_path, exit_code):
    python = tmp_path / "python3"
    python.write_text(f'#!/bin/sh\nprintf \'catalog-result\\n\'\nexit {exit_code}\n')
    python.chmod(0o755)
    summary = tmp_path / "summary"
    job_steps = steps(monitor_jobs()["monitor"])
    check = next(step for step in job_steps if "operations.py monitor" in step)
    command = check.split("        run: ", 1)[1].strip()
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
           "GITHUB_STEP_SUMMARY": str(summary), "STATIC_ORIGIN": "https://example.test",
           "QUOTA_MONITOR_ENABLED": ""}
    result = subprocess.run(["bash", "-e", "-c", command], env=env, check=False)
    assert result.returncode == exit_code
    scope = next(step for step in job_steps if "Record monitoring scope" in step)
    assert "if: always()" in scope
    subprocess.run(["bash", "-e", "-c", dedent(scope.split("        run: |\n", 1)[1])],
                   env=env, check=True)
    assert summary.read_text().startswith("catalog-result\n")
    assert "deferred to #281" in summary.read_text()
    assert "UNKNOWN" in summary.read_text()


def test_publication_is_independent_and_uses_trusted_gates_and_isolated_upload():
    assert "collection_gate.py" not in PRODUCTION
    assert "STATIC_CLOUDFLARE_READ_TOKEN" not in PRODUCTION
    assert "STATIC_GITHUB_READ_TOKEN" not in PRODUCTION
    assert "STATIC_RUNNER_CLASS: ubuntu-latest" in PRODUCTION
    assert "runs-on: ubuntu-latest" in PRODUCTION
    assert PRODUCTION.index("publication_gate.py") < PRODUCTION.index("path: app")
    assert (
        PRODUCTION.index("Production Browser E2E")
        < PRODUCTION.index("Install independent upload CLI")
        < PRODUCTION.index("Repeat publication gates")
        < PRODUCTION.index("pages deploy dist-static")
    )
    assert "working-directory: ${{ runner.temp }}/static-upload" in PRODUCTION
    assert 'pages deploy dist-static --project-name navmate --branch main' in PRODUCTION
    assert '--config' not in PRODUCTION
    assert "node tooling/web/scripts/check-static.mjs app/web/dist-static" in PRODUCTION
    candidate_step = next(
        step for step in steps(PRODUCTION) if "Build and test candidate in isolation" in step
    )
    assert "docker run --rm" in candidate_step
    assert 'source="$GITHUB_WORKSPACE/app",target=/app' in candidate_step
    assert 'source="$RUNNER_TEMP/feed",target=/feed,readonly' in candidate_step
    assert "--env AUTONAVLOG_MSM_FEED=/feed" in candidate_step
    assert "--env GITHUB" not in candidate_step
    assert "target=/tooling" not in candidate_step
    assert "docker.sock" not in candidate_step
    assert "--privileged" not in candidate_step
    assert "--producer tooling/scripts/prepare_msm_feed.py" in PRODUCTION
    assert 'validate_feed.py "$RUNNER_TEMP/static-upload/dist-static/weather/msm"' in PRODUCTION
    assert "cache: npm" not in PRODUCTION
    assert "save-cache" not in PRODUCTION
    assert "upload-artifact" not in PRODUCTION
    assert "vars.STATIC_AUTOMATION_ENABLED == 'true'" in PRODUCTION
    assert "default: false" in PRODUCTION


def test_publication_job_stays_within_generation_hour_margin():
    # A longer job could cross two boundaries and exhaust the producer's extra hour.
    timeout = re.search(r"^    timeout-minutes: (\d+)$", PRODUCTION, re.MULTILINE)
    assert timeout is not None
    assert 0 < int(timeout[1]) <= 60


@pytest.mark.parametrize("step", COLLECTION, ids=["monitor"])
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


def test_hourly_preflight_precedes_all_heavy_work_and_preserves_serialization():
    assert "cron: '23 * * * *'" in PRODUCTION
    assert "group: static-production\n  cancel-in-progress: false" in PRODUCTION
    preflight = next(step for step in steps(PRODUCTION) if "id: preflight" in step)
    assert "if:" not in preflight
    assert "set -euo pipefail" in preflight
    assert (PRODUCTION.index("Install trusted producer dependencies")
            < PRODUCTION.index("id: preflight"))
    assert PRODUCTION.index("id: preflight") < PRODUCTION.index("docker build")
    assert PRODUCTION.index("id: preflight") < PRODUCTION.index("operations.py restore")


@pytest.mark.parametrize("event,proceed,publish,build,upload", [
    ("schedule", "true", False, True, True),
    ("schedule", "false", False, False, False),
    ("schedule", "", False, False, False),
    ("schedule", "unknown", False, False, False),
    ("workflow_dispatch", "", False, False, False),
    ("workflow_dispatch", "", True, False, False),
    ("workflow_dispatch", "false", True, False, False),
    ("workflow_dispatch", "true", False, True, False),
    ("workflow_dispatch", "true", True, True, True),
])
def test_preflight_event_matrix(event, proceed, publish, build, upload):
    workflow_steps = steps(PRODUCTION)
    boundary = next(i for i, step in enumerate(workflow_steps) if "id: preflight" in step)
    heavy = workflow_steps[boundary + 1:]
    assert len(heavy) == 10
    for step in heavy:
        condition = re.search(r"^        if: (.+)$", step, re.MULTILINE)
        assert condition is not None
        expression = condition[1]
        assert "always()" not in expression  # implicit success() must still stop on failure
        expression = expression.replace("github.event_name", repr(event))
        expression = expression.replace("steps.preflight.outputs.proceed", repr(proceed))
        expression = expression.replace("inputs.publish", repr(publish))
        expression = expression.replace("&&", "and").replace("||", "or")
        is_upload = workflow_steps.index(step) >= next(
            i for i, candidate in enumerate(workflow_steps)
            if "Install independent upload CLI" in candidate)
        assert bool(eval(expression, {"__builtins__": {}})) == (upload if is_upload else build)


def test_external_path_preserves_approval_identity_and_final_recheck():
    assert "STATIC_EXTERNAL_TRIGGER_ENABLED == 'true'" in PRODUCTION
    assert "vars.STATIC_AUTOMATION_ENABLED == 'true'" in PRODUCTION
    assert "inputs.publish && 'static-publication'" in PRODUCTION
    assert "ref: ${{ steps.source.outputs.sha }}" in PRODUCTION
    assert "external_trigger_gate.py" in PRODUCTION
    upload = next(step for step in steps(PRODUCTION) if "pages deploy" in step)
    assert (upload.index("--verify-context") < upload.index("--candidate")
            < upload.index("pages deploy"))
    assert "--candidate dist-static/weather/msm/catalog.json" in upload
