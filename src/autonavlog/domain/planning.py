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

ARRIVAL_ALTITUDE_RULE_VERSION = "CAC_REV19_8_4_9_V3"
CP_PROJECTION_POLICY_VERSION = "CP_ABEAM_WGS84_V1"


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
    state_schema_version: Literal[4] = 4
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
    if version == 4:
        return PersistedUiState.model_validate_json(payload)
    if version == 3:
        legacy = _PersistedUiStateV3.model_validate_json(payload)
        return PersistedUiState(
            calculated_against_fingerprint=(legacy.calculated_against_fingerprint),
            defaults_review_fingerprint=legacy.defaults_review_fingerprint,
            manual_qnh_fingerprint=legacy.manual_qnh_fingerprint,
            arrival_plan=legacy.arrival_plan,
            reference_data_snapshot=legacy.reference_data_snapshot,
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
    derived_pattern_altitude_ft_msl: int = Field(ge=1000, multiple_of=100)
    pattern_altitude_ft_msl: FiniteFloat = Field(ge=0)
    base_vrep_altitude_ft_msl: int = Field(multiple_of=100)
    excess_distance_nm_exact: FiniteFloat = Field(ge=0)
    excess_distance_nm_rounded: int = Field(ge=0)
    automatic_altitude_ft_msl: int = Field(multiple_of=100)
    adopted_altitude_ft_msl: int = Field(multiple_of=100)
    adopted_source: AdoptedSource
    manual_override_reason: str | None = None
    selected_reference_fingerprint: Sha256Hex
    rule_version: Literal["CAC_REV19_8_4_9_V3"] = "CAC_REV19_8_4_9_V3"

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
        derived_pattern = rounded_elevation + 1000
        base_altitude = derived_pattern + 500
        excess_exact = max(0.0, effective_distance - 5.0)
        excess_rounded = _round_half_up_nonnegative(excess_exact)
        automatic = base_altitude + 200 * excess_rounded
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
