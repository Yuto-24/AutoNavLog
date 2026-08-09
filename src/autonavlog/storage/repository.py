from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from autonavlog.domain.enums import ProjectStatus
from autonavlog.domain.project import Project
from autonavlog.domain.snapshot import CalculationSnapshot


class ProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: UUID
    name: str
    updated_at: datetime
    status: ProjectStatus
    revision: int


class ProjectIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    projects: list[ProjectSummary]


class SaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: Project
    path: Path
    conflict_copy: Path | None = None


class ProjectRepository(Protocol):
    def list_projects(self) -> list[ProjectSummary]: ...

    def load(self, project_id: UUID) -> Project: ...

    def save(self, project: Project, expected_revision: int) -> SaveResult: ...

    def autosave(self, project: Project) -> Path: ...

    def create_snapshot(self, snapshot: CalculationSnapshot) -> Path: ...

    def load_snapshot(self, project_id: UUID, snapshot_id: UUID) -> CalculationSnapshot: ...
