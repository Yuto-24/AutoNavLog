from __future__ import annotations

import json
from uuid import UUID

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.project_service import ProjectService
from autonavlog.domain.enums import (
    AdoptedSource,
    FlightPhase,
    RouteNodeRole,
    WeatherRequestKind,
)
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
from autonavlog.storage.local import LocalProjectRepository
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
        arrival_plan=ArrivalPlan(
            visual_reporting_point_node_id=vrep.id,
            selected_pattern_altitude_ft_msl=1000,
            selected_pattern_altitude_source=AdoptedSource.AUTOMATIC,
        ),
        reference_data_snapshot=ReferenceDataSnapshot(
            departure_airport=_selection(
                airport_id="RJFM",
                name="Miyazaki",
                latitude_deg=departure.latitude_deg,
                longitude_deg=departure.longitude_deg,
                elevation_ft_msl=20.0,
                pattern_altitude_ft_msl=1_000.0,
            ),
            destination_airport=_selection(
                airport_id="RJFO",
                name="Oita",
                latitude_deg=destination.latitude_deg,
                longitude_deg=destination.longitude_deg,
                elevation_ft_msl=19.0,
                pattern_altitude_ft_msl=1_000.0,
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
        and request.request_id.endswith(":aloft")
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


@pytest.mark.parametrize(
    ("master_pattern", "expected_source"),
    [(1000.0, AdoptedSource.AUTOMATIC), (1100.0, AdoptedSource.MANUAL)],
)
def test_v3_arrival_snapshot_is_migrated_on_load(
    airports: AirportRepository,
    performance_repository: PerformanceRepository,
    project: Project,
    master_pattern: float,
    expected_source: AdoptedSource,
    tmp_path,
) -> None:
    calculation = CalculationService(airports, performance_repository)
    routed = _arrival_project(project)
    outcome = calculation.calculate(routed, FakeWeatherProvider())
    assert outcome.arrival_altitude is not None

    repository = LocalProjectRepository(tmp_path)
    projects = ProjectService(repository)
    saved = projects.save(routed).project
    snapshot_path = projects.snapshot(
        saved,
        outcome,
        calculation,
        msm_package_version=None,
    )
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    arrival = payload["calculation_results"]["arrival_altitude"]
    arrival["rule_version"] = "CAC_REV19_8_4_9_V3"
    arrival["pattern_altitude_ft_msl"] = master_pattern
    arrival.pop("selected_pattern_altitude_ft_msl")
    arrival.pop("selected_pattern_altitude_source")
    arrival_plan = payload["input_data"]["metadata"]["ui_state"]["arrival_plan"]
    arrival_plan.pop("selected_pattern_altitude_ft_msl")
    arrival_plan.pop("selected_pattern_altitude_source")
    destination_snapshot = payload["input_data"]["metadata"]["ui_state"]["reference_data_snapshot"][
        "destination_airport"
    ]
    destination_snapshot["pattern_altitude_ft_msl"] = master_pattern
    snapshot_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    restored = repository.load_snapshot(saved.id, UUID(snapshot_path.stem))

    restored_arrival = restored.calculation_results.arrival_altitude
    assert restored_arrival is not None
    assert restored_arrival.rule_version == "CAC_REV19_8_4_9_V4"
    assert restored_arrival.selected_pattern_altitude_ft_msl == 1000
    assert restored_arrival.selected_pattern_altitude_source == expected_source
    restored_state = projects.ui_state(restored.input_data)
    assert restored_state.arrival_plan is not None
    assert restored_state.arrival_plan.selected_pattern_altitude_ft_msl == 1000
    assert restored_state.arrival_plan.selected_pattern_altitude_source == expected_source
