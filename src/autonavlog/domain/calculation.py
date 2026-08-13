from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .enums import DerivedPointType, FlightPhase, IssueSeverity, ProjectStatus
from .planning import ArrivalAltitudeResult, CheckPointProjection
from .values import AdoptedValue


class CalculationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Issue(CalculationModel):
    code: str
    severity: IssueSeverity
    message: str
    section_id: UUID | None = None
    segment_sequence: int | None = Field(default=None, ge=0)
    acknowledgement_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class DerivedRoutePoint(CalculationModel):
    type: DerivedPointType
    section_id: UUID
    latitude_deg: float
    longitude_deg: float
    along_route_distance_nm: float
    estimated_time_utc: datetime | None = None


class SectionResult(CalculationModel):
    section_id: UUID
    sequence: int = Field(ge=0)
    phase: FlightPhase
    segment_label: str | None = None
    from_name: str
    to_name: str
    planned_altitude_ft_msl: AdoptedValue[float]
    safe_enroute_altitude_ft_msl: AdoptedValue[float]
    loss_time_seconds: float = Field(ge=0)
    pressure_altitude_exact_ft: AdoptedValue[float]
    pressure_altitude_planning_ft: AdoptedValue[float]
    true_course_deg: AdoptedValue[float]
    variation_deg_east: AdoptedValue[float]
    magnetic_course_deg: AdoptedValue[float]
    wind_direction_deg_from: AdoptedValue[float]
    wind_speed_kt: AdoptedValue[float]
    wca_deg: AdoptedValue[float]
    magnetic_heading_deg: AdoptedValue[float]
    temperature_c: AdoptedValue[float]
    cas_kt: AdoptedValue[float]
    tas_kt: AdoptedValue[float]
    ground_speed_kt: AdoptedValue[float]
    zone_distance_nm: AdoptedValue[float]
    cumulative_distance_nm: AdoptedValue[float]
    zone_ete_seconds: AdoptedValue[float]
    cumulative_ete_seconds: AdoptedValue[float]
    eto_utc: AdoptedValue[datetime]
    section_fuel_gal: AdoptedValue[float]
    remaining_fuel_gal: AdoptedValue[float]
    performance_metadata: dict[str, Any] = Field(default_factory=dict)


class NavLogDisplayRow(SectionResult):
    """One rendered NAV LOG row, projected from calculation zones.

    ``sections`` remain the non-overlapping calculation source of truth.  A
    split physical-leg summary therefore never participates in route, time, or
    fuel accumulation even though it carries the displayed subtotals.  An
    unsplit summary is also the sole calculation-zone display row.
    """

    row_type: Literal["PHYSICAL_LEG_SUMMARY", "CALCULATION_ZONE"]
    counts_toward_totals: bool = False


class FuelPlan(CalculationModel):
    total_usable_gal: float
    taxi_runup_gal: float = 1.5
    climb_gal: float | None = None
    cruise_gal: float | None = None
    descent_gal: float | None = None
    additional_gal: float = 2.8
    tgl_gal: float = 0.0
    reserve_gal: float = 12.4
    min_required_gal: float | None = None
    extra_gal: float | None = None
    extra_endurance_seconds: float | None = None


class IterationRecord(CalculationModel):
    iteration: int
    max_time_delta_seconds: float | None
    representative_times_utc: dict[str, datetime]


class CalculationOutcome(CalculationModel):
    project_id: UUID
    selected_forecast_run_id: str | None
    qnh_hpa: AdoptedValue[float]
    sections: list[SectionResult] = Field(default_factory=list)
    display_rows: list[NavLogDisplayRow] = Field(default_factory=list)
    derived_points: list[DerivedRoutePoint] = Field(default_factory=list)
    arrival_altitude: ArrivalAltitudeResult | None = None
    check_point_projections: list[CheckPointProjection] = Field(default_factory=list)
    fuel_plan: FuelPlan
    issues: list[Issue] = Field(default_factory=list)
    iterations: list[IterationRecord] = Field(default_factory=list)
    converged: bool = False
    status: ProjectStatus
    policy_version: str
    performance_table_version: str | None = None

    @property
    def blockers(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == IssueSeverity.BLOCKER]
