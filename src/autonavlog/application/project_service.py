from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from autonavlog.domain.calculation import CalculationOutcome, Issue
from autonavlog.domain.enums import IssueSeverity
from autonavlog.domain.planning import PersistedUiState, load_persisted_ui_state
from autonavlog.domain.project import Project
from autonavlog.domain.snapshot import CalculationSnapshot
from autonavlog.storage.repository import ProjectRepository, ProjectSummary, SaveResult
from autonavlog.version import __version__

from .calculation_service import CalculationService
from .effective_issue_snapshot import (
    EffectiveIssueSnapshotError,
    build_snapshot_effective_issues,
    load_snapshot_effective_issues,
)
from .readiness import (
    EffectiveIssue,
    IssueProducer,
    create_effective_issue,
    derive_project_status,
    outcome_effective_issues,
)


class ProjectService:
    def __init__(
        self,
        repository: ProjectRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

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
            raise ValueError("ProjectにPersistedUiState v4がありません。")
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
        if "snapshot_effective_issues" in project.metadata:
            raise ValueError("Snapshot読取専用Projectは編集保存できません。")
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

    def snapshot(
        self,
        project: Project,
        outcome: CalculationOutcome,
        calculation_service: CalculationService,
        *,
        msm_package_version: str | None,
        effective_issues: Iterable[EffectiveIssue] | None = None,
    ) -> Path:
        input_data = project.model_copy(deep=True)
        try:
            ui_state = self.ui_state(project)
        except ValueError as error:
            raise ValueError(
                "PROJECT_STATE_INVALID: 保存済みUI状態を読み込めないProjectから"
                "Snapshotを作成できません。"
            ) from error
        calculated_against = ui_state.calculated_against_fingerprint
        effective = list(
            outcome_effective_issues(
                outcome,
                calculated_against_fingerprint=calculated_against,
            )
            if effective_issues is None
            else effective_issues
        )
        status = derive_project_status(
            effective,
            project.acknowledged_warning_codes,
            outcome_exists=True,
        )
        envelope = build_snapshot_effective_issues(
            effective,
            outcome,
            calculated_against_fingerprint=calculated_against,
        )
        input_data.metadata["snapshot_effective_issues"] = envelope.model_dump(mode="json")
        input_data.status = status
        snapshot_outcome = outcome.model_copy(
            deep=True,
            update={"status": status},
        )
        snapshot = CalculationSnapshot(
            project_id=project.id,
            project_revision=project.revision,
            input_data=input_data,
            performance_table_version=outcome.performance_table_version,
            calculation_policy_version=outcome.policy_version,
            calculation_results=snapshot_outcome,
            selected_forecast_run_id=outcome.selected_forecast_run_id,
            forecast_metadata=calculation_service.last_forecast_metadata,
            weather_requests=calculation_service.last_weather_requests,
            weather_results=calculation_service.last_weather_results,
            autonavlog_version=__version__,
            msm_package_version=msm_package_version,
            warnings=[
                item.issue.model_copy(
                    deep=True,
                    update={
                        "severity": item.effective_severity,
                        "acknowledgement_required": (item.effective_acknowledgement_required),
                    },
                )
                for item in effective
                if item.effective_severity == IssueSeverity.WARNING
            ],
            readiness_status=status,
        )
        return self.repository.create_snapshot(snapshot)

    def load_snapshot(
        self,
        project_id: UUID,
        snapshot_id: UUID,
    ) -> CalculationSnapshot:
        snapshot = self.repository.load_snapshot(project_id, snapshot_id)
        raw_envelope = snapshot.input_data.metadata.get("snapshot_effective_issues")
        try:
            if raw_envelope is None:
                raise EffectiveIssueSnapshotError("snapshot effective issue envelope is missing")
            _, effective = load_snapshot_effective_issues(
                raw_envelope,
                snapshot.calculation_results,
            )
        except EffectiveIssueSnapshotError as error:
            invalid = Issue(
                code="PROJECT_STATE_INVALID",
                severity=IssueSeverity.BLOCKER,
                message="Snapshotの安全状態を検証できません。",
                metadata={"reason": str(error)},
            )
            effective = (
                create_effective_issue(
                    invalid,
                    producer=IssueProducer.PROJECT_VALIDATION,
                    cause={"reason": str(error)},
                ),
            )
            outcome = snapshot.calculation_results.model_copy(
                deep=True,
                update={
                    "issues": [
                        *snapshot.calculation_results.issues,
                        invalid,
                    ]
                },
            )
        else:
            outcome = snapshot.calculation_results
        status = derive_project_status(
            effective,
            snapshot.input_data.acknowledged_warning_codes,
            outcome_exists=True,
        )
        input_data = snapshot.input_data.model_copy(
            deep=True,
            update={"status": status},
        )
        outcome = outcome.model_copy(
            deep=True,
            update={"status": status},
        )
        return snapshot.model_copy(
            deep=True,
            update={
                "input_data": input_data,
                "calculation_results": outcome,
                "readiness_status": status,
            },
        )
