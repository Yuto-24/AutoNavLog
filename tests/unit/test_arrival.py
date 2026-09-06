from __future__ import annotations

import pytest

from autonavlog.application.arrival import (
    ARITA_COORDINATE,
    RJFM_ARP_COORDINATE,
    SHIRAHAMA_COORDINATE,
    automatic_vrep_altitude,
    calculate_arrival_altitude,
)
from autonavlog.domain.enums import AdoptedSource, RouteNodeRole
from autonavlog.domain.planning import (
    AirportSelection,
    ArrivalAltitudeMode,
    ArrivalPlan,
    PatternAltitudeValidationStatus,
    PersistedUiState,
    ReferenceDataSnapshot,
)
from autonavlog.domain.project import Project
from autonavlog.nav.geodesy import point_along_leg


def _state(
    project: Project,
    *,
    manual_altitude: int | None = None,
    manual_reason: str | None = None,
    selected_pattern: int = 1000,
    validation: PatternAltitudeValidationStatus = (PatternAltitudeValidationStatus.VERIFIED),
) -> PersistedUiState:
    project.route_nodes[-2].role = RouteNodeRole.VISUAL_REPORTING_POINT
    destination = AirportSelection(
        id=project.destination_airport_id,
        icao=project.destination_airport_id,
        name="Destination",
        latitude_deg=project.route_nodes[-1].latitude_deg,
        longitude_deg=project.route_nodes[-1].longitude_deg,
        elevation_ft_msl=19,
        pattern_altitude_ft_msl=1000,
        pattern_altitude_source="fixture",
        pattern_altitude_source_revision="fixture-v1",
        pattern_altitude_validation_status=validation,
        source="fixture",
        source_revision="fixture-v1",
    )
    departure = destination.model_copy(
        update={
            "id": project.departure_airport_id,
            "icao": project.departure_airport_id,
            "name": "Departure",
            "latitude_deg": project.route_nodes[0].latitude_deg,
            "longitude_deg": project.route_nodes[0].longitude_deg,
        }
    )
    manual = manual_altitude is not None or manual_reason is not None
    return PersistedUiState(
        arrival_plan=ArrivalPlan(
            visual_reporting_point_node_id=project.route_nodes[-2].id,
            selected_pattern_altitude_ft_msl=selected_pattern,
            selected_pattern_altitude_source=(
                AdoptedSource.AUTOMATIC
                if selected_pattern == destination.pattern_altitude_ft_msl
                else AdoptedSource.MANUAL
            ),
            altitude_mode=(
                ArrivalAltitudeMode.MANUAL_NON_STANDARD_ENTRY
                if manual
                else ArrivalAltitudeMode.STANDARD_DISTANCE_RULE
            ),
            manual_vrep_altitude_ft_msl=manual_altitude,
            manual_override_reason=manual_reason,
        ),
        reference_data_snapshot=ReferenceDataSnapshot(
            departure_airport=departure,
            destination_airport=destination,
        ),
    )


def test_standard_arrival_uses_verified_snapshot_and_route_vrep(
    project: Project,
) -> None:
    working = project.model_copy(deep=True)
    computation = calculate_arrival_altitude(working, _state(working))
    assert computation.issues == ()
    assert computation.result is not None
    assert computation.result.adopted_source == AdoptedSource.AUTOMATIC
    assert computation.result.destination_airport_id == "RJFO"
    assert computation.result.airport_elevation_rounded_ft_msl == 0
    assert computation.result.base_vrep_altitude_ft_msl == 1500


def test_oita_west_pattern_override_uses_1300_ft(
    project: Project,
) -> None:
    working = project.model_copy(deep=True)
    computation = calculate_arrival_altitude(
        working,
        _state(working, selected_pattern=1300),
    )
    assert computation.issues == ()
    assert computation.result is not None
    assert computation.result.selected_pattern_altitude_ft_msl == 1300
    assert computation.result.selected_pattern_altitude_source == AdoptedSource.MANUAL
    assert computation.result.base_vrep_altitude_ft_msl == 1800


def test_unverified_pattern_altitude_blocks_arrival(
    project: Project,
) -> None:
    working = project.model_copy(deep=True)
    computation = calculate_arrival_altitude(
        working,
        _state(
            working,
            validation=PatternAltitudeValidationStatus.UNVERIFIED,
        ),
    )
    assert computation.result is None
    assert {issue.code for issue in computation.issues} == {"PATTERN_ALTITUDE_REQUIRED"}


def test_manual_arrival_requires_height_above_airport(
    project: Project,
) -> None:
    working = project.model_copy(deep=True)
    computation = calculate_arrival_altitude(
        working,
        _state(
            working,
            manual_altitude=0,
            manual_reason="Direct Base",
        ),
    )
    assert computation.result is None
    assert {issue.code for issue in computation.issues} == {"VISUAL_REPORTING_POINT_ROUTE_INVALID"}


def test_vrep_must_be_immediately_before_destination(
    project: Project,
) -> None:
    working = project.model_copy(deep=True)
    state = _state(working)
    state.arrival_plan = ArrivalPlan(
        visual_reporting_point_node_id=working.route_nodes[0].id,
        selected_pattern_altitude_ft_msl=1000,
        selected_pattern_altitude_source=AdoptedSource.AUTOMATIC,
    )
    working.route_nodes[0].role = RouteNodeRole.VISUAL_REPORTING_POINT
    computation = calculate_arrival_altitude(working, state)
    assert computation.result is None
    assert {issue.code for issue in computation.issues} == {"VISUAL_REPORTING_POINT_ROUTE_INVALID"}


def _rjfm_arrival_state(
    project: Project,
    *,
    vrep_name: str,
    vrep_coordinate: tuple[float, float],
    manual_altitude: int | None = None,
) -> PersistedUiState:
    project.destination_airport_id = "RJFM"
    destination_node = project.route_nodes[-1]
    destination_node.name = "RJFM"
    destination_node.latitude_deg, destination_node.longitude_deg = RJFM_ARP_COORDINATE
    vrep = project.route_nodes[-2]
    vrep.name = vrep_name
    vrep.latitude_deg, vrep.longitude_deg = vrep_coordinate
    state = _state(
        project,
        manual_altitude=manual_altitude,
        manual_reason=None if manual_altitude is None else "Training entry",
    )
    assert state.reference_data_snapshot is not None
    destination = state.reference_data_snapshot.destination_airport.model_copy(
        update={
            "id": "RJFM",
            "icao": "RJFM",
            "name": "宮崎",
            "latitude_deg": RJFM_ARP_COORDINATE[0],
            "longitude_deg": RJFM_ARP_COORDINATE[1],
            "elevation_ft_msl": 19,
            "pattern_altitude_ft_msl": 1000,
        }
    )
    departure = state.reference_data_snapshot.departure_airport
    state.reference_data_snapshot = ReferenceDataSnapshot(
        departure_airport=departure,
        destination_airport=destination,
    )
    return state


@pytest.mark.parametrize(
    ("name", "coordinate", "expected_match"),
    [
        ("V-REP 有田 1500ft(MZE 322/6.0)", (20.0, 120.0), "ARITA"),
        (" v-rep  ａｒｉｔａ (MZE 322/6.0) ", (20.0, 120.0), "ARITA"),
        ("renamed route point", ARITA_COORDINATE, "ARITA"),
        ("白浜", (20.0, 120.0), "SHIRAHAMA"),
        ("renamed route point", SHIRAHAMA_COORDINATE, "SHIRAHAMA"),
    ],
)
def test_rjfm_special_final_vrep_uses_1500_ft_by_name_or_coordinate(
    project: Project,
    name: str,
    coordinate: tuple[float, float],
    expected_match: str,
) -> None:
    working = project.model_copy(deep=True)
    computation = calculate_arrival_altitude(
        working,
        _rjfm_arrival_state(
            working,
            vrep_name=name,
            vrep_coordinate=coordinate,
        ),
    )

    assert computation.issues == ()
    assert computation.result is not None
    assert computation.result.automatic_altitude_ft_msl == 1500
    assert computation.result.adopted_altitude_ft_msl == 1500
    assert computation.result.automatic_altitude_rule == "RJFM_ARITA_SHIRAHAMA_1500FT"
    assert computation.result.automatic_altitude_reason == (
        f"RJFM final VREP matched {expected_match}"
    )


def test_rjfm_special_vrep_does_not_match_narita_or_outside_coordinate_radius(
    project: Project,
) -> None:
    at_radius = point_along_leg(*ARITA_COORDINATE, 90.0, 0.5)
    assert automatic_vrep_altitude(
        8.0,
        1000,
        destination_icao="RJFM",
        vrep_name="NARITA",
        vrep_latitude_deg=at_radius[0],
        vrep_longitude_deg=at_radius[1],
    ).rule == "RJFM_ARITA_SHIRAHAMA_1500FT"
    outside_arita = point_along_leg(*ARITA_COORDINATE, 90.0, 0.5001)
    working = project.model_copy(deep=True)
    computation = calculate_arrival_altitude(
        working,
        _rjfm_arrival_state(
            working,
            vrep_name="NARITA",
            vrep_coordinate=outside_arita,
        ),
    )

    assert computation.issues == ()
    assert computation.result is not None
    assert computation.result.automatic_altitude_rule == "STANDARD_DISTANCE_RULE"
    assert computation.result.automatic_altitude_ft_msl != 1500


def test_rjfm_special_vrep_preserves_manual_altitude(
    project: Project,
) -> None:
    working = project.model_copy(deep=True)
    computation = calculate_arrival_altitude(
        working,
        _rjfm_arrival_state(
            working,
            vrep_name="ARITA",
            vrep_coordinate=ARITA_COORDINATE,
            manual_altitude=2100,
        ),
    )

    assert computation.issues == ()
    assert computation.result is not None
    assert computation.result.automatic_altitude_ft_msl == 1500
    assert computation.result.adopted_altitude_ft_msl == 2100
    assert computation.result.adopted_source == AdoptedSource.MANUAL


def test_special_vrep_does_not_apply_outside_rjfm() -> None:
    policy = automatic_vrep_altitude(
        8.0,
        1000,
        destination_icao="RJFO",
        vrep_name="ARITA",
        vrep_latitude_deg=ARITA_COORDINATE[0],
        vrep_longitude_deg=ARITA_COORDINATE[1],
    )
    assert policy.rule == "STANDARD_DISTANCE_RULE"
    assert policy.altitude_ft_msl == 2100
