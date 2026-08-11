from __future__ import annotations

import pytest

from autonavlog.domain.enums import Pa500Policy
from autonavlog.nav.airspeed import (
    cas_from_tas,
    pressure_altitude_exact_ft,
    pressure_altitude_planning_ft,
    tas_from_cas,
)
from autonavlog.nav.geodesy import geodesic_leg, point_along_route
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


def test_pressure_altitude_and_planning_policy() -> None:
    assert pressure_altitude_exact_ft(1234, 1013.25) == pytest.approx(1234)
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
