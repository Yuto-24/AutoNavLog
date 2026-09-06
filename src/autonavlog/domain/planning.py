from __future__ import annotations

import json
from math import floor
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    model_validator,
)

from .enums import AdoptedSource, RouteNodeRole, StrEnum

Sha256Hex = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]

ARRIVAL_ALTITUDE_RULE_VERSION = "CAC_REV19_8_4_9_V5"
CP_PROJECTION_POLICY_VERSION = "CP_ABEAM_WGS84_V1"
RJFM_DEPARTURE_RULE_VERSION = "RJFM_NORTHBOUND_R6_5_1_V2"
RJFM_INBOUND_RULE_VERSION = "RJFM_INBOUND_OMARU_UMK_VREP_V1"


class PlanningModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        validate_assignment=True,
    )


class ArrivalAltitudeMode(StrEnum):
    STANDARD_DISTANCE_RULE = "STANDARD_DISTANCE_RULE"
    MANUAL_NON_STANDARD_ENTRY = "MANUAL_NON_STANDARD_ENTRY"


class PatternAltitudeValidationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    REJECTED = "REJECTED"


class RjfmDepartureTrigger(StrEnum):
    UMK = "UMK"
    OMARU = "OMARU"


class RjfmMainRouteMode(StrEnum):
    UMK_PHYSICAL = "UMK_PHYSICAL"
    OMARU_VIRTUAL_UMK = "OMARU_VIRTUAL_UMK"


class RjfmInboundApplicationStatus(StrEnum):
    APPLIED = "APPLIED"


class RjfmGuidanceStatus(StrEnum):
    VALID = "VALID"
    WARNING = "WARNING"
    HARD_INVALID = "HARD_INVALID"
    UNAVAILABLE = "UNAVAILABLE"


class RjfmTurnMethod(StrEnum):
    FIXED_BANK_20 = "FIXED_BANK_20"
    ADJUSTED_MAX_RADIUS = "ADJUSTED_MAX_RADIUS"
    NONE = "NONE"


class RjfmTurnDirection(StrEnum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"


class RjfmCoordinate(PlanningModel):
    latitude_deg: FiniteFloat = Field(ge=-90, le=90)
    longitude_deg: FiniteFloat = Field(ge=-180, le=180)
    source: str = Field(min_length=1)
    estimated_error_nm: FiniteFloat = Field(ge=0)


class RjfmDeparturePlan(PlanningModel):
    rule_version: Literal[
        "RJFM_NORTHBOUND_R6_5_1_V1",
        "RJFM_NORTHBOUND_R6_5_1_V2",
    ] = "RJFM_NORTHBOUND_R6_5_1_V2"
    trigger: RjfmDepartureTrigger
    main_route_mode: RjfmMainRouteMode
    target_altitude_ft_msl: Literal[5500] = 5500
    umk: RjfmCoordinate
    over_field: RjfmCoordinate
    omaru: RjfmCoordinate
    virtual_rca_distance_nm: FiniteFloat = Field(gt=0)
    route_application_key: str = Field(min_length=1)
    reference_revision: str = Field(min_length=1)
    reference_content_fingerprint: Sha256Hex


class RjfmInboundPlan(PlanningModel):
    """Persisted, coordinate-triggered RJFM return profile."""

    rule_version: Literal["RJFM_INBOUND_OMARU_UMK_VREP_V1"] = "RJFM_INBOUND_OMARU_UMK_VREP_V1"
    application_status: RjfmInboundApplicationStatus = RjfmInboundApplicationStatus.APPLIED
    application_reason: str = Field(min_length=1)
    omaru_node_id: UUID
    umk_node_id: UUID
    vrep_node_id: UUID
    controlled_section_id: UUID
    # All physical sections from OMARU through UMK.  The singular field is
    # retained for persisted V1 plans and identifies the first section.
    controlled_section_ids: tuple[UUID, ...] = ()
    omaru_coordinate: RjfmCoordinate
    umk_coordinate: RjfmCoordinate
    adopted_vrep_altitude_ft_msl: int | None = Field(default=None, multiple_of=100)
    descent_rate_fpm: Literal[500, 1000]
    reference_revision: str = Field(min_length=1)
    reference_content_fingerprint: Sha256Hex


class RjfmGuidancePathPoint(PlanningModel):
    latitude_deg: FiniteFloat = Field(ge=-90, le=90)
    longitude_deg: FiniteFloat = Field(ge=-180, le=180)
    altitude_ft_msl: FiniteFloat
    elapsed_seconds: FiniteFloat = Field(ge=0)
    segment: str = Field(min_length=1)


class RjfmConstraintResult(PlanningModel):
    code: str = Field(min_length=1)
    passed: bool
    hard: bool
    message: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RjfmRunwayGuidance(PlanningModel):
    runway: Literal["09", "27"]
    status: RjfmGuidanceStatus
    turn_method: RjfmTurnMethod = RjfmTurnMethod.NONE
    path: list[RjfmGuidancePathPoint] = Field(default_factory=list)
    constraints: list[RjfmConstraintResult] = Field(default_factory=list)
    turn_direction: RjfmTurnDirection
    full_turns: int = Field(default=0, ge=0)
    partial_turn_deg: FiniteFloat | None = Field(default=None, ge=0, lt=360)
    turn_entry_radial_deg: FiniteFloat | None = Field(default=None, ge=0, lt=360)
    turn_entry_dme_nm: FiniteFloat | None = Field(default=None, ge=0)
    turn_entry_altitude_ft_msl: FiniteFloat | None = None
    exit_drift_nm: FiniteFloat | None = Field(default=None, ge=0)
    expected_time_delta_seconds: FiniteFloat | None = None
    position_residual_nm: FiniteFloat | None = Field(default=None, ge=0)
    altitude_residual_ft: FiniteFloat | None = Field(default=None, ge=0)
    tangent_residual_deg: FiniteFloat | None = Field(default=None, ge=0)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_left_turn_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        has_legacy_full = "full_left_turns" in migrated
        has_legacy_partial = "partial_left_turn_deg" in migrated
        if not has_legacy_full and not has_legacy_partial:
            return migrated

        direction = migrated.get("turn_direction")
        if direction not in (None, "LEFT", RjfmTurnDirection.LEFT):
            raise ValueError("legacy left-turn fields conflict with turn_direction")
        migrated["turn_direction"] = "LEFT"

        if has_legacy_full:
            legacy_full = migrated.pop("full_left_turns")
            current_full = migrated.get("full_turns", legacy_full)
            if current_full != legacy_full:
                raise ValueError("legacy and generic full-turn fields conflict")
            migrated["full_turns"] = legacy_full
        if has_legacy_partial:
            legacy_partial = migrated.pop("partial_left_turn_deg")
            current_partial = migrated.get("partial_turn_deg", legacy_partial)
            if current_partial != legacy_partial:
                raise ValueError("legacy and generic partial-turn fields conflict")
            migrated["partial_turn_deg"] = legacy_partial
        return migrated


class RjfmDepartureGuidance(PlanningModel):
    rule_version: Literal[
        "RJFM_NORTHBOUND_R6_5_1_V1",
        "RJFM_NORTHBOUND_R6_5_1_V2",
    ] = "RJFM_NORTHBOUND_R6_5_1_V2"
    reference_revision: str = Field(min_length=1)
    reference_content_fingerprint: Sha256Hex
    source_effective_dates: dict[str, str] = Field(default_factory=dict)
    generated_against_fingerprint: Sha256Hex
    candidates: list[RjfmRunwayGuidance] = Field(min_length=2, max_length=2)
    center_route: list[RjfmCoordinate] = Field(min_length=3, max_length=3)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_runways(self) -> RjfmDepartureGuidance:
        if {candidate.runway for candidate in self.candidates} != {"09", "27"}:
            raise ValueError("RJFM guidance must contain exactly RWY09 and RWY27")
        if self.rule_version == RJFM_DEPARTURE_RULE_VERSION:
            expected_directions = {
                "09": RjfmTurnDirection.LEFT,
                "27": RjfmTurnDirection.RIGHT,
            }
            if any(
                candidate.turn_direction != expected_directions[candidate.runway]
                for candidate in self.candidates
            ):
                raise ValueError(
                    "current RJFM guidance requires LEFT for RWY09 and RIGHT for RWY27"
                )
        return self


class MasterReference(PlanningModel):
    dataset_id: str = Field(min_length=1)
    dataset_revision: str = Field(min_length=1)
    entity_kind: Literal["AIRPORT", "POINT", "CHECK_POINT"]
    entity_id: str = Field(min_length=1)
    row_fingerprint: Sha256Hex


class AirportSelection(PlanningModel):
    id: str = Field(min_length=1)
    icao: str = Field(min_length=1)
    name: str = Field(min_length=1)
    latitude_deg: FiniteFloat = Field(ge=-90, le=90)
    longitude_deg: FiniteFloat = Field(ge=-180, le=180)
    elevation_ft_msl: FiniteFloat = Field(ge=0, le=20_000)
    pattern_altitude_ft_msl: FiniteFloat = Field(ge=0, le=25_000)
    pattern_altitude_source: str = Field(min_length=1)
    pattern_altitude_source_revision: str = Field(min_length=1)
    pattern_altitude_validation_status: PatternAltitudeValidationStatus
    source: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    origin: MasterReference | None = None

    @property
    def arp_coordinate(self) -> tuple[float, float]:
        return float(self.latitude_deg), float(self.longitude_deg)

    def canonical_row(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"origin"})


class PointSelection(PlanningModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    latitude_deg: FiniteFloat = Field(ge=-90, le=90)
    longitude_deg: FiniteFloat = Field(ge=-180, le=180)
    point_role: RouteNodeRole
    source: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    notes: str = ""
    origin: MasterReference | None = None

    def canonical_row(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"origin"})


class CheckPointSelection(PlanningModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    latitude_deg: FiniteFloat = Field(ge=-90, le=90)
    longitude_deg: FiniteFloat = Field(ge=-180, le=180)
    source: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    notes: str = ""
    origin: MasterReference | None = None

    def canonical_row(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"origin"})


class ReferenceDataSnapshot(PlanningModel):
    departure_airport: AirportSelection
    destination_airport: AirportSelection
    route_points: dict[UUID, PointSelection] = Field(default_factory=dict)
    check_points: dict[UUID, CheckPointSelection] = Field(default_factory=dict)


class ArrivalPlan(PlanningModel):
    visual_reporting_point_node_id: UUID
    selected_pattern_altitude_ft_msl: int | None = Field(
        default=None,
        ge=100,
        le=25_000,
        multiple_of=100,
    )
    selected_pattern_altitude_source: AdoptedSource | None = None
    altitude_mode: ArrivalAltitudeMode = ArrivalAltitudeMode.STANDARD_DISTANCE_RULE
    manual_vrep_altitude_ft_msl: int | None = Field(
        default=None,
        ge=-1000,
        le=25_000,
        multiple_of=100,
    )
    manual_override_reason: str | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> ArrivalPlan:
        selected_values = (
            self.selected_pattern_altitude_ft_msl,
            self.selected_pattern_altitude_source,
        )
        if (selected_values[0] is None) != (selected_values[1] is None):
            raise ValueError("selected pattern altitude and source must be supplied together")
        if self.altitude_mode == ArrivalAltitudeMode.STANDARD_DISTANCE_RULE:
            if (
                self.manual_vrep_altitude_ft_msl is not None
                or self.manual_override_reason is not None
            ):
                raise ValueError("standard arrival must not contain manual fields")
        elif (
            self.manual_vrep_altitude_ft_msl is None
            or not (self.manual_override_reason or "").strip()
        ):
            raise ValueError("manual arrival requires altitude and reason")
        return self


class PersistedUiState(PlanningModel):
    state_schema_version: Literal[7] = 7
    calculated_against_fingerprint: Sha256Hex | None = None
    defaults_review_fingerprint: Sha256Hex | None = None
    arrival_plan: ArrivalPlan | None = None
    reference_data_snapshot: ReferenceDataSnapshot | None = None
    rjfm_departure_plan: RjfmDeparturePlan | None = None
    rjfm_departure_guidance: RjfmDepartureGuidance | None = None
    rjfm_inbound_plan: RjfmInboundPlan | None = None


class _PersistedUiStateV6(PlanningModel):
    state_schema_version: Literal[6]
    calculated_against_fingerprint: Sha256Hex | None = None
    defaults_review_fingerprint: Sha256Hex | None = None
    arrival_plan: ArrivalPlan | None = None
    reference_data_snapshot: ReferenceDataSnapshot | None = None
    rjfm_departure_plan: RjfmDeparturePlan | None = None
    rjfm_departure_guidance: RjfmDepartureGuidance | None = None


class _PersistedUiStateV5(PlanningModel):
    state_schema_version: Literal[5]
    calculated_against_fingerprint: Sha256Hex | None = None
    defaults_review_fingerprint: Sha256Hex | None = None
    manual_qnh_fingerprint: Sha256Hex | None = None
    arrival_plan: ArrivalPlan | None = None
    reference_data_snapshot: ReferenceDataSnapshot | None = None
    rjfm_departure_plan: RjfmDeparturePlan | None = None
    rjfm_departure_guidance: RjfmDepartureGuidance | None = None


class _PersistedUiStateV4(PlanningModel):
    state_schema_version: Literal[4]
    calculated_against_fingerprint: Sha256Hex | None = None
    defaults_review_fingerprint: Sha256Hex | None = None
    manual_qnh_fingerprint: Sha256Hex | None = None
    arrival_plan: ArrivalPlan | None = None
    reference_data_snapshot: ReferenceDataSnapshot | None = None


class _PersistedUiStateV3(PlanningModel):
    state_schema_version: Literal[3]
    calculated_against_fingerprint: Sha256Hex | None = None
    defaults_review_fingerprint: Sha256Hex | None = None
    manual_qnh_fingerprint: Sha256Hex | None = None
    arrival_plan: ArrivalPlan | None = None
    reference_data_snapshot: ReferenceDataSnapshot | None = None
    sea_states: dict[str, Any] = Field(default_factory=dict)


def load_persisted_ui_state(raw: Any) -> PersistedUiState:
    if not isinstance(raw, dict):
        raise ValueError("ui_state must be an object")
    version = raw.get("state_schema_version")
    payload = json.dumps(
        raw,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    if version == 7:
        return PersistedUiState.model_validate_json(payload)
    if version == 6:
        legacy_v6 = _PersistedUiStateV6.model_validate_json(payload)
        return PersistedUiState(
            calculated_against_fingerprint=legacy_v6.calculated_against_fingerprint,
            defaults_review_fingerprint=legacy_v6.defaults_review_fingerprint,
            arrival_plan=legacy_v6.arrival_plan,
            reference_data_snapshot=legacy_v6.reference_data_snapshot,
            rjfm_departure_plan=legacy_v6.rjfm_departure_plan,
            rjfm_departure_guidance=legacy_v6.rjfm_departure_guidance,
        )
    if version == 5:
        legacy_v5 = _PersistedUiStateV5.model_validate_json(payload)
        return PersistedUiState(
            calculated_against_fingerprint=legacy_v5.calculated_against_fingerprint,
            defaults_review_fingerprint=legacy_v5.defaults_review_fingerprint,
            arrival_plan=legacy_v5.arrival_plan,
            reference_data_snapshot=legacy_v5.reference_data_snapshot,
            rjfm_departure_plan=legacy_v5.rjfm_departure_plan,
            rjfm_departure_guidance=legacy_v5.rjfm_departure_guidance,
        )
    if version == 4:
        legacy_v4 = _PersistedUiStateV4.model_validate_json(payload)
        return PersistedUiState(
            calculated_against_fingerprint=legacy_v4.calculated_against_fingerprint,
            defaults_review_fingerprint=legacy_v4.defaults_review_fingerprint,
            arrival_plan=legacy_v4.arrival_plan,
            reference_data_snapshot=legacy_v4.reference_data_snapshot,
        )
    if version == 3:
        legacy_v3 = _PersistedUiStateV3.model_validate_json(payload)
        return PersistedUiState(
            calculated_against_fingerprint=(legacy_v3.calculated_against_fingerprint),
            defaults_review_fingerprint=legacy_v3.defaults_review_fingerprint,
            arrival_plan=legacy_v3.arrival_plan,
            reference_data_snapshot=legacy_v3.reference_data_snapshot,
        )
    raise ValueError("unsupported ui_state schema version")


def _round_half_up_nonnegative(value: float) -> int:
    if value < 0:
        raise ValueError("arrival altitude inputs must be non-negative")
    return floor(value + 0.5)


class ArrivalAltitudeResult(PlanningModel):
    vrep_node_id: UUID
    destination_airport_id: str = Field(min_length=1)
    distance_nm_exact: FiniteFloat = Field(ge=0)
    effective_distance_nm: FiniteFloat = Field(ge=0)
    boundary_tolerance_m: FiniteFloat = Field(default=1.0, ge=1.0, le=1.0)
    airport_elevation_ft_msl: FiniteFloat = Field(ge=0)
    airport_elevation_rounded_ft_msl: int = Field(ge=0, multiple_of=100)
    derived_pattern_altitude_ft_msl: int = Field(ge=100, multiple_of=100)
    pattern_altitude_ft_msl: FiniteFloat = Field(ge=0)
    selected_pattern_altitude_ft_msl: int = Field(ge=100, le=25_000, multiple_of=100)
    selected_pattern_altitude_source: AdoptedSource
    base_vrep_altitude_ft_msl: int = Field(multiple_of=100)
    excess_distance_nm_exact: FiniteFloat = Field(ge=0)
    excess_distance_nm_rounded: int = Field(ge=0)
    automatic_altitude_ft_msl: int = Field(multiple_of=100)
    automatic_altitude_rule: Literal[
        "STANDARD_DISTANCE_RULE",
        "RJFM_ARITA_SHIRAHAMA_1500FT",
    ] = "STANDARD_DISTANCE_RULE"
    automatic_altitude_reason: str | None = None
    adopted_altitude_ft_msl: int = Field(multiple_of=100)
    adopted_source: AdoptedSource
    manual_override_reason: str | None = None
    selected_reference_fingerprint: Sha256Hex
    rule_version: Literal[
        "CAC_REV19_8_4_9_V4",
        "CAC_REV19_8_4_9_V5",
    ] = "CAC_REV19_8_4_9_V5"

    @model_validator(mode="after")
    def validate_derived_values(self) -> ArrivalAltitudeResult:
        distance = float(self.distance_nm_exact)
        effective_distance = (
            5.0
            if abs(distance - 5.0) * 1852.0 <= float(self.boundary_tolerance_m) + 1e-9
            else distance
        )
        rounded_elevation = 100 * _round_half_up_nonnegative(
            float(self.airport_elevation_ft_msl) / 100.0
        )
        selected_pattern = self.selected_pattern_altitude_ft_msl
        derived_pattern = selected_pattern
        base_altitude = selected_pattern + 500
        excess_exact = max(0.0, effective_distance - 5.0)
        excess_rounded = _round_half_up_nonnegative(excess_exact)
        automatic = (
            1500
            if self.automatic_altitude_rule == "RJFM_ARITA_SHIRAHAMA_1500FT"
            else base_altitude + 200 * excess_rounded
        )
        expected = {
            "effective_distance_nm": effective_distance,
            "airport_elevation_rounded_ft_msl": rounded_elevation,
            "derived_pattern_altitude_ft_msl": derived_pattern,
            "base_vrep_altitude_ft_msl": base_altitude,
            "excess_distance_nm_exact": excess_exact,
            "excess_distance_nm_rounded": excess_rounded,
            "automatic_altitude_ft_msl": automatic,
        }
        actual = {
            "effective_distance_nm": float(self.effective_distance_nm),
            "airport_elevation_rounded_ft_msl": (self.airport_elevation_rounded_ft_msl),
            "derived_pattern_altitude_ft_msl": (self.derived_pattern_altitude_ft_msl),
            "base_vrep_altitude_ft_msl": self.base_vrep_altitude_ft_msl,
            "excess_distance_nm_exact": float(self.excess_distance_nm_exact),
            "excess_distance_nm_rounded": self.excess_distance_nm_rounded,
            "automatic_altitude_ft_msl": self.automatic_altitude_ft_msl,
        }
        for key, expected_value in expected.items():
            actual_value = actual[key]
            if isinstance(expected_value, float):
                if abs(float(actual_value) - expected_value) > 1e-9:
                    raise ValueError(f"{key} does not match the arrival rule")
            elif actual_value != expected_value:
                raise ValueError(f"{key} does not match the arrival rule")
        expected_pattern_source = (
            AdoptedSource.AUTOMATIC
            if selected_pattern == self.pattern_altitude_ft_msl
            else AdoptedSource.MANUAL
        )
        if self.selected_pattern_altitude_source != expected_pattern_source:
            raise ValueError("selected pattern altitude source does not match the master value")
        if selected_pattern <= self.airport_elevation_ft_msl:
            raise ValueError("selected pattern altitude must be above airport elevation")
        if self.automatic_altitude_rule == "STANDARD_DISTANCE_RULE":
            if self.automatic_altitude_reason is not None:
                raise ValueError("standard arrival must not contain an automatic reason")
        elif not (self.automatic_altitude_reason or "").strip():
            raise ValueError("special automatic arrival requires a reason")
        if self.adopted_source == AdoptedSource.AUTOMATIC:
            if self.adopted_altitude_ft_msl != automatic:
                raise ValueError("standard arrival must adopt the automatic value")
            if self.manual_override_reason is not None:
                raise ValueError("standard arrival must not contain a manual reason")
        else:
            if not (self.manual_override_reason or "").strip():
                raise ValueError("manual arrival requires a reason")
            if self.adopted_altitude_ft_msl <= self.airport_elevation_ft_msl:
                raise ValueError("manual arrival altitude must exceed airport elevation")
        return self


class CheckPointProjection(PlanningModel):
    checkpoint_id: UUID
    section_id: UUID
    abeam_latitude_deg: FiniteFloat = Field(ge=-90, le=90)
    abeam_longitude_deg: FiniteFloat = Field(ge=-180, le=180)
    along_track_fraction: FiniteFloat = Field(ge=0, le=1)
    along_section_distance_nm: FiniteFloat = Field(ge=0)
    cumulative_distance_nm: FiniteFloat = Field(ge=0)
    cross_track_distance_nm: FiniteFloat = Field(ge=0)
    policy_version: Literal["CP_ABEAM_WGS84_V1"] = "CP_ABEAM_WGS84_V1"
