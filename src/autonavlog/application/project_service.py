from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID

from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.planning import PersistedUiState, load_persisted_ui_state
from autonavlog.domain.project import Project
from autonavlog.storage.repository import ProjectRepository, ProjectSummary, SaveResult


class ProjectService:
    def __init__(
        self,
        repository: ProjectRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

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

    def delete_expired_projects(self) -> list[ProjectSummary]:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        active: list[ProjectSummary] = []
        for summary in self.repository.list_projects():
            try:
                project = self.repository.load(summary.id)
            except (FileNotFoundError, ValueError):
                continue
            if project.planned_departure_time_jst < now:
                try:
                    self.repository.delete(summary.id)
                except FileNotFoundError:
                    pass
            else:
                active.append(summary)
        return active

    def delete(self, project_id: UUID) -> None:
        self.repository.delete(project_id)

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
