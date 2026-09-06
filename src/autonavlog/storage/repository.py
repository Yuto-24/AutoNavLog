from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.enums import ProjectStatus
from autonavlog.domain.project import Project
from autonavlog.weather.destination_taf import DestinationWindForecast

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_OWNER_KEY_DOMAIN = b"AutoNavLog OwnerProjectState v1\0"


def owner_storage_key(owner_id: str) -> str:
    if not owner_id:
        raise ValueError("owner identity must not be empty")
    return hashlib.sha256(_OWNER_KEY_DOMAIN + owner_id.encode("utf-8")).hexdigest()


class ProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: UUID
    name: str
    updated_at: datetime
    status: ProjectStatus
    revision: int
    web_owner_id: str | None = None
    kind: Literal["LATEST", "SAVED"] = "SAVED"


class ProjectIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[2] = 2
    projects: list[ProjectSummary]


class SaveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: Project
    path: Path
    conflict_copy: Path | None = None


class ProjectLoadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: Project
    recovered_from_fallback: bool = False


class LastCalculationRecord(BaseModel):
    """One self-contained last-good calculation for a Project."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    project_id: UUID
    owner_key: str
    project: Project
    outcome: CalculationOutcome
    destination_wind: DestinationWindForecast | None = None
    selected_forecast_run_id: str | None = None
    forecast_metadata: dict[str, Any] = Field(default_factory=dict)
    calculation_fingerprint: str
    saved_at_utc: datetime

    @model_validator(mode="after")
    def validate_record_identity(self) -> LastCalculationRecord:
        if not _SHA256_PATTERN.fullmatch(self.owner_key):
            raise ValueError("owner key must be a lowercase SHA-256 digest")
        if not _SHA256_PATTERN.fullmatch(self.calculation_fingerprint):
            raise ValueError("calculation fingerprint must be a lowercase SHA-256 digest")
        if self.saved_at_utc.tzinfo is None:
            raise ValueError("saved_at_utc must be timezone-aware")
        self.saved_at_utc = self.saved_at_utc.astimezone(UTC)
        if self.project_id != self.project.id or self.project_id != self.outcome.project_id:
            raise ValueError("last calculation Project identities do not match")
        stored_owner = self.project.metadata.get("web_owner_id")
        if not isinstance(stored_owner, str) or owner_storage_key(stored_owner) != self.owner_key:
            raise ValueError("last calculation owner does not match the Project snapshot")
        if self.selected_forecast_run_id != self.outcome.selected_forecast_run_id:
            raise ValueError("last calculation forecast run does not match the outcome")
        if self.project.selected_forecast_run_id != self.selected_forecast_run_id:
            raise ValueError("last calculation forecast run does not match the Project snapshot")
        raw_ui_state = self.project.metadata.get("ui_state")
        if not isinstance(raw_ui_state, dict):
            raise ValueError("last calculation Project has no persisted UI state")
        if raw_ui_state.get("calculated_against_fingerprint") != self.calculation_fingerprint:
            raise ValueError("last calculation fingerprint does not match the Project snapshot")
        inbound = self.outcome.rjfm_inbound_guidance
        if (
            inbound is not None
            and inbound.generated_against_fingerprint != self.calculation_fingerprint
        ):
            raise ValueError("inbound guidance fingerprint does not match the calculation")
        return self


class OwnerProjectState(BaseModel):
    """Opaque-owner state; raw identity is never persisted in its path or payload."""

    model_config = ConfigDict(extra="forbid", strict=True)

    # v1 contained a single ``project_id``.  It is accepted only to allow a
    # safe, lazy migration by the local repository; new writes are v2.
    schema_version: Literal[1, 2] = 2
    last_opened_project_id: UUID | None = None
    latest_draft_project_id: UUID | None = None
    pending_cleanup_project_ids: list[UUID] = Field(default_factory=list)
    project_id: UUID | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_state(self) -> OwnerProjectState:
        if self.schema_version == 1 and self.project_id is None:
            raise ValueError("v1 owner state requires project_id")
        if self.schema_version == 2 and self.project_id is not None:
            raise ValueError("v2 owner state must not contain project_id")
        if len(set(self.pending_cleanup_project_ids)) != len(self.pending_cleanup_project_ids):
            raise ValueError("owner cleanup Project ids must be unique")
        return self


class ProjectRepository(Protocol):
    def list_projects(self) -> list[ProjectSummary]: ...

    def load(self, project_id: UUID) -> Project: ...

    def load_with_recovery(self, project_id: UUID) -> ProjectLoadResult: ...

    def save(self, project: Project, expected_revision: int) -> SaveResult: ...

    def delete(self, project_id: UUID) -> None: ...

    def delete_with_owner_marker(
        self,
        project_id: UUID,
        *,
        owner_key: str,
    ) -> None: ...

    def autosave(self, project: Project, *, set_last_opened: bool = False) -> Path: ...

    def replace_last_calculation(self, record: LastCalculationRecord) -> Path: ...

    def load_last_calculation(
        self,
        project_id: UUID,
        *,
        owner_key: str,
    ) -> LastCalculationRecord | None: ...

    def load_owner_project(self, owner_key: str) -> UUID | None: ...

    def load_owner_state(self, owner_key: str) -> OwnerProjectState | None: ...

    def set_owner_project(self, owner_key: str, project_id: UUID) -> Path: ...

    def clear_owner_project(
        self,
        owner_key: str,
        project_id: UUID | None = None,
    ) -> None: ...
