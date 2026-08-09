from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autonavlog.domain.calculation import CalculationOutcome, Issue
from autonavlog.domain.enums import IssueSeverity
from autonavlog.domain.planning import Sha256Hex

from .fingerprints import make_fingerprint
from .readiness import (
    EffectiveIssue,
    IssueProducer,
    create_effective_issue,
    dedupe_effective_issues,
    outcome_effective_issues,
)


class EffectiveIssueSnapshotError(ValueError):
    pass


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )


class OutcomeIssueBinding(_SnapshotModel):
    index: int = Field(ge=0)
    issue_fingerprint: Sha256Hex


class EffectiveIssueRecord(_SnapshotModel):
    issue: Issue
    canonical_cause_json: str = Field(min_length=1)
    cause_fingerprint: Sha256Hex
    ack_key: Sha256Hex
    effective_severity: IssueSeverity
    effective_acknowledgement_required: bool
    producers: tuple[IssueProducer, ...] = Field(min_length=1)
    outcome_bindings: tuple[OutcomeIssueBinding, ...] = ()

    @field_validator("producers")
    @classmethod
    def validate_producers(
        cls,
        value: tuple[IssueProducer, ...],
    ) -> tuple[IssueProducer, ...]:
        if tuple(sorted(set(value), key=lambda item: item.value)) != value:
            raise ValueError("producers must be sorted and unique")
        return value

    @field_validator("outcome_bindings")
    @classmethod
    def validate_bindings(
        cls,
        value: tuple[OutcomeIssueBinding, ...],
    ) -> tuple[OutcomeIssueBinding, ...]:
        indexes = [item.index for item in value]
        if indexes != sorted(set(indexes)):
            raise ValueError("outcome bindings must be sorted and unique")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> EffectiveIssueRecord:
        try:
            payload = json.loads(
                self.canonical_cause_json,
                object_pairs_hook=_pairs_without_duplicates,
                parse_constant=_reject_constant,
            )
        except (json.JSONDecodeError, EffectiveIssueSnapshotError) as error:
            raise ValueError("canonical cause JSON is invalid") from error
        if not isinstance(payload, dict):
            raise ValueError("canonical cause must be an object")
        if payload.get("kind") != "issue_cause":
            raise ValueError("canonical cause kind is invalid")
        fields = payload.get("fields")
        if not isinstance(fields, dict) or "cause" not in fields:
            raise ValueError("canonical cause fields are invalid")
        producer = self.producers[0]
        rebuilt = create_effective_issue(
            self.issue,
            producer=producer,
            cause=fields["cause"],
        )
        if rebuilt.ctx.canonical_cause_json != self.canonical_cause_json:
            raise ValueError("canonical cause JSON does not match the issue")
        if rebuilt.ctx.cause_fingerprint != self.cause_fingerprint:
            raise ValueError("cause fingerprint mismatch")
        if rebuilt.ctx.ack_key != self.ack_key:
            raise ValueError("ack key mismatch")
        if self.issue.severity != self.effective_severity:
            raise ValueError("display and effective severity differ")
        if self.issue.acknowledgement_required != self.effective_acknowledgement_required:
            raise ValueError("display and effective acknowledgement differ")
        has_outcome = IssueProducer.OUTCOME in self.producers
        if has_outcome != bool(self.outcome_bindings):
            raise ValueError("OUTCOME producer and bindings differ")
        return self


class SnapshotEffectiveIssuesEnvelope(_SnapshotModel):
    schema_version: Literal[1] = 1
    created_at_utc: datetime
    calculated_against_fingerprint: Sha256Hex | None = None
    outcome_issues_fingerprint: Sha256Hex
    records: tuple[EffectiveIssueRecord, ...]
    records_fingerprint: Sha256Hex

    @field_validator("created_at_utc")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at_utc must be UTC")
        return value

    @model_validator(mode="after")
    def validate_records_fingerprint(self) -> SnapshotEffectiveIssuesEnvelope:
        expected = _records_fingerprint(self.records)
        if self.records_fingerprint != expected:
            raise ValueError("effective issue records fingerprint mismatch")
        return self


def _reject_constant(value: str) -> Any:
    raise EffectiveIssueSnapshotError(f"non-finite JSON constant is not allowed: {value}")


def _pairs_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise EffectiveIssueSnapshotError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _issue_fingerprint(issue: Issue) -> str:
    return make_fingerprint(
        kind="outcome_issue",
        fields={"issue": issue.model_dump(mode="python")},
    )


def _outcome_issues_fingerprint(issues: list[Issue]) -> str:
    return make_fingerprint(
        kind="outcome_issues",
        fields={
            "issues": [
                {
                    "index": index,
                    "fingerprint": _issue_fingerprint(issue),
                }
                for index, issue in enumerate(issues)
            ]
        },
    )


def _records_fingerprint(
    records: tuple[EffectiveIssueRecord, ...],
) -> str:
    return make_fingerprint(
        kind="snapshot_effective_issue_records",
        fields={"records": [record.model_dump(mode="python") for record in records]},
    )


def build_snapshot_effective_issues(
    effective_issues: tuple[EffectiveIssue, ...] | list[EffectiveIssue],
    outcome: CalculationOutcome,
    *,
    calculated_against_fingerprint: str | None,
    created_at_utc: datetime | None = None,
) -> SnapshotEffectiveIssuesEnvelope:
    effective = dedupe_effective_issues(effective_issues)
    outcome_effective = outcome_effective_issues(
        outcome,
        calculated_against_fingerprint=calculated_against_fingerprint,
    )
    records: list[EffectiveIssueRecord] = []
    for item in effective:
        bindings = tuple(
            OutcomeIssueBinding(
                index=index,
                issue_fingerprint=_issue_fingerprint(outcome.issues[index]),
            )
            for index, produced in enumerate(outcome_effective)
            if produced.ctx.cause_fingerprint == item.ctx.cause_fingerprint
        )
        display = item.issue.model_copy(
            deep=True,
            update={
                "severity": item.effective_severity,
                "acknowledgement_required": (item.effective_acknowledgement_required),
            },
        )
        records.append(
            EffectiveIssueRecord(
                issue=display,
                canonical_cause_json=item.ctx.canonical_cause_json,
                cause_fingerprint=item.ctx.cause_fingerprint,
                ack_key=item.ctx.ack_key,
                effective_severity=item.effective_severity,
                effective_acknowledgement_required=(item.effective_acknowledgement_required),
                producers=tuple(sorted(item.producers, key=lambda producer: producer.value)),
                outcome_bindings=bindings,
            )
        )
    record_tuple = tuple(records)
    bound = {
        binding.index
        for record in record_tuple
        for binding in record.outcome_bindings
    }
    if bound != set(range(len(outcome.issues))):
        raise EffectiveIssueSnapshotError("not all outcome issues are bound")
    return SnapshotEffectiveIssuesEnvelope(
        created_at_utc=created_at_utc or datetime.now(timezone.utc),
        calculated_against_fingerprint=calculated_against_fingerprint,
        outcome_issues_fingerprint=_outcome_issues_fingerprint(outcome.issues),
        records=record_tuple,
        records_fingerprint=_records_fingerprint(record_tuple),
    )


def load_snapshot_effective_issues(
    raw: Any,
    outcome: CalculationOutcome,
) -> tuple[SnapshotEffectiveIssuesEnvelope, tuple[EffectiveIssue, ...]]:
    try:
        payload = json.dumps(
            raw,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        envelope = SnapshotEffectiveIssuesEnvelope.model_validate_json(
            payload,
            strict=True,
        )
    except (TypeError, ValueError) as error:
        raise EffectiveIssueSnapshotError("snapshot effective issue envelope is invalid") from error
    if envelope.outcome_issues_fingerprint != _outcome_issues_fingerprint(outcome.issues):
        raise EffectiveIssueSnapshotError("outcome issue set binding mismatch")
    bound: set[int] = set()
    rebuilt: list[EffectiveIssue] = []
    expected_outcome = outcome_effective_issues(
        outcome,
        calculated_against_fingerprint=(envelope.calculated_against_fingerprint),
    )
    for record in envelope.records:
        try:
            cause_payload = json.loads(record.canonical_cause_json)
            cause = cause_payload["fields"]["cause"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise EffectiveIssueSnapshotError("record cause cannot be reconstructed") from error
        for binding in record.outcome_bindings:
            if binding.index >= len(outcome.issues):
                raise EffectiveIssueSnapshotError("outcome issue index is out of range")
            if binding.index in bound:
                raise EffectiveIssueSnapshotError("outcome issue is bound more than once")
            if binding.issue_fingerprint != _issue_fingerprint(outcome.issues[binding.index]):
                raise EffectiveIssueSnapshotError("outcome issue hash mismatch")
            if expected_outcome[binding.index].ctx.cause_fingerprint != record.cause_fingerprint:
                raise EffectiveIssueSnapshotError("outcome issue cause binding mismatch")
            bound.add(binding.index)
        produced = [
            create_effective_issue(
                record.issue,
                producer=producer,
                cause=cause,
            )
            for producer in record.producers
        ]
        merged = dedupe_effective_issues(produced)
        if len(merged) != 1:
            raise EffectiveIssueSnapshotError("record reconstruction failed")
        rebuilt.append(merged[0])
    if bound != set(range(len(outcome.issues))):
        raise EffectiveIssueSnapshotError("not all outcome issues are bound")
    return envelope, tuple(dedupe_effective_issues(rebuilt))
