from __future__ import annotations

from typing import Any

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.effective_issue_snapshot import (
    EffectiveIssueSnapshotError,
    build_snapshot_effective_issues,
    load_snapshot_effective_issues,
)
from autonavlog.application.readiness import (
    IssueProducer,
    create_effective_issue,
    outcome_effective_issues,
)
from autonavlog.domain.calculation import Issue
from autonavlog.domain.enums import IssueSeverity
from autonavlog.weather.fake_provider import FakeWeatherProvider


def _snapshot_fixture(
    project: Any,
    airports: Any,
    performance_repository: Any,
):
    outcome = CalculationService(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    warning = Issue(
        code="RCA_BEYOND_FIRST_TURN",
        severity=IssueSeverity.WARNING,
        message="fixture warning",
        acknowledgement_required=True,
        metadata={"distance_nm": 12.5},
    )
    outcome = outcome.model_copy(update={"issues": [warning]})
    calculation_fingerprint = "a" * 64
    effective = outcome_effective_issues(
        outcome,
        calculated_against_fingerprint=calculation_fingerprint,
    )
    effective.append(
        create_effective_issue(
            Issue(
                code="DEFAULTS_NOT_REVIEWED",
                severity=IssueSeverity.BLOCKER,
                message="review defaults",
            ),
            producer=IssueProducer.DEFAULTS_REVIEW,
            cause={"current_defaults_review_fingerprint": "b" * 64},
        )
    )
    envelope = build_snapshot_effective_issues(
        effective,
        outcome,
        calculated_against_fingerprint=calculation_fingerprint,
    )
    return outcome, envelope


def test_snapshot_effective_issues_round_trip_with_outcome_binding(
    project: Any,
    airports: Any,
    performance_repository: Any,
) -> None:
    outcome, envelope = _snapshot_fixture(
        project,
        airports,
        performance_repository,
    )

    loaded, effective = load_snapshot_effective_issues(
        envelope.model_dump(mode="json"),
        outcome,
    )

    assert loaded.records_fingerprint == envelope.records_fingerprint
    assert {item.ctx.code for item in effective} == {
        "RCA_BEYOND_FIRST_TURN",
        "DEFAULTS_NOT_REVIEWED",
    }
    outcome_record = next(
        record for record in loaded.records if IssueProducer.OUTCOME in record.producers
    )
    assert outcome_record.outcome_bindings[0].index == 0


def test_snapshot_effective_issues_rejects_incomplete_outcome_coverage(
    project: Any,
    airports: Any,
    performance_repository: Any,
) -> None:
    outcome, _ = _snapshot_fixture(
        project,
        airports,
        performance_repository,
    )

    with pytest.raises(
        EffectiveIssueSnapshotError,
        match="not all outcome issues are bound",
    ):
        build_snapshot_effective_issues(
            [],
            outcome,
            calculated_against_fingerprint="a" * 64,
        )


@pytest.mark.parametrize("tamper", ("ack", "unknown", "outcome"))
def test_snapshot_effective_issues_fail_closed_on_tampering(
    project: Any,
    airports: Any,
    performance_repository: Any,
    tamper: str,
) -> None:
    outcome, envelope = _snapshot_fixture(
        project,
        airports,
        performance_repository,
    )
    raw = envelope.model_dump(mode="json")
    if tamper == "ack":
        raw["records"][0]["ack_key"] = "0" * 64
    elif tamper == "unknown":
        raw["records"][0]["unexpected"] = True
    else:
        outcome = outcome.model_copy(
            update={
                "issues": [outcome.issues[0].model_copy(update={"message": "tampered outcome"})]
            }
        )

    with pytest.raises(EffectiveIssueSnapshotError):
        load_snapshot_effective_issues(raw, outcome)
