from __future__ import annotations

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.domain.enums import FlightPhase, RouteNodeRole, WeatherRequestKind
from autonavlog.domain.planning import (
    AirportSelection,
    ArrivalPlan,
    PatternAltitudeValidationStatus,
    PersistedUiState,
    ReferenceDataSnapshot,
)
from autonavlog.domain.project import NavSection, Project, RouteNode
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.storage.airports import AirportRepository
from autonavlog.weather.fake_provider import FakeWeatherProvider


def _selection(
    *,
    airport_id: str,
    name: str,
    latitude_deg: float,
    longitude_deg: float,
    elevation_ft_msl: float,
    pattern_altitude_ft_msl: float,
) -> AirportSelection:
    return AirportSelection(
        id=airport_id,
        icao=airport_id,
        name=name,
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        elevation_ft_msl=elevation_ft_msl,
        pattern_altitude_ft_msl=pattern_altitude_ft_msl,
        pattern_altitude_source="fixture primary source",
        pattern_altitude_source_revision="fixture-v1",
        pattern_altitude_validation_status=PatternAltitudeValidationStatus.VERIFIED,
        source="fixture",
        source_revision="fixture-v1",
    )


def _arrival_project(project: Project) -> Project:
    departure = RouteNode(
        sequence=0,
        name="RJFM",
        latitude_deg=31.877,
        longitude_deg=131.449,
        role=RouteNodeRole.AIRPORT,
    )
    climb_end = RouteNode(
        sequence=1,
        name="RCA-side",
        latitude_deg=32.15,
        longitude_deg=131.49,
        role=RouteNodeRole.ROUTE_POINT,
    )
    cruise_end = RouteNode(
        sequence=2,
        name="EOC-side",
        latitude_deg=32.65,
        longitude_deg=131.59,
        role=RouteNodeRole.ROUTE_POINT,
    )
    destination = RouteNode(
        sequence=4,
        name="RJFO",
        latitude_deg=33.479,
        longitude_deg=131.737,
        role=RouteNodeRole.DESTINATION,
    )
    destination_to_route = geodesic_leg(
        destination.latitude_deg,
        destination.longitude_deg,
        cruise_end.latitude_deg,
        cruise_end.longitude_deg,
    )
    vrep_latitude, vrep_longitude = point_along_leg(
        destination.latitude_deg,
        destination.longitude_deg,
        destination_to_route.initial_true_course_deg,
        5.0,
    )
    vrep = RouteNode(
        sequence=3,
        name="VREP",
        latitude_deg=vrep_latitude,
        longitude_deg=vrep_longitude,
        role=RouteNodeRole.VISUAL_REPORTING_POINT,
    )
    nodes = (departure, climb_end, cruise_end, vrep, destination)
    phases_and_altitudes = (
        (FlightPhase.CLIMB, 5_000.0),
        (FlightPhase.CRUISE, 5_000.0),
        (FlightPhase.DESCENT, 5_000.0),
        (FlightPhase.VISUAL_ARRIVAL, 3_000.0),
    )
    sections = [
        NavSection(
            sequence=index,
            from_node_id=start.id,
            to_node_id=end.id,
            phase=phase,
            planned_altitude_ft_msl=altitude,
        )
        for index, ((start, end), (phase, altitude)) in enumerate(
            zip(
                zip(nodes, nodes[1:], strict=False),
                phases_and_altitudes,
                strict=True,
            )
        )
    ]
    routed = Project.model_validate(
        project.model_dump() | {"route_nodes": list(nodes), "sections": sections}
    )
    state = PersistedUiState(
        arrival_plan=ArrivalPlan(visual_reporting_point_node_id=vrep.id),
        reference_data_snapshot=ReferenceDataSnapshot(
            departure_airport=_selection(
                airport_id="RJFM",
                name="Miyazaki",
                latitude_deg=departure.latitude_deg,
                longitude_deg=departure.longitude_deg,
                elevation_ft_msl=20.0,
                pattern_altitude_ft_msl=1_020.0,
            ),
            destination_airport=_selection(
                airport_id="RJFO",
                name="Oita",
                latitude_deg=destination.latitude_deg,
                longitude_deg=destination.longitude_deg,
                elevation_ft_msl=19.0,
                pattern_altitude_ft_msl=1_019.0,
            ),
        ),
    )
    routed.metadata["ui_state"] = state.model_dump(mode="json")
    return routed


def test_arrival_altitude_flows_through_descent_eoc_weather_and_nav_alt(
    airports: AirportRepository,
    performance_repository: PerformanceRepository,
    project: Project,
) -> None:
    service = CalculationService(airports, performance_repository)
    outcome = service.calculate(
        _arrival_project(project),
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    assert outcome.arrival_altitude is not None
    assert outcome.arrival_altitude.distance_nm_exact == pytest.approx(5.0, abs=1e-9)
    assert outcome.arrival_altitude.adopted_altitude_ft_msl == 1_500
    visual = next(
        section for section in outcome.sections if section.phase == FlightPhase.VISUAL_ARRIVAL
    )
    assert visual.planned_altitude_ft_msl.adopted() == 1_500
    assert visual.planned_altitude_ft_msl.automatic_metadata["source"] == "ARRIVAL_ALTITUDE_RULE"
    descent = next(section for section in outcome.sections if section.phase == FlightPhase.DESCENT)
    assert descent.performance_metadata["target_altitude_ft_msl"] == 1_500
    assert any(point.type.value == "EOC" for point in outcome.derived_points)

    requests = {request.request_id: request for request in service.last_weather_requests}
    descent_request = next(
        request
        for request in requests.values()
        if request.kind == WeatherRequestKind.ALOFT
        and request.metadata["phase"] == FlightPhase.DESCENT.value
    )
    visual_request = next(
        request
        for request in requests.values()
        if request.kind == WeatherRequestKind.ALOFT
        and request.metadata["phase"] == FlightPhase.VISUAL_ARRIVAL.value
    )
    assert descent_request.metadata["lower_altitude_ft_msl"] == 1_500
    assert visual_request.metadata["upper_altitude_ft_msl"] == 1_500
    assert visual_request.altitude_ft_msl == pytest.approx((1_500 + 19) / 2)
