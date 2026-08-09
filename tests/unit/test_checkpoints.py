from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from autonavlog.application.checkpoints import project_check_points
from autonavlog.domain.enums import (
    FlightPhase,
    RouteNodeRole,
    VisualReferenceRole,
)
from autonavlog.domain.project import (
    NavSection,
    Project,
    RouteNode,
    VisualReference,
)

JST = ZoneInfo("Asia/Tokyo")


def _project(*references: VisualReference) -> Project:
    start = RouteNode(
        sequence=0,
        name="A",
        latitude_deg=0,
        longitude_deg=0,
        role=RouteNodeRole.AIRPORT,
        manual_distance_nm=100,
    )
    end = RouteNode(
        sequence=1,
        name="B",
        latitude_deg=0,
        longitude_deg=1,
        role=RouteNodeRole.DESTINATION,
    )
    section = NavSection(
        sequence=0,
        from_node_id=start.id,
        to_node_id=end.id,
        phase=FlightPhase.CRUISE,
        planned_altitude_ft_msl=5000,
    )
    normalized = [
        reference.model_copy(
            update={"linked_section_id": section.id}
            if reference.linked_section_id is not None
            else {},
        )
        for reference in references
    ]
    return Project(
        name="CP fixture",
        flight_date=date(2026, 8, 10),
        planned_departure_time_jst=datetime(2026, 8, 10, 9, tzinfo=JST),
        departure_airport_id="A",
        destination_airport_id="B",
        total_usable_fuel_gal=81,
        default_variation_deg_east=8,
        route_nodes=[start, end],
        sections=[section],
        visual_references=normalized,
    )


def _cp(
    *,
    latitude: float,
    longitude: float,
    linked: bool = True,
    name: str = "CP",
) -> VisualReference:
    return VisualReference(
        name=name,
        latitude_deg=latitude,
        longitude_deg=longitude,
        role=VisualReferenceRole.CHECK_POINT,
        linked_section_id=(
            # A non-None marker; _project replaces it with the actual Section ID.
            RouteNode(
                sequence=0,
                name="marker",
                latitude_deg=0,
                longitude_deg=0,
                role=RouteNodeRole.ROUTE_POINT,
            ).id
            if linked
            else None
        ),
        along_track_fraction=0.99,
    )


def test_cross_track_checkpoint_projects_to_finite_leg_and_scales_adopted_distance() -> None:
    project = _project(_cp(latitude=1, longitude=0.5))
    computed = project_check_points(project)
    assert computed.issues == ()
    assert len(computed.projections) == 1
    projection = computed.projections[0]
    assert projection.along_track_fraction == pytest.approx(0.5, abs=1e-6)
    assert projection.along_section_distance_nm == pytest.approx(50, abs=1e-4)
    assert projection.cumulative_distance_nm == pytest.approx(50, abs=1e-4)
    assert projection.cross_track_distance_nm == pytest.approx(60.04, rel=0.01)
    assert projection.abeam_latitude_deg == pytest.approx(0, abs=1e-7)


def test_missing_link_is_a_blocker() -> None:
    computed = project_check_points(_project(_cp(latitude=1, longitude=0.5, linked=False)))
    assert computed.projections == ()
    assert {issue.code for issue in computed.issues} == {"CP_LINK_REQUIRED"}


def test_missing_linked_section_endpoint_is_a_blocker_instead_of_key_error() -> None:
    project = _project(_cp(latitude=1, longitude=0.5))
    linked_section = project.sections[0]
    project.route_nodes[:] = [
        node for node in project.route_nodes if node.id != linked_section.to_node_id
    ]

    computed = project_check_points(project)

    assert computed.projections == ()
    assert {issue.code for issue in computed.issues} == {"CP_LINK_REQUIRED"}


def test_endpoint_projection_requires_a_different_leg() -> None:
    computed = project_check_points(_project(_cp(latitude=0, longitude=0)))
    assert computed.projections == ()
    assert {issue.code for issue in computed.issues} == {"CP_NOT_ABEAM_LINKED_SECTION"}


def test_duplicate_stations_within_one_meter_are_rejected() -> None:
    computed = project_check_points(
        _project(
            _cp(latitude=1, longitude=0.5, name="CP1"),
            _cp(latitude=2, longitude=0.5, name="CP2"),
        )
    )
    assert len(computed.projections) == 1
    assert {issue.code for issue in computed.issues} == {"CP_NOT_ABEAM_LINKED_SECTION"}
