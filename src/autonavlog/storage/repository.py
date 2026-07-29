from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from autonavlog.domain.project import Project
from autonavlog.domain.snapshot import CalculationSnapshot


class SaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: Project
    path: Path
    conflict_copy: Path | None = None


class ProjectRepository(Protocol):
    def load(self, project_id: UUID) -> Project: ...

    def save(self, project: Project, expected_revision: int) -> SaveResult: ...

    def autosave(self, project: Project) -> Path: ...

    def create_snapshot(self, snapshot: CalculationSnapshot) -> Path: ...

    def load_snapshot(self, project_id: UUID, snapshot_id: UUID) -> CalculationSnapshot: ...
