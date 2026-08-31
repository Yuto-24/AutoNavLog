from __future__ import annotations

from math import cos, hypot, radians

import pytest

from autonavlog.application.rjfm_inbound_geometry import (
    GeometryUnsupportedError,
    _segment_intersects_polygon,
    _xy,
    geometry_supported,
    polyline_boundary_metrics,
    sample_geodesic_points,
)
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
from autonavlog.storage.rjfm_inbound_reference import GeoPoint


def _point_on_course(origin: GeoPoint, course_deg: float, distance_nm: float) -> GeoPoint:
    latitude_deg, longitude_deg = point_along_leg(
        origin.latitude_deg, origin.longitude_deg, course_deg, distance_nm
    )
    return GeoPoint(latitude_deg, longitude_deg)


def _far_polygon() -> tuple[GeoPoint, ...]:
    return (
        GeoPoint(5.0, 5.0),
        GeoPoint(5.0, 6.0),
        GeoPoint(6.0, 6.0),
        GeoPoint(6.0, 5.0),
    )


def test_geometry_supported_covers_rjfm_and_equatorial_240_nm_routes() -> None:
    rjfm_start = GeoPoint(31.8787, 131.4374)
    rjfm_end = _point_on_course(rjfm_start, 257.0, 240.0)
    equator_start = GeoPoint(0.0, 0.0)
    equator_end = _point_on_course(equator_start, 270.0, 240.0)

    assert geometry_supported(sample_geodesic_points(rjfm_start, rjfm_end))
    assert geometry_supported(sample_geodesic_points(equator_start, equator_end))


def test_geometry_supported_rejects_antimeridian_short_geodesic() -> None:
    # WGS84 sees this as a short 0.108 NM leg, but raw longitude chords
    # would span almost the whole world and are outside the local model.
    points = (GeoPoint(0.0, 179.999), GeoPoint(0.0, -179.999))

    assert not geometry_supported(points)


def test_geometry_supported_rejects_unsampled_long_segments_and_outside_latitudes() -> None:
    assert not geometry_supported((GeoPoint(0.0, 0.0), GeoPoint(0.0, 1.0)))
    assert not geometry_supported((GeoPoint(-11.0, 0.0), GeoPoint(-11.0, 0.001)))
    assert not geometry_supported((GeoPoint(46.0, 0.0), GeoPoint(46.0, 0.001)))


def test_sample_geodesic_points_rejects_leg_over_240_nm() -> None:
    with pytest.raises(GeometryUnsupportedError, match="240 NM"):
        sample_geodesic_points(GeoPoint(0.0, 0.0), GeoPoint(0.0, 5.0))


def test_polyline_boundary_metrics_counts_a_direct_tangent_as_intersection() -> None:
    points = sample_geodesic_points(
        GeoPoint(0.0, 0.0), _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0)
    )
    boundary = (
        GeoPoint(-0.05, -0.55),
        GeoPoint(-0.05, -0.45),
        GeoPoint(0.05, -0.45),
        GeoPoint(0.05, -0.55),
    )

    intersects, clearance_nm = polyline_boundary_metrics(points, boundary)

    assert intersects
    assert clearance_nm == 0.0


def test_between_sample_geodesic_tangent_is_blocked_by_conservative_guard() -> None:
    start = GeoPoint(31.8787, 131.4374)
    end = _point_on_course(start, 257.0, 30.0)
    points = sample_geodesic_points(start, end)
    first, second = points[0], points[1]
    leg = geodesic_leg(
        first.latitude_deg,
        first.longitude_deg,
        second.latitude_deg,
        second.longitude_deg,
    )
    midpoint_latitude, midpoint_longitude = point_along_leg(
        first.latitude_deg,
        first.longitude_deg,
        leg.initial_true_course_deg,
        leg.distance_nm / 2.0,
    )
    midpoint = GeoPoint(midpoint_latitude, midpoint_longitude)
    reference_latitude = (first.latitude_deg + second.latitude_deg + midpoint.latitude_deg) / 3.0
    ax, ay = _xy(first, reference_latitude)
    bx, by = _xy(second, reference_latitude)
    mx, my = _xy(midpoint, reference_latitude)
    chord_length = hypot(bx - ax, by - ay)
    signed_offset = ((bx - ax) * (my - ay) - (by - ay) * (mx - ax)) / chord_length
    normal_x, normal_y = -(by - ay) / chord_length, (bx - ax) / chord_length
    half_width = abs(signed_offset) * 0.25
    along_width = 0.0001
    corners: list[GeoPoint] = []
    for along, normal in ((-along_width, -half_width), (along_width, -half_width),
                          (along_width, half_width), (-along_width, half_width)):
        x = mx + (bx - ax) / chord_length * along + normal_x * normal
        y = my + (by - ay) / chord_length * along + normal_y * normal
        corners.append(GeoPoint(y / 60.0, x / (60.0 * cos(radians(reference_latitude)))))
    boundary = tuple(corners)

    assert not _segment_intersects_polygon(first, second, boundary)
    intersects, clearance_nm = polyline_boundary_metrics(points, boundary)

    assert intersects
    assert clearance_nm == 0.0


def test_polyline_boundary_metrics_returns_guarded_positive_clearance() -> None:
    points = sample_geodesic_points(
        GeoPoint(0.0, 0.0), _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0)
    )

    intersects, clearance_nm = polyline_boundary_metrics(points, _far_polygon())

    assert not intersects
    assert clearance_nm > 0.0


def test_polyline_boundary_metrics_rejects_unsupported_polygon_latitude() -> None:
    points = sample_geodesic_points(
        GeoPoint(0.0, 0.0), _point_on_course(GeoPoint(0.0, 0.0), 270.0, 5.0)
    )
    boundary = (
        GeoPoint(46.0, 0.0),
        GeoPoint(46.0, 0.01),
        GeoPoint(46.01, 0.0),
    )

    with pytest.raises(GeometryUnsupportedError, match="boundary polygon"):
        polyline_boundary_metrics(points, boundary)

def test_polyline_boundary_metrics_rejects_antimeridian_combined_extent() -> None:
    points = sample_geodesic_points(
        GeoPoint(0.0, 0.0), _point_on_course(GeoPoint(0.0, 0.0), 270.0, 5.0)
    )
    boundary = (
        GeoPoint(0.0, 179.9),
        GeoPoint(0.01, 179.9),
        GeoPoint(0.0, 179.8),
    )

    with pytest.raises(GeometryUnsupportedError, match="local domain"):
        polyline_boundary_metrics(points, boundary)
