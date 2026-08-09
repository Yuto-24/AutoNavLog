from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from autonavlog.domain.enums import AdoptedSource
from autonavlog.domain.planning import (
    ARRIVAL_ALTITUDE_RULE_VERSION,
    AirportSelection,
    ArrivalAltitudeMode,
    ArrivalAltitudeResult,
    ArrivalPlan,
    PatternAltitudeValidationStatus,
    PersistedUiState,
    ReferenceDataSnapshot,
    load_persisted_ui_state,
)

HEX = "a" * 64


def _airport(elevation: float = 490) -> AirportSelection:
    return AirportSelection(
        id="RJXX",
        icao="RJXX",
        name="Fixture",
        latitude_deg=31.0,
        longitude_deg=131.0,
        elevation_ft_msl=elevation,
        pattern_altitude_ft_msl=1500,
        pattern_altitude_source="fixture",
        pattern_altitude_source_revision="fixture-v1",
        pattern_altitude_validation_status=(PatternAltitudeValidationStatus.VERIFIED),
        source="fixture",
        source_revision="fixture-v1",
    )


def _result(
    *,
    elevation: float,
    distance: float,
    rounded_elevation: int,
    excess_rounded: int,
) -> ArrivalAltitudeResult:
    effective = 5.0 if abs(distance - 5.0) * 1852 <= 1 + 1e-9 else distance
    derived_pattern = rounded_elevation + 1000
    base = derived_pattern + 500
    automatic = base + 200 * excess_rounded
    return ArrivalAltitudeResult(
        vrep_node_id=uuid4(),
        destination_airport_id="RJXX",
        distance_nm_exact=distance,
        effective_distance_nm=effective,
        airport_elevation_ft_msl=elevation,
        airport_elevation_rounded_ft_msl=rounded_elevation,
        derived_pattern_altitude_ft_msl=derived_pattern,
        pattern_altitude_ft_msl=1500,
        base_vrep_altitude_ft_msl=base,
        excess_distance_nm_exact=max(0.0, effective - 5.0),
        excess_distance_nm_rounded=excess_rounded,
        automatic_altitude_ft_msl=automatic,
        adopted_altitude_ft_msl=automatic,
        adopted_source=AdoptedSource.AUTOMATIC,
        selected_reference_fingerprint=HEX,
        rule_version=ARRIVAL_ALTITUDE_RULE_VERSION,
    )


@pytest.mark.parametrize(
    ("elevation", "distance", "rounded_elevation", "excess", "altitude"),
    [
        (490, 5.0, 500, 0, 2000),
        (19, 5.0, 0, 0, 1500),
        (300, 8.0, 300, 3, 2400),
        (490, 5.49, 500, 0, 2000),
        (490, 5.50, 500, 1, 2200),
    ],
)
def test_arrival_altitude_rule_examples(
    elevation: float,
    distance: float,
    rounded_elevation: int,
    excess: int,
    altitude: int,
) -> None:
    result = _result(
        elevation=elevation,
        distance=distance,
        rounded_elevation=rounded_elevation,
        excess_rounded=excess,
    )
    assert result.adopted_altitude_ft_msl == altitude


def test_arrival_distance_boundary_uses_one_meter_tolerance() -> None:
    within = 5.0 + 1.0 / 1852.0
    result = _result(
        elevation=490,
        distance=within,
        rounded_elevation=500,
        excess_rounded=0,
    )
    assert result.effective_distance_nm == 5.0


def test_arrival_result_rejects_tampered_derived_values() -> None:
    result = _result(
        elevation=490,
        distance=5.5,
        rounded_elevation=500,
        excess_rounded=1,
    )
    with pytest.raises(ValidationError, match="automatic_altitude"):
        ArrivalAltitudeResult.model_validate(
            result.model_dump() | {"automatic_altitude_ft_msl": 2400},
        )


def test_arrival_plan_manual_mode_requires_altitude_and_reason() -> None:
    node_id = uuid4()
    with pytest.raises(ValidationError, match="requires altitude and reason"):
        ArrivalPlan(
            visual_reporting_point_node_id=node_id,
            altitude_mode=ArrivalAltitudeMode.MANUAL_NON_STANDARD_ENTRY,
        )
    plan = ArrivalPlan(
        visual_reporting_point_node_id=node_id,
        altitude_mode=ArrivalAltitudeMode.MANUAL_NON_STANDARD_ENTRY,
        manual_vrep_altitude_ft_msl=2200,
        manual_override_reason="Direct Base",
    )
    assert plan.manual_vrep_altitude_ft_msl == 2200


def test_ui_state_v3_migration_discards_sea_state() -> None:
    airport = _airport()
    snapshot = ReferenceDataSnapshot(
        departure_airport=airport,
        destination_airport=airport,
    )
    raw = {
        "state_schema_version": 3,
        "calculated_against_fingerprint": HEX,
        "defaults_review_fingerprint": None,
        "manual_qnh_fingerprint": None,
        "arrival_plan": {
            "visual_reporting_point_node_id": str(UUID("00000000-0000-0000-0000-000000000001")),
            "altitude_mode": "STANDARD_DISTANCE_RULE",
            "manual_vrep_altitude_ft_msl": None,
            "manual_override_reason": None,
        },
        "reference_data_snapshot": snapshot.model_dump(mode="json"),
        "sea_states": {"legacy": {"confirmed": True}},
    }
    migrated = load_persisted_ui_state(raw)
    assert isinstance(migrated, PersistedUiState)
    assert migrated.state_schema_version == 4
    assert "sea" not in migrated.model_dump_json().lower()


def test_ui_state_rejects_unknown_versions_and_fields() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        load_persisted_ui_state({"state_schema_version": 2})
    with pytest.raises(ValidationError):
        load_persisted_ui_state({"state_schema_version": 4, "unknown": "unsafe"})
