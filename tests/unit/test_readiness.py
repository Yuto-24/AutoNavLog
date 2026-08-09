from __future__ import annotations

from autonavlog.application.readiness import (
    IssueProducer,
    can_render_transfer_aid,
    create_effective_issue,
    dedupe_effective_issues,
    derive_project_status,
)
from autonavlog.domain.calculation import Issue
from autonavlog.domain.enums import IssueSeverity, ProjectStatus


def _effective(
    code: str,
    *,
    severity: IssueSeverity = IssueSeverity.WARNING,
    acknowledgement_required: bool = False,
    cause: dict[str, object] | None = None,
    producer: IssueProducer = IssueProducer.OUTCOME,
):
    return create_effective_issue(
        Issue(
            code=code,
            severity=severity,
            message="fixture",
            acknowledgement_required=acknowledgement_required,
        ),
        producer=producer,
        cause=cause or {"fixture": 1},
    )


def test_effective_issue_identity_is_frozen_from_source_mutation() -> None:
    source = Issue(
        code="RCA_BEYOND_FIRST_TURN",
        severity=IssueSeverity.WARNING,
        message="original",
        acknowledgement_required=True,
        metadata={"distance": 10.0},
    )
    effective = create_effective_issue(
        source,
        producer=IssueProducer.OUTCOME,
        cause={"calculation": "a" * 64, "metadata": source.metadata},
    )
    source.code = "CHANGED"
    source.severity = IssueSeverity.BLOCKER
    source.metadata["distance"] = 0.0
    assert effective.ctx.code == "RCA_BEYOND_FIRST_TURN"
    assert effective.issue.code == "RCA_BEYOND_FIRST_TURN"
    assert effective.effective_severity == IssueSeverity.WARNING


def test_dedupe_uses_canonical_cause_and_preserves_safest_attributes() -> None:
    warning = _effective(
        "REFERENCE_DATA_PACK_INVALID",
        cause={"revision": "r1"},
        producer=IssueProducer.REFERENCE_DATA,
    )
    blocker = create_effective_issue(
        warning.issue.model_copy(
            update={
                "severity": IssueSeverity.BLOCKER,
                "acknowledgement_required": True,
            }
        ),
        producer=IssueProducer.PROJECT_VALIDATION,
        cause={"revision": "r1"},
    )
    merged = dedupe_effective_issues([warning, blocker])
    assert len(merged) == 1
    assert merged[0].effective_severity == IssueSeverity.BLOCKER
    assert merged[0].effective_acknowledgement_required is True
    assert merged[0].producers == {
        IssueProducer.REFERENCE_DATA,
        IssueProducer.PROJECT_VALIDATION,
    }


def test_raw_issue_code_does_not_acknowledge_a_warning() -> None:
    warning = _effective(
        "RCA_BEYOND_FIRST_TURN",
        acknowledgement_required=True,
    )
    assert (
        derive_project_status(
            [warning],
            {warning.ctx.code},
            outcome_exists=True,
        )
        == ProjectStatus.CALCULATION_WARNING
    )
    assert (
        derive_project_status(
            [warning],
            {warning.ctx.ack_key},
            outcome_exists=True,
        )
        == ProjectStatus.READY_FOR_COPY
    )


def test_status_derivation_prioritizes_route_weather_and_other_blockers() -> None:
    route = _effective(
        "ROUTE_INCOMPLETE",
        severity=IssueSeverity.BLOCKER,
    )
    weather = _effective(
        "WEATHER_QUERY_FAILED",
        severity=IssueSeverity.BLOCKER,
    )
    manual = _effective(
        "DEFAULTS_NOT_REVIEWED",
        severity=IssueSeverity.BLOCKER,
    )
    assert derive_project_status([route], set(), outcome_exists=False) == (
        ProjectStatus.ROUTE_INCOMPLETE
    )
    assert derive_project_status([weather], set(), outcome_exists=True) == (
        ProjectStatus.WEATHER_PENDING
    )
    assert derive_project_status([manual], set(), outcome_exists=True) == (
        ProjectStatus.MANUAL_INPUT_REQUIRED
    )


def test_transfer_gate_requires_current_editable_and_acknowledged_state() -> None:
    warning = _effective(
        "RCA_BEYOND_FIRST_TURN",
        acknowledgement_required=True,
    )
    assert not can_render_transfer_aid(
        [warning],
        set(),
        outcome_exists=True,
        calculation_is_current=True,
        editable=True,
    )
    assert can_render_transfer_aid(
        [warning],
        {warning.ctx.ack_key},
        outcome_exists=True,
        calculation_is_current=True,
        editable=True,
    )
    assert not can_render_transfer_aid(
        [],
        set(),
        outcome_exists=True,
        calculation_is_current=False,
        editable=True,
    )
