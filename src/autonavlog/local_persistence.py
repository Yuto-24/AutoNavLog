"""Portable durable Project envelope; no session, owner, or storage driver state."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from autonavlog.domain.project import Project
from autonavlog.web.models import WorkingCalculation


class LocalProjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal[2]
    id: UUID
    token: UUID
    checkpoint: Project | None
    draft: Project
    lastCalculation: WorkingCalculation | None
    updatedAt: datetime

    @model_validator(mode="after")
    def validate_identity(self) -> LocalProjectRecord:
        if self.draft.id != self.id:
            raise ValueError("draft identity mismatch")
        if self.checkpoint is not None and (
            self.checkpoint.id != self.id or self.checkpoint.revision != self.draft.revision
        ):
            raise ValueError("checkpoint identity/revision mismatch")
        if self.lastCalculation is not None and self.lastCalculation.project.id != self.id:
            raise ValueError("calculation identity mismatch")
        for project in [
            self.draft,
            self.checkpoint,
            self.lastCalculation.project if self.lastCalculation else None,
        ]:
            if project is not None and "web_owner_id" in project.metadata:
                raise ValueError("local records must not contain server owner identity")
        return self


def migrate_record(payload: dict[str, Any]) -> LocalProjectRecord:
    # v1 has the same explicit data fields, but uses the draft's update timestamp.
    # Pure conversion; the IndexedDB transaction commits only after full validation.
    candidate = dict(payload)
    if candidate.get("schemaVersion") == 1:
        candidate["schemaVersion"] = 2
        candidate["updatedAt"] = candidate["draft"]["updated_at"]
    return LocalProjectRecord.model_validate(candidate)
