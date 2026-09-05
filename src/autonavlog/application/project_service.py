from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.planning import PersistedUiState, load_persisted_ui_state
from autonavlog.domain.project import Project
from autonavlog.storage.repository import (
    LastCalculationRecord,
    ProjectLoadResult,
    ProjectRepository,
    ProjectSummary,
    SaveResult,
    owner_storage_key,
)
from autonavlog.weather.destination_taf import DestinationWindForecast


class ProjectService:
    def __init__(self, repository: ProjectRepository):
        self.repository = repository

    def create(
        self,
        *,
        name: str,
        flight_date: date,
        planned_departure_time_jst: datetime,
        departure_airport_id: str,
        destination_airport_id: str,
        total_usable_fuel_gal: float,
        default_variation_deg_east: float,
        pilot_name: str = "",
        ship_identifier: str = "",
    ) -> Project:
        project = Project(
            name=name,
            pilot_name=pilot_name,
            ship_identifier=ship_identifier,
            flight_date=flight_date,
            planned_departure_time_jst=planned_departure_time_jst,
            departure_airport_id=departure_airport_id,
            destination_airport_id=destination_airport_id,
            total_usable_fuel_gal=total_usable_fuel_gal,
            default_variation_deg_east=default_variation_deg_east,
        )

        project.metadata["ui_state"] = PersistedUiState().model_dump(mode="json")
        return project

    def list_projects(self) -> list[ProjectSummary]:
        return self.repository.list_projects()

    def delete(self, project_id: UUID) -> None:
        self.repository.delete(project_id)

    def delete_for_owner(self, project_id: UUID, owner_id: str) -> None:
        self.repository.delete_with_owner_marker(
            project_id,
            owner_key=self.owner_key(owner_id),
        )

    def autosave(self, project: Project) -> None:
        self.repository.autosave(self._normalize_ui_state(project))

    @staticmethod
    def owner_key(owner_id: str) -> str:
        return owner_storage_key(owner_id)

    def last_opened_project(self, owner_id: str) -> UUID | None:
        return self.repository.load_owner_project(self.owner_key(owner_id))

    def set_last_opened_project(self, owner_id: str, project_id: UUID) -> None:
        self.repository.set_owner_project(self.owner_key(owner_id), project_id)

    def clear_last_opened_project(
        self,
        owner_id: str,
        project_id: UUID | None = None,
    ) -> None:
        self.repository.clear_owner_project(self.owner_key(owner_id), project_id)

    def load_last_calculation(
        self,
        project_id: UUID,
        owner_id: str,
    ) -> LastCalculationRecord | None:
        return self.repository.load_last_calculation(
            project_id,
            owner_key=self.owner_key(owner_id),
        )

    def replace_last_calculation(
        self,
        *,
        owner_id: str,
        project: Project,
        outcome: CalculationOutcome,
        destination_wind: DestinationWindForecast | None,
        forecast_metadata: dict[str, Any],
        calculation_fingerprint: str,
    ) -> LastCalculationRecord:
        normalized = self._normalize_ui_state(project)
        record = LastCalculationRecord(
            project_id=normalized.id,
            owner_key=self.owner_key(owner_id),
            project=normalized.model_copy(deep=True),
            outcome=outcome.model_copy(deep=True),
            destination_wind=(
                None if destination_wind is None else destination_wind.model_copy(deep=True)
            ),
            selected_forecast_run_id=outcome.selected_forecast_run_id,
            forecast_metadata=dict(forecast_metadata),
            calculation_fingerprint=calculation_fingerprint,
            saved_at_utc=datetime.now(UTC),
        )
        self.repository.replace_last_calculation(record)
        return record

    @staticmethod
    def ui_state(project: Project) -> PersistedUiState:
        raw_state = project.metadata.get("ui_state")
        if raw_state is None:
            raise ValueError("ProjectにPersistedUiState v5がありません。")
        try:
            return load_persisted_ui_state(raw_state)
        except (TypeError, ValueError) as error:
            raise ValueError("ProjectのPersistedUiStateを安全に読み込めません。") from error

    @staticmethod
    def set_ui_state(
        project: Project,
        ui_state: PersistedUiState,
        *,
        reconfirmed: bool = False,
    ) -> None:
        project.metadata["ui_state"] = ui_state.model_dump(mode="json")
        if reconfirmed:
            project.metadata.pop("ui_state_reconfirmation_required", None)

    @classmethod
    def _normalize_ui_state(cls, project: Project) -> Project:
        normalized = project.model_copy(deep=True)
        raw_state = normalized.metadata.get("ui_state")
        if raw_state is None:
            normalized.metadata["ui_state"] = PersistedUiState().model_dump(mode="json")
            normalized.metadata["ui_state_reconfirmation_required"] = True
        else:
            try:
                ui_state = load_persisted_ui_state(raw_state)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "ProjectのPersistedUiStateを安全に読み込めません。"
                ) from error
            normalized.metadata["ui_state"] = ui_state.model_dump(mode="json")
        return normalized

    def load(self, project_id: UUID) -> Project:
        return self._normalize_ui_state(self.repository.load(project_id))

    def load_with_recovery(self, project_id: UUID) -> ProjectLoadResult:
        loaded = self.repository.load_with_recovery(project_id)
        return loaded.model_copy(
            update={"project": self._normalize_ui_state(loaded.project)}
        )

    def save(self, project: Project) -> SaveResult:
        normalized = self._normalize_ui_state(project)
        return self.repository.save(
            normalized,
            expected_revision=project.revision,
        )

    def apply_calculation_outcome(
        self,
        project: Project,
        outcome: CalculationOutcome,
    ) -> Project:
        if project.id != outcome.project_id:
            raise ValueError("calculation outcome belongs to a different project")
        return Project.model_validate(
            project.model_dump()
            | {
                "selected_forecast_run_id": outcome.selected_forecast_run_id,
                "status": outcome.status,
            },
        )
