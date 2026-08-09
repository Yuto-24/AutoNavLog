from __future__ import annotations

from autonavlog.application.arrival import calculate_arrival_altitude
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


def _state(
    project: Project,
    *,
    manual_altitude: int | None = None,
    manual_reason: str | None = None,
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
    )
    working.route_nodes[0].role = RouteNodeRole.VISUAL_REPORTING_POINT
    computation = calculate_arrival_altitude(working, state)
    assert computation.result is None
    assert {issue.code for issue in computation.issues} == {"VISUAL_REPORTING_POINT_ROUTE_INVALID"}
