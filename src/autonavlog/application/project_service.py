from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.project import Project
from autonavlog.domain.snapshot import CalculationSnapshot
from autonavlog.storage.repository import ProjectRepository, SaveResult
from autonavlog.version import __version__

from .calculation_service import CalculationService


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
        return Project(
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

    def save(self, project: Project) -> SaveResult:
        return self.repository.save(project, expected_revision=project.revision)

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
    ) -> Path:
        snapshot = CalculationSnapshot(
            project_id=project.id,
            project_revision=project.revision,
            input_data=project.model_copy(deep=True),
            performance_table_version=outcome.performance_table_version,
            calculation_policy_version=outcome.policy_version,
            calculation_results=outcome,
            selected_forecast_run_id=outcome.selected_forecast_run_id,
            forecast_metadata=calculation_service.last_forecast_metadata,
            weather_requests=calculation_service.last_weather_requests,
            weather_results=calculation_service.last_weather_results,
            autonavlog_version=__version__,
            msm_package_version=msm_package_version,
            warnings=[
                issue
                for issue in outcome.issues
                if issue.severity.value == "WARNING"
            ],
            readiness_status=outcome.status,
        )
        return self.repository.create_snapshot(snapshot)
