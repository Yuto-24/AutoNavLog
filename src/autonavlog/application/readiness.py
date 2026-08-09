from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import UUID

from autonavlog.domain.calculation import CalculationOutcome, Issue
from autonavlog.domain.enums import IssueSeverity, ProjectStatus
from autonavlog.domain.planning import PersistedUiState
from autonavlog.domain.project import Project

from .fingerprints import canonical_json, make_fingerprint


class IssueProducer(str, Enum):
    OUTCOME = "OUTCOME"
    REFERENCE_DATA = "REFERENCE_DATA"
    STALE_RESULT = "STALE_RESULT"
    DEFAULTS_REVIEW = "DEFAULTS_REVIEW"
    MANUAL_QNH = "MANUAL_QNH"
    PROJECT_VALIDATION = "PROJECT_VALIDATION"


@dataclass(frozen=True)
class IssueContext:
    code: str
    section_id: UUID | None
    segment_sequence: int | None
    canonical_cause_json: str
    cause_fingerprint: str
    ack_key: str


@dataclass(frozen=True)
class EffectiveIssue:
    issue: Issue
    ctx: IssueContext
    effective_severity: IssueSeverity
    effective_acknowledgement_required: bool
    producers: frozenset[IssueProducer]


def create_effective_issue(
    issue: Issue,
    *,
    producer: IssueProducer,
    cause: dict[str, Any],
) -> EffectiveIssue:
    """Freeze one displayed issue and its canonical safety identity."""

    frozen_issue = Issue.model_validate(issue.model_dump(mode="python"))
    cause_fields = {
        "code": frozen_issue.code,
        "section_id": frozen_issue.section_id,
        "segment_sequence": frozen_issue.segment_sequence,
        "cause": cause,
    }
    cause_json = canonical_json(kind="issue_cause", fields=cause_fields)
    cause_fingerprint = hashlib.sha256(cause_json.encode("utf-8")).hexdigest()
    ack_key = make_fingerprint(
        kind="warning_ack",
        fields={
            "code": frozen_issue.code,
            "section_id": frozen_issue.section_id,
            "segment_sequence": frozen_issue.segment_sequence,
            "cause_fingerprint": cause_fingerprint,
        },
    )
    return EffectiveIssue(
        issue=frozen_issue,
        ctx=IssueContext(
            code=frozen_issue.code,
            section_id=frozen_issue.section_id,
            segment_sequence=frozen_issue.segment_sequence,
            canonical_cause_json=cause_json,
            cause_fingerprint=cause_fingerprint,
            ack_key=ack_key,
        ),
        effective_severity=frozen_issue.severity,
        effective_acknowledgement_required=(frozen_issue.acknowledgement_required),
        producers=frozenset({producer}),
    )


def dedupe_effective_issues(
    issues: Iterable[EffectiveIssue],
) -> list[EffectiveIssue]:
    merged: dict[str, EffectiveIssue] = {}
    order: list[str] = []
    for current in issues:
        key = current.ctx.cause_fingerprint
        previous = merged.get(key)
        if previous is None:
            merged[key] = current
            order.append(key)
            continue
        severity = (
            IssueSeverity.BLOCKER
            if IssueSeverity.BLOCKER in {previous.effective_severity, current.effective_severity}
            else IssueSeverity.WARNING
        )
        acknowledgement_required = (
            previous.effective_acknowledgement_required
            or current.effective_acknowledgement_required
        )
        display = previous.issue.model_copy(
            deep=True,
            update={
                "severity": severity,
                "acknowledgement_required": acknowledgement_required,
            },
        )
        merged[key] = EffectiveIssue(
            issue=display,
            ctx=previous.ctx,
            effective_severity=severity,
            effective_acknowledgement_required=acknowledgement_required,
            producers=previous.producers | current.producers,
        )
    return [merged[key] for key in order]


def derive_project_status(
    effective_issues: Iterable[EffectiveIssue],
    acknowledged_warning_codes: set[str],
    *,
    outcome_exists: bool,
) -> ProjectStatus:
    issues = list(effective_issues)
    if any(item.ctx.code == "PROJECT_STATE_INVALID" for item in issues):
        return ProjectStatus.MANUAL_INPUT_REQUIRED if outcome_exists else ProjectStatus.DRAFT
    if any(item.ctx.code == "ROUTE_INCOMPLETE" for item in issues):
        return ProjectStatus.ROUTE_INCOMPLETE
    blockers = [item for item in issues if item.effective_severity == IssueSeverity.BLOCKER]
    if blockers:
        if any(item.ctx.code.startswith(("FORECAST", "WEATHER")) for item in blockers):
            return ProjectStatus.WEATHER_PENDING
        return ProjectStatus.MANUAL_INPUT_REQUIRED
    if not outcome_exists:
        return ProjectStatus.DRAFT
    if any(
        item.effective_acknowledgement_required
        and item.ctx.ack_key not in acknowledged_warning_codes
        for item in issues
    ):
        return ProjectStatus.CALCULATION_WARNING
    return ProjectStatus.READY_FOR_COPY


def can_render_transfer_aid(
    effective_issues: Iterable[EffectiveIssue],
    acknowledged_warning_codes: set[str],
    *,
    outcome_exists: bool,
    calculation_is_current: bool,
    editable: bool,
) -> bool:
    issues = list(effective_issues)
    return (
        outcome_exists
        and editable
        and calculation_is_current
        and not any(item.effective_severity == IssueSeverity.BLOCKER for item in issues)
        and all(
            not item.effective_acknowledgement_required
            or item.ctx.ack_key in acknowledged_warning_codes
            for item in issues
        )
    )


@dataclass(frozen=True)
class ReadinessEvaluation:
    effective_issues: tuple[EffectiveIssue, ...]
    status: ProjectStatus
    calculation_is_current: bool
    transfer_aid_allowed: bool


def _readiness_blocker(code: str, message: str) -> Issue:
    return Issue(
        code=code,
        severity=IssueSeverity.BLOCKER,
        message=message,
    )


def _source_independent_issue_cause(issue: Issue) -> dict[str, Any] | None:
    if issue.code == "PERFORMANCE_DATA_UNVERIFIED":
        return {
            "reason": issue.metadata.get("reason"),
        }
    return None


def outcome_effective_issues(
    outcome: CalculationOutcome,
    *,
    calculated_against_fingerprint: str | None,
) -> list[EffectiveIssue]:
    return [
        create_effective_issue(
            issue,
            producer=IssueProducer.OUTCOME,
            cause=_source_independent_issue_cause(issue)
            or {
                "calculated_against_fingerprint": (calculated_against_fingerprint),
                "metadata": issue.metadata,
            },
        )
        for issue in outcome.issues
    ]


def collect_effective_issues(
    project: Project,
    outcome: CalculationOutcome | None,
    *,
    ui_state: PersistedUiState | None,
    current_calculation_input_fingerprint: str | None,
    current_defaults_review_fingerprint: str | None = None,
    current_manual_qnh_fingerprint: str | None = None,
    reference_data_issues: Iterable[Issue] = (),
    project_validation_issues: Iterable[Issue] = (),
) -> list[EffectiveIssue]:
    """Assemble the only safety issue set used for status and output gates."""

    effective: list[EffectiveIssue] = []
    calculated_against = None if ui_state is None else ui_state.calculated_against_fingerprint
    if outcome is not None:
        effective.extend(
            outcome_effective_issues(
                outcome,
                calculated_against_fingerprint=calculated_against,
            )
        )

    reference_snapshot = (
        None
        if ui_state is None or ui_state.reference_data_snapshot is None
        else ui_state.reference_data_snapshot.model_dump(mode="python")
    )
    for issue in reference_data_issues:
        effective.append(
            create_effective_issue(
                issue,
                producer=IssueProducer.REFERENCE_DATA,
                cause=_source_independent_issue_cause(issue)
                or {
                    "selected_reference_snapshot": reference_snapshot,
                    "metadata": issue.metadata,
                },
            )
        )

    if ui_state is None:
        invalid = _readiness_blocker(
            "PROJECT_STATE_INVALID",
            "保存済みUI状態を確認し、参照データとVREPを再選択してください。",
        )
        effective.append(
            create_effective_issue(
                invalid,
                producer=IssueProducer.PROJECT_VALIDATION,
                cause={"reason": "PERSISTED_UI_STATE_MISSING_OR_INVALID"},
            )
        )
    elif outcome is not None and (
        current_calculation_input_fingerprint is None
        or calculated_against != current_calculation_input_fingerprint
    ):
        stale = _readiness_blocker(
            "RECALCULATION_REQUIRED",
            "計算依存入力が変わりました。NAV LOGを再計算してください。",
        )
        effective.append(
            create_effective_issue(
                stale,
                producer=IssueProducer.STALE_RESULT,
                cause={
                    "calculated_against_fingerprint": calculated_against,
                    "current_calculation_input_fingerprint": (
                        current_calculation_input_fingerprint
                    ),
                },
            )
        )

    if ui_state is not None and current_defaults_review_fingerprint is not None:
        if ui_state.defaults_review_fingerprint != current_defaults_review_fingerprint:
            defaults_issue = _readiness_blocker(
                "DEFAULTS_NOT_REVIEWED",
                "ALT・Phase・燃料・偏差などの既定値を確認してください。",
            )
            effective.append(
                create_effective_issue(
                    defaults_issue,
                    producer=IssueProducer.DEFAULTS_REVIEW,
                    cause={
                        "reviewed_fingerprint": (ui_state.defaults_review_fingerprint),
                        "current_defaults_review_fingerprint": (
                            current_defaults_review_fingerprint
                        ),
                    },
                )
            )

    if (
        project.manual_qnh_hpa is not None
        and ui_state is not None
        and current_manual_qnh_fingerprint is not None
        and ui_state.manual_qnh_fingerprint != current_manual_qnh_fingerprint
    ):
        qnh_issue = _readiness_blocker(
            "MANUAL_QNH_RECONFIRM_REQUIRED",
            "DATE・ETD・出発地に対する手動QNHを再確認してください。",
        )
        effective.append(
            create_effective_issue(
                qnh_issue,
                producer=IssueProducer.MANUAL_QNH,
                cause={
                    "confirmed_fingerprint": (ui_state.manual_qnh_fingerprint),
                    "current_manual_qnh_fingerprint": (current_manual_qnh_fingerprint),
                },
            )
        )

    for issue in project_validation_issues:
        effective.append(
            create_effective_issue(
                issue,
                producer=IssueProducer.PROJECT_VALIDATION,
                cause={"metadata": issue.metadata},
            )
        )
    return dedupe_effective_issues(effective)


def evaluate_readiness(
    project: Project,
    outcome: CalculationOutcome | None,
    *,
    ui_state: PersistedUiState | None,
    current_calculation_input_fingerprint: str | None,
    current_defaults_review_fingerprint: str | None = None,
    current_manual_qnh_fingerprint: str | None = None,
    reference_data_issues: Iterable[Issue] = (),
    project_validation_issues: Iterable[Issue] = (),
    editable: bool = True,
) -> ReadinessEvaluation:
    effective = collect_effective_issues(
        project,
        outcome,
        ui_state=ui_state,
        current_calculation_input_fingerprint=(current_calculation_input_fingerprint),
        current_defaults_review_fingerprint=(current_defaults_review_fingerprint),
        current_manual_qnh_fingerprint=current_manual_qnh_fingerprint,
        reference_data_issues=reference_data_issues,
        project_validation_issues=project_validation_issues,
    )
    calculation_is_current = bool(
        outcome is not None
        and ui_state is not None
        and current_calculation_input_fingerprint is not None
        and ui_state.calculated_against_fingerprint == current_calculation_input_fingerprint
    )
    status = derive_project_status(
        effective,
        project.acknowledged_warning_codes,
        outcome_exists=outcome is not None,
    )
    allowed = can_render_transfer_aid(
        effective,
        project.acknowledged_warning_codes,
        outcome_exists=outcome is not None,
        calculation_is_current=calculation_is_current,
        editable=editable,
    )
    return ReadinessEvaluation(
        effective_issues=tuple(effective),
        status=status,
        calculation_is_current=calculation_is_current,
        transfer_aid_allowed=allowed,
    )
