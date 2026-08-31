from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.rjfm_inbound_plan import (
    RjfmInboundReferences,
    apply_rjfm_inbound_exception,
)
from autonavlog.domain.enums import (
    AdoptedSource,
    FlightPhase,
    RouteNodeRole,
    VisualReferenceRole,
)
from autonavlog.domain.planning import (
    AirportSelection,
    ArrivalAltitudeMode,
    ArrivalPlan,
    PatternAltitudeValidationStatus,
    PersistedUiState,
    ReferenceDataSnapshot,
    RjfmCoordinate,
    load_persisted_ui_state,
)
from autonavlog.domain.project import NavSection, Project, RouteNode, VisualReference
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
from autonavlog.nav.variation import variation_for_departure_latitude
from autonavlog.nav.wind_triangle import solve_wind_triangle
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.storage.airports import AirportRepository
from autonavlog.weather.fake_provider import FakeWeatherProvider

JST = ZoneInfo("Asia/Tokyo")


def _selection(airport_id: str, latitude: float, longitude: float) -> AirportSelection:
    return AirportSelection(
        id=airport_id,
        icao=airport_id,
        name=airport_id,
        latitude_deg=latitude,
        longitude_deg=longitude,
        elevation_ft_msl=20.0,
        pattern_altitude_ft_msl=1000.0,
        pattern_altitude_source="fixture",
        pattern_altitude_source_revision="fixture-v1",
        pattern_altitude_validation_status=PatternAltitudeValidationStatus.VERIFIED,
        source="fixture",
        source_revision="fixture-v1",
    )


def _inbound_project(
    manual_vrep_altitude: int | None = None,
    nonadjacent_omaru_umk: bool = False,
) -> tuple[Project, RjfmInboundReferences]:
    origin = RouteNode(
        sequence=0,
        name="RJFO",
        latitude_deg=33.479,
        longitude_deg=131.737,
        role=RouteNodeRole.AIRPORT,
    )
    omaru = RouteNode(
        sequence=1,
        name="renamed OMARU",
        latitude_deg=32.60,
        longitude_deg=131.62,
        role=RouteNodeRole.ROUTE_POINT,
    )
    umk = RouteNode(
        sequence=2,
        name="renamed UMK",
        latitude_deg=32.20,
        longitude_deg=131.50,
        role=RouteNodeRole.ROUTE_POINT,
    )
    middle = RouteNode(
        sequence=3,
        name="MID",
        latitude_deg=32.00,
        longitude_deg=131.45,
        role=RouteNodeRole.ROUTE_POINT,
    )
    destination = RouteNode(
        sequence=5,
        name="RJFM",
        latitude_deg=31.877,
        longitude_deg=131.449,
        role=RouteNodeRole.DESTINATION,
    )
    to_destination = geodesic_leg(
        destination.latitude_deg,
        destination.longitude_deg,
        middle.latitude_deg,
        middle.longitude_deg,
    )
    vrep_lat, vrep_lon = point_along_leg(
        destination.latitude_deg,
        destination.longitude_deg,
        to_destination.initial_true_course_deg,
        5.0,
    )
    vrep = RouteNode(
        sequence=4,
        name="VREP",
        latitude_deg=vrep_lat,
        longitude_deg=vrep_lon,
        role=RouteNodeRole.VISUAL_REPORTING_POINT,
    )
    if nonadjacent_omaru_umk:
        bridge = RouteNode(
            sequence=2,
            name="BRIDGE",
            latitude_deg=32.42,
            longitude_deg=131.59,
            role=RouteNodeRole.ROUTE_POINT,
        )
        umk.sequence = 3
        middle.sequence = 4
        vrep.sequence = 5
        destination.sequence = 6
        nodes = [origin, omaru, bridge, umk, middle, vrep, destination]
        phases = [
            FlightPhase.CRUISE,
            FlightPhase.CRUISE,
            FlightPhase.CRUISE,
            FlightPhase.DESCENT,
            FlightPhase.DESCENT,
            FlightPhase.VISUAL_ARRIVAL,
        ]
    else:
        nodes = [origin, omaru, umk, middle, vrep, destination]
        phases = [
            FlightPhase.CRUISE,
            FlightPhase.CRUISE,
            FlightPhase.DESCENT,
            FlightPhase.DESCENT,
            FlightPhase.VISUAL_ARRIVAL,
        ]
    sections = [
        NavSection(
            sequence=index,
            from_node_id=start.id,
            to_node_id=end.id,
            phase=phase,
            planned_altitude_ft_msl=4500.0 if index >= 2 else 5500.0,
        )
        for index, (phase, (start, end)) in enumerate(
            zip(phases, zip(nodes, nodes[1:], strict=False), strict=True)
        )
    ]
    project = Project(
        name="RJFM inbound fixture",
        flight_date=date(2026, 8, 31),
        planned_departure_time_jst=datetime(2026, 8, 31, 9, tzinfo=JST),
        departure_airport_id="RJFO",
        destination_airport_id="RJFM",
        total_usable_fuel_gal=81.0,
        default_variation_deg_east=8.0,
        route_nodes=nodes,
        sections=sections,
    )
    ui_state = PersistedUiState(
        arrival_plan=ArrivalPlan(
            visual_reporting_point_node_id=vrep.id,
            selected_pattern_altitude_ft_msl=1000,
            selected_pattern_altitude_source=AdoptedSource.AUTOMATIC,
            altitude_mode=(
                ArrivalAltitudeMode.MANUAL_NON_STANDARD_ENTRY
                if manual_vrep_altitude is not None
                else ArrivalAltitudeMode.STANDARD_DISTANCE_RULE
            ),
            manual_vrep_altitude_ft_msl=manual_vrep_altitude,
            manual_override_reason=("fixture" if manual_vrep_altitude is not None else None),
        ),
        reference_data_snapshot=ReferenceDataSnapshot(
            departure_airport=_selection("RJFO", origin.latitude_deg, origin.longitude_deg),
            destination_airport=_selection(
                "RJFM",
                destination.latitude_deg,
                destination.longitude_deg,
            ),
        ),
    )
    project.metadata["ui_state"] = ui_state.model_dump(mode="json")
    refs = RjfmInboundReferences(
        revision="fixture-rjfm-v1",
        content_fingerprint="b" * 64,
        omaru=RjfmCoordinate(
            latitude_deg=omaru.latitude_deg,
            longitude_deg=omaru.longitude_deg,
            source="fixture:OMARU",
            estimated_error_nm=0.2,
        ),
        umk=RjfmCoordinate(
            latitude_deg=umk.latitude_deg,
            longitude_deg=umk.longitude_deg,
            source="fixture:UMK",
            estimated_error_nm=0.2,
        ),
    )
    return project, refs


def _service(airports: AirportRepository, performance: PerformanceRepository) -> CalculationService:
    return CalculationService(
        airports,
        performance,
        expected_rjfm_reference_revision="fixture-rjfm-v1",
        expected_rjfm_reference_content_fingerprint="b" * 64,
    )


@pytest.mark.parametrize(("descent_rate", "target_altitude"), [(500, 1500), (1000, 1500)])
def test_inbound_operational_profile_preserves_physical_route_and_uses_formula(
    airports: AirportRepository,
    performance_repository: PerformanceRepository,
    descent_rate: int,
    target_altitude: int,
) -> None:
    project, refs = _inbound_project()
    project.descent_rate_fpm = descent_rate
    plan = apply_rjfm_inbound_exception(
        project,
        refs,
        adopted_vrep_altitude_ft_msl=target_altitude,
    )
    assert plan is not None

    outcome = _service(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    inbound = [result for result in outcome.sections if result.phase is FlightPhase.DESCENT]
    assert len(inbound) >= 2
    nodes = {node.id: node for node in project.route_nodes}
    sections_by_id = {section.id: section for section in project.ordered_sections()}
    for result in outcome.sections:
        if result.section_id not in sections_by_id:
            continue
        start = nodes[result.from_node_id]
        end = nodes[result.to_node_id]
        leg = geodesic_leg(
            start.latitude_deg,
            start.longitude_deg,
            end.latitude_deg,
            end.longitude_deg,
        )
        assert result.zone_distance_nm.adopted() == pytest.approx(leg.distance_nm, abs=1e-9)
        assert result.true_course_deg.adopted() == pytest.approx(
            leg.initial_true_course_deg,
            abs=1e-9,
        )
        variation = variation_for_departure_latitude(start.latitude_deg).degrees_east
        assert result.variation_deg_east.adopted() == pytest.approx(variation, abs=1e-9)
        expected_mc = (leg.initial_true_course_deg + variation) % 360
        actual_mc = result.magnetic_course_deg.adopted()
        assert abs((actual_mc - expected_mc + 180) % 360 - 180) < 1e-9
        expected_gs = solve_wind_triangle(
            leg.initial_true_course_deg,
            result.tas_kt.adopted(),
            result.wind_direction_deg_from.adopted(),
            result.wind_speed_kt.adopted(),
        ).ground_speed_kt
        assert result.ground_speed_kt.adopted() == pytest.approx(expected_gs, abs=1e-9)
    total_ete = sum(result.zone_ete_seconds.adopted() or 0.0 for result in inbound)
    assert total_ete == pytest.approx((4500 - target_altitude) / descent_rate * 60 + 60, abs=1e-6)
    assert sum(result.section_fuel_gal.adopted() or 0.0 for result in inbound) == pytest.approx(
        total_ete * 12 / 3600,
        abs=1e-9,
    )
    first_display = next(
        row
        for row in outcome.display_rows
        if row.row_type == "CALCULATION_ZONE"
        and row.phase is FlightPhase.DESCENT
        and row.source_result_sequence == inbound[0].sequence
    )
    expected_first_altitude = max(
        target_altitude,
        4500 - inbound[0].zone_ete_seconds.adopted() / 60 * descent_rate,
    )
    assert float((first_display.pa.text or "0").strip("()")) == pytest.approx(
        expected_first_altitude,
        abs=1.0,
    )
    assert inbound[0].from_name.startswith("UMK")
    assert all("EOC" not in result.from_name for result in outcome.sections[:2])
    assert all(
        point.type.value != "EOC"
        or point.along_route_distance_nm
        >= (sum((item.zone_distance_nm.adopted() or 0.0) for item in outcome.sections[:2]))
        for point in outcome.derived_points
    )
    assert [
        item.to_node_id
        for item in outcome.sections
        if item.to_node_id in {node.id for node in project.route_nodes}
    ]  # all endpoints remain physical route nodes
    assert len(outcome.derived_points) <= 1  # inbound does not add virtual route points


def test_inbound_display_ete_children_sum_to_once_rounded_profile_total(
    airports: AirportRepository,
    performance_repository: PerformanceRepository,
) -> None:
    project, refs = _inbound_project()
    assert (
        apply_rjfm_inbound_exception(
            project,
            refs,
            adopted_vrep_altitude_ft_msl=1500,
        )
        is not None
    )
    outcome = _service(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )

    children = [
        row
        for row in outcome.display_rows
        if row.row_type == "CALCULATION_ZONE" and row.phase is FlightPhase.DESCENT
    ]
    summary = [
        row
        for row in outcome.display_rows
        if row.row_type == "PHYSICAL_LEG_SUMMARY" and row.phase is FlightPhase.DESCENT
    ]
    child_minutes = sum(float(row.ete.text or "nan") for row in children)
    total_exact = sum(
        result.zone_ete_seconds.adopted() or 0.0
        for result in outcome.sections
        if result.phase is FlightPhase.DESCENT
    )
    expected_minutes = round(total_exact / 60 * 2) / 2
    assert child_minutes == pytest.approx(expected_minutes, abs=1e-9)
    assert sum(float(row.ete.text.split(" / ", 1)[0]) for row in summary) == pytest.approx(
        expected_minutes,
        abs=1e-9,
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda project: setattr(project, "descent_rate_fpm", 1000), "ROUTE_INPUT_MISMATCH"),
        (
            lambda project: project.route_nodes[1].__setattr__("latitude_deg", 32.61),
            "ROUTE_INPUT_MISMATCH",
        ),
    ],
)
def test_inbound_stale_route_or_rate_is_blocker(
    airports: AirportRepository,
    performance_repository: PerformanceRepository,
    mutation,
    reason: str,
) -> None:
    project, refs = _inbound_project()
    plan = apply_rjfm_inbound_exception(project, refs, adopted_vrep_altitude_ft_msl=1500)
    assert plan is not None
    mutation(project)
    outcome = _service(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    stale = [issue for issue in outcome.issues if issue.code == "RJFM_INBOUND_PLAN_STALE"]
    assert len(stale) == 1
    assert stale[0].metadata["reason"] == reason
    assert all(
        result.performance_metadata.get("type") != "rjfm_inbound_operational_descent"
        for result in outcome.sections
    )


def test_inbound_stale_reference_hash_and_adopted_altitude_are_blockers(
    airports: AirportRepository,
    performance_repository: PerformanceRepository,
) -> None:
    project, refs = _inbound_project()
    plan = apply_rjfm_inbound_exception(project, refs, adopted_vrep_altitude_ft_msl=1500)
    assert plan is not None
    state = load_persisted_ui_state(project.metadata["ui_state"])
    project.metadata["ui_state"] = state.model_copy(
        update={
            "rjfm_inbound_plan": plan.model_copy(
                update={"reference_content_fingerprint": "c" * 64},
            )
        }
    ).model_dump(mode="json")
    outcome = _service(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    stale = [issue for issue in outcome.issues if issue.code == "RJFM_INBOUND_PLAN_STALE"]
    assert stale[0].metadata["reason"] == "REFERENCE_IDENTITY_MISMATCH"
    assert outcome.rjfm_inbound_guidance is None

    project, refs = _inbound_project()
    plan = apply_rjfm_inbound_exception(project, refs, adopted_vrep_altitude_ft_msl=1500)
    assert plan is not None
    state = load_persisted_ui_state(project.metadata["ui_state"])
    project.metadata["ui_state"] = state.model_copy(
        update={
            "rjfm_inbound_plan": plan.model_copy(
                update={"adopted_vrep_altitude_ft_msl": 1700},
            )
        }
    ).model_dump(mode="json")
    outcome = _service(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    stale = [issue for issue in outcome.issues if issue.code == "RJFM_INBOUND_PLAN_STALE"]
    assert stale[0].metadata["reason"] == "ARRIVAL_ALTITUDE_MISMATCH"


@pytest.mark.parametrize("descent_rate", [500, 1000])
def test_inbound_manual_vrep_altitude_is_authoritative(
    airports: AirportRepository,
    performance_repository: PerformanceRepository,
    descent_rate: int,
) -> None:
    project, refs = _inbound_project(manual_vrep_altitude=1700)
    project.descent_rate_fpm = descent_rate
    assert (
        apply_rjfm_inbound_exception(
            project,
            refs,
            adopted_vrep_altitude_ft_msl=1700,
        )
        is not None
    )
    outcome = _service(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    assert not outcome.blockers
    assert outcome.arrival_altitude is not None
    assert outcome.arrival_altitude.adopted_altitude_ft_msl == 1700
    inbound = [result for result in outcome.sections if result.phase is FlightPhase.DESCENT]
    assert sum(result.zone_ete_seconds.adopted() or 0.0 for result in inbound) == pytest.approx(
        (4500 - 1700) / descent_rate * 60 + 60,
        abs=1e-6,
    )


def test_inbound_nonadjacent_omaru_umk_calculates_all_controlled_links(
    airports: AirportRepository,
    performance_repository: PerformanceRepository,
) -> None:
    project, refs = _inbound_project(nonadjacent_omaru_umk=True)
    plan = apply_rjfm_inbound_exception(
        project,
        refs,
        adopted_vrep_altitude_ft_msl=1500,
    )
    assert plan is not None
    assert len(plan.controlled_section_ids) == 2

    outcome = _service(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    controlled = [
        result
        for result in outcome.sections
        if result.section_id in set(plan.controlled_section_ids)
    ]
    assert len(controlled) == 2
    assert all(
        result.planned_altitude_ft_msl.adopted() == pytest.approx(4500) for result in controlled
    )
    assert all(
        result.performance_metadata.get("rjfm_inbound_fixed_altitude") is True
        for result in controlled
    )


def test_inbound_checkpoint_segmentation_preserves_exact_operational_sum(
    airports: AirportRepository, performance_repository: PerformanceRepository,
) -> None:
    project, refs = _inbound_project()
    section = project.ordered_sections()[2]
    nodes = {node.id: node for node in project.route_nodes}
    start = nodes[section.from_node_id]
    end = nodes[section.to_node_id]
    leg = geodesic_leg(
        start.latitude_deg, start.longitude_deg,
        end.latitude_deg, end.longitude_deg,
    )
    cp_lat, cp_lon = point_along_leg(
        start.latitude_deg, start.longitude_deg,
        leg.initial_true_course_deg, leg.distance_nm * 0.5,
    )
    project.visual_references.append(
        VisualReference(
            name="INBOUND CP",
            latitude_deg=cp_lat,
            longitude_deg=cp_lon,
            role=VisualReferenceRole.CHECK_POINT,
            linked_section_id=section.id,
        )
    )
    assert apply_rjfm_inbound_exception(
        project, refs, adopted_vrep_altitude_ft_msl=1500,
    ) is not None

    outcome = _service(airports, performance_repository).calculate(
        project, FakeWeatherProvider(),
    )

    assert not outcome.blockers
    assert any(result.to_name == "CP:INBOUND CP" for result in outcome.sections)
    inbound = [
        result for result in outcome.sections
        if result.performance_metadata.get("type") == "rjfm_inbound_operational_descent"
    ]
    assert len(inbound) >= 3
    assert sum(result.zone_ete_seconds.adopted() or 0.0 for result in inbound) == pytest.approx(
        (4500 - 1500) / 500 * 60 + 60, abs=1e-6,
    )
