from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    eto_utc: AdoptedValue[datetime]
    section_fuel_gal: AdoptedValue[float]
    remaining_fuel_gal: AdoptedValue[float]
    performance_metadata: dict[str, Any] = Field(default_factory=dict)


class NavLogDisplayCell(CalculationModel):
    """A formatted display cell with explicit blank/error semantics.

    ``text`` is the canonical value rendered by both Web and A4 output.
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
        description="Canonical formatted text shared by Web and A4 renderers.",
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
    counts_toward_totals: bool = Field(
        default=False,
        description=(
            "Legacy projection metadata only. Route totals must always come from "
            "CalculationOutcome.sections, never from display_rows."
        ),
    )
    from_name: str = ""
    to_name: str = ""
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
            "MC + WCA; transcription text adds the separately rounded 1-degree "
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
    zone_distance_nm_exact: float | None = Field(
        default=None,
        ge=0,
        description="Unrounded display-row zone/subtotal distance; never a totals source.",
    )
    cumulative_distance_nm_exact: float | None = Field(default=None, ge=0)
    zone_ete_seconds_exact: float | None = Field(
        default=None,
        ge=0,
        description="Unrounded display-row zone/subtotal ETE; never a totals source.",
    )
    cumulative_ete_seconds_exact: float | None = Field(default=None, ge=0)


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
    fuel_plan: FuelPlan
    issues: list[Issue] = Field(default_factory=list)
    iterations: list[IterationRecord] = Field(default_factory=list)
    converged: bool = False
    status: ProjectStatus
    policy_version: str
    performance_table_version: str | None = None

    @field_validator("display_rows", mode="before")
    @classmethod
    def discard_legacy_display_projection(cls, value: Any) -> Any:
        """Accept snapshots created before display cells became a separate DTO.

        Legacy rows inherited ``SectionResult`` and are intentionally discarded:
        saved display projections are not authoritative and callers must render
        a fresh projection from Project inputs and the current calculation policy.
        """

        if isinstance(value, list) and any(
            isinstance(row, dict) and "pa" not in row for row in value
        ):
            return []
        return value

    @property
    def blockers(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == IssueSeverity.BLOCKER]
