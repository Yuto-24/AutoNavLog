from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from autonavlog.application.rjfm_coordinate_matcher import TRIGGER_TOLERANCE_NM
from autonavlog.application.rjfm_inbound_plan import (
    RJFM_INPUT_MODE_OMARU_TO_UMK_FIXED,
    RjfmInboundReferences,
    apply_rjfm_inbound_exception,
    rjfm_inbound_plan_matches_project,
    rjfm_inbound_section_input_modes,
)
from autonavlog.domain.enums import FlightPhase, RouteNodeNameSource, RouteNodeRole
from autonavlog.domain.planning import RjfmCoordinate, load_persisted_ui_state
from autonavlog.domain.project import NavSection, Project, RouteNode
from autonavlog.nav.geodesy import point_along_leg

JST = ZoneInfo("Asia/Tokyo")


def _point(name: str, latitude: float, longitude: float) -> RjfmCoordinate:
    return RjfmCoordinate(
        latitude_deg=latitude,
        longitude_deg=longitude,
        source=f"fixture:{name}",
        estimated_error_nm=0.2,
    )


def _project(*, destination: str = "RJFM", nonadjacent: bool = False) -> Project:
    points = [
        ("ORIGIN", 33.0, 131.6, RouteNodeRole.AIRPORT),
        ("renamed OMARU", 32.22, 131.55, RouteNodeRole.ROUTE_POINT),
        *(
            [
                ("INTERMEDIATE", 32.15, 131.525, RouteNodeRole.ROUTE_POINT),
            ]
            if nonadjacent
            else []
        ),
        ("renamed UMK", 32.08, 131.50, RouteNodeRole.ROUTE_POINT),
        ("ARITA", 31.97, 131.48, RouteNodeRole.VISUAL_REPORTING_POINT),
        (destination, 31.877, 131.449, RouteNodeRole.DESTINATION),
    ]
    nodes = [
        RouteNode(sequence=i, name=name, latitude_deg=lat, longitude_deg=lon, role=role)
        for i, (name, lat, lon, role) in enumerate(points)
    ]
    sections = [
        NavSection(
            sequence=i,
            from_node_id=a.id,
            to_node_id=b.id,
            phase=(
                FlightPhase.VISUAL_ARRIVAL
                if i == len(nodes) - 2
                else FlightPhase.DESCENT
                if i >= 2
                else FlightPhase.CRUISE
            ),
            planned_altitude_ft_msl=5500,
        )
        for i, (a, b) in enumerate(zip(nodes, nodes[1:], strict=False))
    ]
    return Project(
        name="inbound",
        flight_date=date(2026, 8, 31),
        planned_departure_time_jst=datetime(2026, 8, 31, 9, tzinfo=JST),
        departure_airport_id="RJFO",
        destination_airport_id=destination,
        total_usable_fuel_gal=81,
        default_variation_deg_east=-8,
        route_nodes=nodes,
        sections=sections,
    )


def _references() -> RjfmInboundReferences:
    return RjfmInboundReferences(
        revision="fixture-r1",
        content_fingerprint="b" * 64,
        omaru=_point("OMARU", 32.22, 131.55),
        umk=_point("UMK", 32.08, 131.50),
    )


def test_inbound_coordinate_order_locks_only_physical_omaru_to_umk() -> None:
    project = _project()
    original = project.ordered_sections()[1].model_copy(deep=True)

    plan = apply_rjfm_inbound_exception(project, _references(), adopted_vrep_altitude_ft_msl=1900)

    assert plan is not None
    assert plan.adopted_vrep_altitude_ft_msl == 1900
    assert project.ordered_sections()[1].planned_altitude_ft_msl == 4500
    assert project.ordered_sections()[1].phase is FlightPhase.CRUISE
    assert rjfm_inbound_plan_matches_project(project, plan)
    assert rjfm_inbound_section_input_modes(project, plan) == {
        str(original.id): RJFM_INPUT_MODE_OMARU_TO_UMK_FIXED
    }
    state = load_persisted_ui_state(project.metadata["ui_state"])
    assert state.rjfm_inbound_plan == plan
    project.descent_rate_fpm = 1000
    assert not rjfm_inbound_plan_matches_project(project, plan)
    project.descent_rate_fpm = 500
    project.route_nodes[2].latitude_deg += 0.02
    assert not rjfm_inbound_plan_matches_project(project, plan)


def test_inbound_requires_rjfm_and_ordered_final_vrep() -> None:
    assert apply_rjfm_inbound_exception(_project(destination="RJFO"), _references()) is None
    project = _project()
    project.route_nodes[1], project.route_nodes[2] = project.route_nodes[2], project.route_nodes[1]
    for index, node in enumerate(project.route_nodes):
        node.sequence = index
    assert apply_rjfm_inbound_exception(project, _references()) is None


def test_inbound_supports_ordered_nonadjacent_omaru_to_umk() -> None:
    project = _project(nonadjacent=True)
    plan = apply_rjfm_inbound_exception(
        project,
        _references(),
        adopted_vrep_altitude_ft_msl=1900,
    )

    assert plan is not None
    assert len(plan.controlled_section_ids) == 2
    assert rjfm_inbound_plan_matches_project(project, plan)
    assert set(rjfm_inbound_section_input_modes(project, plan)) == set(
        map(str, plan.controlled_section_ids)
    )
    assert all(
        section.phase is FlightPhase.CRUISE and section.planned_altitude_ft_msl == 4500
        for section in project.ordered_sections()[1:3]
    )


@pytest.mark.parametrize(
    ("offset_nm", "matches"),
    [
        (TRIGGER_TOLERANCE_NM - 0.01, True),
        (TRIGGER_TOLERANCE_NM + 0.01, False),
    ],
)
def test_inbound_coordinate_trigger_boundary(offset_nm: float, matches: bool) -> None:
    project = _project()
    latitude, longitude = point_along_leg(32.22, 131.55, 0.0, offset_nm)
    project.route_nodes[1].latitude_deg = latitude
    project.route_nodes[1].longitude_deg = longitude

    plan = apply_rjfm_inbound_exception(
        project,
        _references(),
        adopted_vrep_altitude_ft_msl=1900,
    )

    assert (plan is not None) is matches


def test_inbound_trigger_ignores_user_renamed_reference_nodes() -> None:
    project = _project()
    project.route_nodes[1].name = "USER LABEL"
    project.route_nodes[1].name_source = RouteNodeNameSource.USER

    plan = apply_rjfm_inbound_exception(
        project,
        _references(),
        adopted_vrep_altitude_ft_msl=1900,
    )

    assert plan is not None
    assert project.route_nodes[1].name == "USER LABEL"
    assert project.route_nodes[1].name_source is RouteNodeNameSource.USER


@pytest.mark.parametrize("missing", ["OMARU", "UMK", "VREP"])
def test_inbound_missing_required_reference_node_does_not_apply(missing: str) -> None:
    project = _project()
    if missing == "VREP":
        node = next(
            node
            for node in project.route_nodes
            if node.role is RouteNodeRole.VISUAL_REPORTING_POINT
        )
        node.role = RouteNodeRole.ROUTE_POINT
    else:
        node = next(node for node in project.route_nodes if node.name == f"renamed {missing}")
        node.latitude_deg = 0.0
        node.longitude_deg = 0.0

    assert apply_rjfm_inbound_exception(project, _references()) is None


def test_inbound_wrong_reference_order_with_coherent_sections_does_not_apply() -> None:
    project = _project()
    omaru = project.route_nodes[1]
    umk = project.route_nodes[2]
    omaru.latitude_deg, umk.latitude_deg = umk.latitude_deg, omaru.latitude_deg
    omaru.longitude_deg, umk.longitude_deg = umk.longitude_deg, omaru.longitude_deg
    ordered_nodes = project.ordered_nodes()
    for index, node in enumerate(ordered_nodes):
        node.sequence = index
    ordered_nodes = project.ordered_nodes()
    for index, section in enumerate(project.ordered_sections()):
        section.sequence = index
        section.from_node_id = ordered_nodes[index].id
        section.to_node_id = ordered_nodes[index + 1].id

    assert apply_rjfm_inbound_exception(project, _references()) is None
