from __future__ import annotations

import pytest

from autonavlog.domain.enums import Pa500Policy
from autonavlog.nav.airspeed import (
    cas_from_tas,
    pressure_altitude_planning_ft,
    tas_from_cas,
)
from autonavlog.nav.geodesy import (
    _geodesic_distance_nm_and_initial_true_course_deg,
    _points_along_leg,
    geodesic_leg,
    point_along_leg,
    point_along_route,
)
from autonavlog.nav.rounding import DisplayRoundingPolicy, round_half_up
from autonavlog.nav.wind_triangle import WindTriangleError, solve_wind_triangle


def test_wgs84_distance_course_and_route_projection() -> None:
    leg = geodesic_leg(31.877, 131.449, 33.479, 131.737)
    assert 96 < leg.distance_nm < 98
    assert 7 < leg.initial_true_course_deg < 10
    projection = point_along_route(
        [(31.877, 131.449), (32.45, 131.55), (33.479, 131.737)],
        20,
    )
    assert projection is not None
    assert projection.section_index == 0


@pytest.mark.parametrize(
    ("origin", "course_deg", "distances_nm"),
    [
        ((0.0, 0.0), 0.0, (0.0, 0.5, 12.5)),
        ((31.8787, 131.4374), 257.4, (0.0, 0.25, 8.0, 30.0)),
        ((0.0, 179.9), 90.0, (0.0, 3.0, 12.0, 24.0)),
        ((-5.0, -45.0), 315.0, (0.0, 1.0, 15.0, 60.0)),
    ],
)
def test_points_along_leg_matches_point_along_leg_parity(
    origin: tuple[float, float],
    course_deg: float,
    distances_nm: tuple[float, ...],
) -> None:
    batch = _points_along_leg(
        origin[0],
        origin[1],
        course_deg,
        distances_nm,
    )
    direct = tuple(
        point_along_leg(
            origin[0],
            origin[1],
            course_deg,
            distance_nm,
        )
        for distance_nm in distances_nm
    )

    assert len(batch) == len(direct)
    for actual, expected in zip(batch, direct, strict=True):
        assert actual[0] == pytest.approx(expected[0], abs=1e-12)
        assert actual[1] == pytest.approx(expected[1], abs=1e-12)


def test_points_along_leg_matches_leg_endpoints_and_midpoint() -> None:
    start = (31.877, 131.449)
    end = (33.479, 131.737)
    leg = geodesic_leg(start[0], start[1], end[0], end[1])
    distances_nm = (0.0, leg.distance_nm / 2.0, leg.distance_nm)

    points = _points_along_leg(
        start[0],
        start[1],
        leg.initial_true_course_deg,
        distances_nm,
    )

    assert points[0][0] == pytest.approx(start[0], abs=1e-12)
    assert points[0][1] == pytest.approx(start[1], abs=1e-12)
    assert points[1][0] == pytest.approx(leg.midpoint_latitude_deg, abs=1e-12)
    assert points[1][1] == pytest.approx(leg.midpoint_longitude_deg, abs=1e-12)
    assert points[2][0] == pytest.approx(end[0], abs=1e-9)
    assert points[2][1] == pytest.approx(end[1], abs=1e-9)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ((31.877, 131.449), (33.479, 131.737)),
        ((0.0, 179.9), (0.0, -179.9)),
        ((-5.0, -45.0), (12.0, -12.0)),
        ((31.8787, 131.4374), (31.75, 130.82)),
    ],
)
def test_geodesic_distance_course_helper_matches_geodesic_leg(
    start: tuple[float, float],
    end: tuple[float, float],
) -> None:
    distance_nm, initial_true_course_deg = _geodesic_distance_nm_and_initial_true_course_deg(
        start[0],
        start[1],
        end[0],
        end[1],
    )
    leg = geodesic_leg(start[0], start[1], end[0], end[1])

    assert distance_nm == pytest.approx(leg.distance_nm, abs=1e-12)
    assert initial_true_course_deg == pytest.approx(leg.initial_true_course_deg, abs=1e-12)


@pytest.mark.parametrize(
    ("course", "wind_from", "expected_sign"),
    [(0, 90, 1), (90, 0, -1), (180, 270, 1), (270, 180, -1)],
)
def test_wind_triangle_four_quadrants(
    course: float,
    wind_from: float,
    expected_sign: int,
) -> None:
    result = solve_wind_triangle(course, 150, wind_from, 20)
    assert result.wca_deg * expected_sign > 0
    assert result.ground_speed_kt > 0


def test_calm_and_invalid_crosswind() -> None:
    calm = solve_wind_triangle(123, 150, None, 0)
    assert calm.calm and calm.wca_deg == 0 and calm.ground_speed_kt == 150
    with pytest.raises(WindTriangleError):
        solve_wind_triangle(0, 50, 90, 50)


def test_pressure_altitude_planning_policy() -> None:
    assert pressure_altitude_planning_ft(1234, Pa500Policy.CEILING) == 1500
    assert pressure_altitude_planning_ft(1234, Pa500Policy.NEAREST) == 1000
    assert pressure_altitude_planning_ft(1234, Pa500Policy.FLOOR) == 1000


def test_compressible_cas_tas_round_trip() -> None:
    tas = 165.0
    cas = cas_from_tas(tas, 8000, 5)
    assert tas_from_cas(cas, 8000, 5) == pytest.approx(tas, rel=1e-10)
    assert cas < tas


def test_half_up_rounding() -> None:
    assert round_half_up(1.25, 0.5) == 1.5
    assert round_half_up(2.25, 0.5) == 2.5
    assert DisplayRoundingPolicy().speed(114.5) == 115
