from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from .enums import (
    DerivedPointType,
    DisplayCellState,
    FlightPhase,
    IssueSeverity,
    PressureAltitudeDisplayKind,
    ProjectStatus,
)
from .planning import ArrivalAltitudeResult, CheckPointProjection, RjfmDepartureGuidance
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


class SectionResult(CalculationModel):
    section_id: UUID
    sequence: int = Field(ge=0)
    phase: FlightPhase
    from_name: str
    to_name: str
    from_node_id: UUID | None = None
    to_node_id: UUID | None = None
    from_latitude_deg: float | None = Field(default=None, ge=-90, le=90)
    from_longitude_deg: float | None = Field(default=None, ge=-180, le=180)
    to_latitude_deg: float | None = Field(default=None, ge=-90, le=90)
    to_longitude_deg: float | None = Field(default=None, ge=-180, le=180)
    planned_altitude_ft_msl: AdoptedValue[float]
    pressure_altitude_exact_ft: AdoptedValue[float]
    pressure_altitude_planning_ft: AdoptedValue[float]
    true_course_deg: AdoptedValue[float] = Field(
        description="True course in degrees, normalized to [0, 360).",
    )
    variation_deg_east: AdoptedValue[float] = Field(
        description="Magnetic variation in degrees with east variation positive.",
    )
    magnetic_course_deg: AdoptedValue[float] = Field(
        description="Magnetic course: MC = (TC + VAR) mod 360.",
    )
    wind_direction_deg_from: AdoptedValue[float]
    wind_speed_kt: AdoptedValue[float]
    wca_deg: AdoptedValue[float] = Field(
        description="Wind correction angle in degrees with right correction positive.",
    )
    magnetic_heading_deg: AdoptedValue[float] = Field(
        description="Magnetic heading: MH = (MC + WCA) mod 360.",
    )
    temperature_c: AdoptedValue[float]
    cas_kt: AdoptedValue[float]
    tas_kt: AdoptedValue[float]
    ground_speed_kt: AdoptedValue[float]
    zone_distance_nm: AdoptedValue[float]
    cumulative_distance_nm: AdoptedValue[float]
    zone_ete_seconds: AdoptedValue[float]
    cumulative_ete_seconds: AdoptedValue[float]
    section_fuel_gal: AdoptedValue[float]
    remaining_fuel_gal: AdoptedValue[float]
    performance_metadata: dict[str, Any] = Field(default_factory=dict)


class NavLogDisplayCell(CalculationModel):
    """A formatted display cell with explicit blank/error semantics.

    ``text`` is the canonical value rendered by the Web NAV LOG.
    ``effective_value`` may remain populated when ``state`` is ``INHERIT`` so
    the projection records which parent/preceding value is used without
    repeating it visually.
    """

    state: DisplayCellState = Field(
        description=(
            "DISPLAY_VALUE shows text; INHERIT and BLANK render empty; "
            "UNAVAILABLE renders a true acquisition/calculation failure; "
            "STATE_SYMBOL renders a presentation symbol such as an arrow."
        )
    )
    text: str | None = Field(
        default=None,
        description="Canonical formatted text rendered by the Web NAV LOG.",
    )
    effective_value: float | str | None = Field(
        default=None,
        description="Effective raw/canonical value, including values hidden by INHERIT.",
    )
    reason_code: str | None = Field(
        default=None,
        description="Failure reason used only when state is UNAVAILABLE.",
    )
    manual: bool = Field(
        default=False,
        description="Whether the effective calculation value came from a manual override.",
    )

    @model_validator(mode="after")
    def validate_state_payload(self) -> NavLogDisplayCell:
        if self.state in {DisplayCellState.INHERIT, DisplayCellState.BLANK}:
            if self.text is not None:
                raise ValueError("INHERIT and BLANK display cells must not contain text")
        elif self.state == DisplayCellState.UNAVAILABLE:
            if self.text != "未取得" or not self.reason_code:
                raise ValueError("UNAVAILABLE display cells require 未取得 and a reason code")
        elif not (self.text or "").strip():
            raise ValueError("visible display cells require non-empty text")
        return self


class NavLogDisplayRow(CalculationModel):
    """Presentation-only NAV LOG row projected from calculation zones.

    ``CalculationOutcome.sections`` remains the sole non-overlapping source of
    truth for distance, time, and fuel.  These rows may contain display-only
    subtotals, inheritance, symbols, separators, and destination reference
    information and must never be used to recompute route totals.
    """

    section_id: UUID | None = Field(
        default=None,
        description="Physical source Leg identifier; null for a pure separator row.",
    )
    wind_source_section_id: UUID | None = Field(
        default=None,
        description=(
            "Project Section that owns an editable wind value. This may differ "
            "from section_id when EOC backtracks into a preceding physical Leg."
        ),
    )
    sequence: int = Field(ge=0)
    source_result_sequence: int | None = Field(
        default=None,
        ge=0,
        description="Source Calculation Zone sequence used for editing/provenance only.",
    )
    phase: FlightPhase | None = None
    row_type: Literal[
        "PHYSICAL_LEG_SUMMARY",
        "CALCULATION_ZONE",
        "DESTINATION_INFO",
        "LEG_SEPARATOR",
    ] = Field(
        description=(
            "Presentation row kind: Physical Leg subtotal, Calculation Zone, "
            "destination reference information, or visual Leg separator."
        )
    )
    from_name: str = ""
    to_name: str = ""
    from_node_id: UUID | None = None
    to_node_id: UUID | None = None
    from_latitude_deg: float | None = Field(default=None, ge=-90, le=90)
    from_longitude_deg: float | None = Field(default=None, ge=-180, le=180)
    to_latitude_deg: float | None = Field(default=None, ge=-90, le=90)
    to_longitude_deg: float | None = Field(default=None, ge=-180, le=180)
    pa_display_kind: PressureAltitudeDisplayKind = PressureAltitudeDisplayKind.BLANK
    pa: NavLogDisplayCell
    toat: NavLogDisplayCell
    cas: NavLogDisplayCell
    tas: NavLogDisplayCell
    tc: NavLogDisplayCell
    variation: NavLogDisplayCell
    mc: NavLogDisplayCell
    wind: NavLogDisplayCell
    wca: NavLogDisplayCell
    mh: NavLogDisplayCell = Field(
        description=(
            "Magnetic heading display cell. The calculation effective value is "
            "MC + WCA; display text adds the separately rounded 1-degree "
            "MC and WCA display operands."
        )
    )
    distance: NavLogDisplayCell
    gs: NavLogDisplayCell
    ete: NavLogDisplayCell
    eto: NavLogDisplayCell
    ato: NavLogDisplayCell
    ate: NavLogDisplayCell
    fuel: NavLogDisplayCell


class RjfmInboundGuidancePoint(CalculationModel):
    latitude_deg: FiniteFloat = Field(ge=-90, le=90)
    longitude_deg: FiniteFloat = Field(ge=-180, le=180)


class RjfmInboundGuidance(CalculationModel):
    """West-extension diagnostic persisted only inside the last-good calculation."""

    status: Literal[
        "AVAILABLE",
        "WARNING",
        "UNAVAILABLE",
        "ADVERSE_WIND",
        "NO_SOLUTION",
        "CONVERGENCE_FAILURE",
    ] = "UNAVAILABLE"
    message: str
    reason_code: str | None = None
    generated_against_fingerprint: str | None = None
    reference_revision: str | None = None
    reference_content_fingerprint: str | None = None
    raw_turn_point: RjfmInboundGuidancePoint | None = None
    rounded_turn_point: RjfmInboundGuidancePoint | None = None
    bearing_magnetic_deg: FiniteFloat | None = None
    actual_bearing_magnetic_deg: FiniteFloat | None = None
    raw_extra_distance_nm: FiniteFloat | None = Field(default=None, ge=0)
    extra_distance_nm: FiniteFloat | None = Field(default=None, ge=0)
    raw_predicted_ete_min: FiniteFloat | None = Field(default=None, ge=0)
    predicted_ete_min: FiniteFloat | None = Field(default=None, ge=0)
    raw_dme_nm: FiniteFloat | None = Field(default=None, ge=0)
    rounded_dme_nm: FiniteFloat | None = Field(default=None, ge=0)
    raw_turn_altitude_ft_msl: FiniteFloat | None = Field(default=None, ge=0)
    rounded_turn_altitude_ft_msl: FiniteFloat | None = Field(default=None, ge=0)
    raw_minimum_boundary_clearance_nm: FiniteFloat | None = Field(default=None, ge=0)
    minimum_boundary_clearance_nm: FiniteFloat | None = Field(default=None, ge=0)


class FuelPlan(CalculationModel):
    total_usable_gal: float
    taxi_runup_minutes: int = 10
    taxi_runup_gal: float = 1.5
    climb_gal: float | None = None
    cruise_gal: float | None = None
    descent_gal: float | None = None
    additional_gal: float = 2.8
    tgl_gal: float = 0.0
    reserve_gal: float = 12.4
    bof_gal: float | None = None
    min_required_gal: float | None = None
    extra_gal: float | None = None
    extra_endurance_seconds: float | None = None


class CalculationOutcome(CalculationModel):
    project_id: UUID
    selected_forecast_run_id: str | None
    sections: list[SectionResult] = Field(
        default_factory=list,
        description=(
            "Non-overlapping Calculation Zones and the sole source of truth for "
            "distance, time, and fuel totals."
        ),
    )
    display_rows: list[NavLogDisplayRow] = Field(
        default_factory=list,
        description=(
            "Presentation-only projection containing Physical Leg subtotals, "
            "inheritance, blank cells, symbols, destination information, and separators."
        ),
    )
    derived_points: list[DerivedRoutePoint] = Field(default_factory=list)
    arrival_altitude: ArrivalAltitudeResult | None = None
    check_point_projections: list[CheckPointProjection] = Field(default_factory=list)
    rjfm_departure_guidance: RjfmDepartureGuidance | None = None
    rjfm_inbound_guidance: RjfmInboundGuidance | None = None
    fuel_plan: FuelPlan
    issues: list[Issue] = Field(default_factory=list)
    converged: bool = False
    status: ProjectStatus
    policy_version: str
    performance_table_version: str | None = None

    @property
    def blockers(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == IssueSeverity.BLOCKER]
