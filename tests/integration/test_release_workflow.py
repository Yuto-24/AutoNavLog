from pathlib import Path

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "release-to-drive.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_workflow_requires_a_fixed_utc_live_msm_time() -> None:
    text = _workflow_text()

    assert "msm_valid_time_utc:" in text
    assert "required: true" in text
    assert "MSM_VALID_TIME_UTC: ${{ inputs.msm_valid_time_utc }}" in text
    assert "YYYY-MM-DDTHH:MM:SSZ" in text
    assert "datetime.now" not in text
    assert "date -u" not in text


def test_release_workflow_validates_isolated_wheels_before_manifest_and_upload() -> None:
    text = _workflow_text()

    assemble = text.index("- name: Assemble runtime data")
    isolated_install = text.index("- name: Install release wheels in isolated environment")
    runtime_data = text.index("- name: Validate release runtime data")
    real_msm = text.index("- name: Validate live real MSM")
    enforce = text.index("- name: Enforce release validation")
    manifest = text.index("- name: Build release manifest")
    upload = text.index("- name: Upload versioned release to Drive")

    assert assemble < isolated_install < runtime_data < real_msm < enforce < manifest < upload
    assert "release/wheels/autonavlog-0.2.0-py3-none-any.whl" in text
    assert "release/wheels/jma_msm_wind-0.2.1-py3-none-any.whl" in text
    assert "scripts/validate_runtime_data.py" in text
    assert "scripts/validate_real_msm_release.py" in text
    assert "--live" in text
    assert '--valid-time "$MSM_VALID_TIME_UTC"' in text


def test_release_workflow_preserves_both_json_reports_and_fails_closed() -> None:
    text = _workflow_text()

    assert "--output release/acceptance/runtime-data-acceptance.json" in text
    assert "--output release/acceptance/real-msm-acceptance.json" in text
    assert text.count("continue-on-error: true") == 2
    assert "uses: actions/upload-artifact@v4" in text
    assert "path: release/acceptance/*.json" in text
    assert "RUNTIME_DATA_OUTCOME: ${{ steps.runtime_data_validation.outcome }}" in text
    assert "REAL_MSM_OUTCOME: ${{ steps.real_msm_validation.outcome }}" in text
    assert (
        '[[ "$RUNTIME_DATA_OUTCOME" != "success" || "$REAL_MSM_OUTCOME" != "success" ]]'
    ) in text
