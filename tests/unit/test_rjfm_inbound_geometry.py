from __future__ import annotations

from math import ceil, cos, hypot, radians
from random import Random

import pytest

import autonavlog.application.rjfm_inbound_geometry as geometry_module
from autonavlog.application.rjfm_inbound_geometry import (
    _BOUNDARY_BBOX_EPSILON_LAT_DEG,
    _SAMPLED_ROUTE_SENTINEL,
    GeometryUnsupportedError,
    _chord_clearance_guard_nm,
    _KnownValidSampledRoute,
    _normalized_polygon,
    _point_in_polygon,
    _polyline_boundary_metrics_for_sampled_route,
    _prepare_polygon,
    _sampled_geodesic_route,
    _segment_bbox_overlaps_polygon_bbox,
    _segment_clearance_nm,
    _segment_distance_nm,
    _segment_intersects_polygon,
    _segments_intersect,
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


def _exhaustive_segment_intersects_polygon(
    start: GeoPoint, end: GeoPoint, polygon: tuple[GeoPoint, ...]
) -> bool:
    return (
        _point_in_polygon(start, polygon)
        or _point_in_polygon(end, polygon)
        or any(
            _segments_intersect(start, end, edge_start, edge_end)
            for edge_start, edge_end in zip(polygon, polygon[1:] + polygon[:1], strict=True)
        )
    )


def _exhaustive_polyline_boundary_metrics(
    points: tuple[GeoPoint, ...],
    polygon: tuple[GeoPoint, ...],
    *,
    boundary_model_error_nm: float = 0.0,
) -> tuple[bool, float]:
    normalized_polygon = _normalized_polygon(polygon)
    route_distances = tuple(
        geodesic_leg(
            start.latitude_deg,
            start.longitude_deg,
            end.latitude_deg,
            end.longitude_deg,
        ).distance_nm
        for start, end in zip(points, points[1:], strict=False)
    )
    minimum_clearance_nm = float("inf")
    for (start, end), distance_nm in zip(
        zip(points, points[1:], strict=False), route_distances, strict=True
    ):
        if _exhaustive_segment_intersects_polygon(start, end, normalized_polygon):
            return True, 0.0
        raw_clearance_nm = min(
            _segment_distance_nm(start, end, edge_start, edge_end)
            for edge_start, edge_end in zip(
                normalized_polygon,
                normalized_polygon[1:] + normalized_polygon[:1],
                strict=True,
            )
        )
        conservative_clearance_nm = raw_clearance_nm - (
            _chord_clearance_guard_nm(distance_nm) + boundary_model_error_nm
        )
        if conservative_clearance_nm <= 0.0:
            return True, 0.0
        minimum_clearance_nm = min(minimum_clearance_nm, conservative_clearance_nm)
    return False, minimum_clearance_nm


def _exhaustive_segment_clearance_nm(
    start: GeoPoint, end: GeoPoint, polygon: tuple[GeoPoint, ...]
) -> float:
    normalized_polygon = _normalized_polygon(polygon)
    return min(
        _segment_distance_nm(start, end, edge_start, edge_end)
        for edge_start, edge_end in zip(
            normalized_polygon,
            normalized_polygon[1:] + normalized_polygon[:1],
            strict=True,
        )
    )


def _subdivided_rectangle(
    center: GeoPoint,
    *,
    half_width_deg: float,
    half_height_deg: float,
    segments_per_side: int,
) -> tuple[GeoPoint, ...]:
    left = center.longitude_deg - half_width_deg
    right = center.longitude_deg + half_width_deg
    bottom = center.latitude_deg - half_height_deg
    top = center.latitude_deg + half_height_deg
    points: list[GeoPoint] = []
    for index in range(segments_per_side):
        ratio = index / segments_per_side
        points.append(GeoPoint(bottom, left + (right - left) * ratio))
    for index in range(segments_per_side):
        ratio = index / segments_per_side
        points.append(GeoPoint(bottom + (top - bottom) * ratio, right))
    for index in range(segments_per_side):
        ratio = index / segments_per_side
        points.append(GeoPoint(top, right - (right - left) * ratio))
    for index in range(segments_per_side):
        ratio = index / segments_per_side
        points.append(GeoPoint(top - (top - bottom) * ratio, left))
    return tuple(points)


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


def test_sample_geodesic_points_preserves_expected_count_and_intermediate_positions() -> None:
    start = GeoPoint(31.8787, 131.4374)
    end = _point_on_course(start, 257.0, 30.0)
    points = sample_geodesic_points(start, end)
    leg = geodesic_leg(
        start.latitude_deg,
        start.longitude_deg,
        end.latitude_deg,
        end.longitude_deg,
    )
    segment_count = max(1, ceil(leg.distance_nm / 0.5))

    assert len(points) == segment_count + 1
    assert points[0] == start
    assert points[-1] == end
    for index, point in enumerate(points[1:-1], start=1):
        expected = point_along_leg(
            start.latitude_deg,
            start.longitude_deg,
            leg.initial_true_course_deg,
            leg.distance_nm * index / segment_count,
        )
        assert point.latitude_deg == pytest.approx(expected[0], abs=1e-12)
        assert point.longitude_deg == pytest.approx(expected[1], abs=1e-12)


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


def test_segment_intersects_polygon_matches_exhaustive_for_closed_and_reversed_polygon() -> None:
    route = sample_geodesic_points(
        GeoPoint(0.0, 0.0), _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0)
    )
    closed_polygon = (
        GeoPoint(-0.05, -0.55),
        GeoPoint(-0.05, -0.45),
        GeoPoint(0.05, -0.45),
        GeoPoint(0.05, -0.55),
        GeoPoint(-0.05, -0.55),
    )
    reversed_polygon = tuple(reversed(closed_polygon[:-1]))

    for polygon in (_normalized_polygon(closed_polygon), reversed_polygon):
        for start, end in zip(route, route[1:], strict=False):
            assert _segment_intersects_polygon(start, end, polygon) is (
                _exhaustive_segment_intersects_polygon(start, end, polygon)
            )


def test_segment_intersects_polygon_keeps_inside_route_without_edge_candidates() -> None:
    polygon = (
        GeoPoint(-1.0, -1.0),
        GeoPoint(-1.0, 1.0),
        GeoPoint(1.0, 1.0),
        GeoPoint(1.0, -1.0),
    )
    start = GeoPoint(0.0, -0.1)
    end = GeoPoint(0.0, 0.1)

    assert _segment_intersects_polygon(start, end, polygon)
    assert _segment_intersects_polygon(
        start, end, polygon
    ) is _exhaustive_segment_intersects_polygon(start, end, polygon)


def test_segment_intersects_polygon_skips_exact_checks_outside_whole_polygon_bbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polygon = (
        GeoPoint(-0.05, -0.55),
        GeoPoint(-0.05, -0.45),
        GeoPoint(0.05, -0.45),
        GeoPoint(0.05, -0.55),
    )
    prepared_polygon = _prepare_polygon(polygon)
    start = GeoPoint(0.2, -0.8)
    end = GeoPoint(0.2, -0.7)
    exact_checks = 0
    original_segments_intersect = geometry_module._segments_intersect

    def counting_segments_intersect(a: GeoPoint, b: GeoPoint, c: GeoPoint, d: GeoPoint) -> bool:
        nonlocal exact_checks
        exact_checks += 1
        return original_segments_intersect(a, b, c, d)

    monkeypatch.setattr(geometry_module, "_segments_intersect", counting_segments_intersect)

    assert not _segment_bbox_overlaps_polygon_bbox(start, end, prepared_polygon)
    assert not _segment_intersects_polygon(
        start,
        end,
        polygon,
        prepared_polygon=prepared_polygon,
        check_endpoints=False,
    )
    assert exact_checks == 0


def test_segment_intersects_polygon_preserves_tolerance_overlap_near_whole_polygon_bbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polygon = (
        GeoPoint(-0.05, -0.55),
        GeoPoint(-0.05, -0.45),
        GeoPoint(0.05, -0.45),
        GeoPoint(0.05, -0.55),
    )
    prepared_polygon = _prepare_polygon(polygon)
    start = GeoPoint(
        prepared_polygon.max_latitude_deg + (_BOUNDARY_BBOX_EPSILON_LAT_DEG / 2.0),
        -0.54,
    )
    end = GeoPoint(
        prepared_polygon.max_latitude_deg + (_BOUNDARY_BBOX_EPSILON_LAT_DEG / 2.0),
        -0.46,
    )
    exact_checks = 0
    original_segments_intersect = geometry_module._segments_intersect

    def counting_segments_intersect(a: GeoPoint, b: GeoPoint, c: GeoPoint, d: GeoPoint) -> bool:
        nonlocal exact_checks
        exact_checks += 1
        return original_segments_intersect(a, b, c, d)

    monkeypatch.setattr(geometry_module, "_segments_intersect", counting_segments_intersect)

    assert _segment_bbox_overlaps_polygon_bbox(start, end, prepared_polygon)
    assert not _segment_intersects_polygon(
        start,
        end,
        polygon,
        prepared_polygon=prepared_polygon,
        check_endpoints=False,
    )
    assert exact_checks > 0


def test_polyline_boundary_metrics_matches_exhaustive_when_route_starts_outside_then_enters() -> (
    None
):
    points = sample_geodesic_points(
        GeoPoint(0.0, 0.0), _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0)
    )
    boundary = (
        GeoPoint(-0.2, -0.35),
        GeoPoint(-0.2, -0.15),
        GeoPoint(0.2, -0.15),
        GeoPoint(0.2, -0.35),
    )

    assert polyline_boundary_metrics(points, boundary) == _exhaustive_polyline_boundary_metrics(
        points, boundary
    )


def test_polyline_boundary_metrics_matches_exhaustive_when_route_starts_inside() -> None:
    points = sample_geodesic_points(
        GeoPoint(0.0, -0.1), _point_on_course(GeoPoint(0.0, -0.1), 270.0, 12.0)
    )
    boundary = (
        GeoPoint(-0.5, -0.5),
        GeoPoint(-0.5, 0.5),
        GeoPoint(0.5, 0.5),
        GeoPoint(0.5, -0.5),
    )

    assert polyline_boundary_metrics(points, boundary) == _exhaustive_polyline_boundary_metrics(
        points, boundary
    )


def test_polyline_boundary_metrics_matches_exhaustive_for_closed_and_reversed_polygon() -> None:
    points = sample_geodesic_points(
        GeoPoint(0.0, 0.0), _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0)
    )
    open_polygon = (
        GeoPoint(-0.05, -0.55),
        GeoPoint(-0.05, -0.45),
        GeoPoint(0.05, -0.45),
        GeoPoint(0.05, -0.55),
    )
    closed_polygon = open_polygon + (open_polygon[0],)
    reversed_polygon = tuple(reversed(open_polygon))

    for polygon in (open_polygon, closed_polygon, reversed_polygon):
        assert polyline_boundary_metrics(points, polygon) == _exhaustive_polyline_boundary_metrics(
            points, polygon
        )


def test_internal_sampled_route_metrics_match_public_for_clear_route_and_guard() -> None:
    origin = GeoPoint(0.0, 0.0)
    sampled_route = _sampled_geodesic_route(origin, _point_on_course(origin, 270.0, 30.0))
    boundary = (
        GeoPoint(0.0003, -0.55),
        GeoPoint(0.0003, -0.45),
        GeoPoint(0.0004, -0.45),
        GeoPoint(0.0004, -0.55),
    )

    assert _polyline_boundary_metrics_for_sampled_route(
        sampled_route,
        boundary,
    ) == polyline_boundary_metrics(sampled_route.points, boundary)
    assert _polyline_boundary_metrics_for_sampled_route(
        sampled_route,
        boundary,
        boundary_model_error_nm=0.02,
    ) == polyline_boundary_metrics(
        sampled_route.points,
        boundary,
        boundary_model_error_nm=0.02,
    )


def test_polyline_boundary_metrics_skips_clearance_for_intersecting_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points = sample_geodesic_points(
        GeoPoint(0.0, 0.0), _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0)
    )
    boundary = (
        GeoPoint(-0.2, -0.35),
        GeoPoint(-0.2, -0.15),
        GeoPoint(0.2, -0.15),
        GeoPoint(0.2, -0.35),
    )
    clearance_calls = 0
    original_segment_clearance_nm = geometry_module._segment_clearance_nm

    def counting_segment_clearance_nm(
        start: GeoPoint,
        end: GeoPoint,
        polygon: tuple[GeoPoint, ...],
        *,
        prepared_polygon: object | None = None,
    ) -> float:
        nonlocal clearance_calls
        clearance_calls += 1
        return original_segment_clearance_nm(
            start,
            end,
            polygon,
            prepared_polygon=prepared_polygon,
        )

    monkeypatch.setattr(geometry_module, "_segment_clearance_nm", counting_segment_clearance_nm)

    assert polyline_boundary_metrics(points, boundary) == _exhaustive_polyline_boundary_metrics(
        points, boundary
    )
    assert clearance_calls == 0


def test_internal_sampled_route_intersection_skips_per_chord_geodesic_inverses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampled_route = _sampled_geodesic_route(
        GeoPoint(0.0, 0.0),
        _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0),
    )
    boundary = (
        GeoPoint(-0.2, -0.35),
        GeoPoint(-0.2, -0.15),
        GeoPoint(0.2, -0.15),
        GeoPoint(0.2, -0.35),
    )
    geodesic_calls = 0
    original_geodesic_leg = geometry_module.geodesic_leg

    def counting_geodesic_leg(*args: float):
        nonlocal geodesic_calls
        geodesic_calls += 1
        return original_geodesic_leg(*args)

    monkeypatch.setattr(geometry_module, "geodesic_leg", counting_geodesic_leg)

    assert _polyline_boundary_metrics_for_sampled_route(
        sampled_route, boundary
    ) == _exhaustive_polyline_boundary_metrics(sampled_route.points, boundary)
    assert geodesic_calls == 0


def test_polyline_boundary_metrics_matches_exhaustive_with_outside_segments_before_intersection(
) -> None:
    points = sample_geodesic_points(
        GeoPoint(0.2, 0.2),
        _point_on_course(GeoPoint(0.2, 0.2), 225.0, 70.0),
    )
    boundary = (
        GeoPoint(-0.2, -0.35),
        GeoPoint(-0.2, -0.15),
        GeoPoint(0.2, -0.15),
        GeoPoint(0.2, -0.35),
    )

    assert polyline_boundary_metrics(points, boundary) == _exhaustive_polyline_boundary_metrics(
        points, boundary
    )


def test_internal_sampled_route_factory_prevents_manual_bypass() -> None:
    with pytest.raises(TypeError, match="_sampled_geodesic_route"):
        _KnownValidSampledRoute(  # type: ignore[call-arg]
            (GeoPoint(0.0, 0.0), GeoPoint(0.0, 1.0)),
            _sentinel=object(),
        )


def test_internal_sampled_route_metrics_reject_invalid_points_like_public_path() -> None:
    invalid_route = _KnownValidSampledRoute(
        (GeoPoint(46.0, 0.0), GeoPoint(46.0, 0.001)),
        _sentinel=_SAMPLED_ROUTE_SENTINEL,
    )
    boundary = (
        GeoPoint(0.0, 0.0),
        GeoPoint(0.0, 0.01),
        GeoPoint(0.01, 0.0),
    )

    with pytest.raises(GeometryUnsupportedError, match="route samples"):
        _polyline_boundary_metrics_for_sampled_route(invalid_route, boundary)


def test_segment_clearance_nm_matches_exhaustive_for_seeded_subdivided_rectangles() -> None:
    random = Random(0)

    for _ in range(20):
        polygon = _subdivided_rectangle(
            GeoPoint(
                random.uniform(-2.0, 2.0),
                random.uniform(-2.0, 2.0),
            ),
            half_width_deg=random.uniform(0.15, 0.6),
            half_height_deg=random.uniform(0.15, 0.6),
            segments_per_side=random.randint(3, 10),
        )
        prepared_polygon = _prepare_polygon(polygon)
        start = GeoPoint(random.uniform(-3.0, 3.0), random.uniform(-3.0, 3.0))
        end = GeoPoint(random.uniform(-3.0, 3.0), random.uniform(-3.0, 3.0))

        assert _segment_clearance_nm(
            start,
            end,
            polygon,
            prepared_polygon=prepared_polygon,
        ) == _exhaustive_segment_clearance_nm(start, end, polygon)


def test_segment_clearance_nm_prunes_far_edges_after_exact_best_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    polygon = _subdivided_rectangle(
        GeoPoint(1.2, 0.0),
        half_width_deg=2.4,
        half_height_deg=0.6,
        segments_per_side=12,
    )
    prepared_polygon = _prepare_polygon(polygon)
    start = GeoPoint(0.0, -0.1)
    end = GeoPoint(0.0, 0.1)
    distance_calls = 0
    original_segment_distance_nm = geometry_module._segment_distance_nm

    def counting_segment_distance_nm(a: GeoPoint, b: GeoPoint, c: GeoPoint, d: GeoPoint) -> float:
        nonlocal distance_calls
        distance_calls += 1
        return original_segment_distance_nm(a, b, c, d)

    monkeypatch.setattr(geometry_module, "_segment_distance_nm", counting_segment_distance_nm)

    clearance_nm = _segment_clearance_nm(
        start,
        end,
        polygon,
        prepared_polygon=prepared_polygon,
    )

    assert clearance_nm == _exhaustive_segment_clearance_nm(start, end, polygon)
    assert 0 < distance_calls < len(prepared_polygon.edges)


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
    for along, normal in (
        (-along_width, -half_width),
        (along_width, -half_width),
        (along_width, half_width),
        (-along_width, half_width),
    ):
        x = mx + (bx - ax) / chord_length * along + normal_x * normal
        y = my + (by - ay) / chord_length * along + normal_y * normal
        corners.append(GeoPoint(y / 60.0, x / (60.0 * cos(radians(reference_latitude)))))
    boundary = tuple(corners)

    assert not _segment_intersects_polygon(first, second, boundary)
    intersects, clearance_nm = polyline_boundary_metrics(points, boundary)

    assert intersects
    assert clearance_nm == 0.0

    assert (intersects, clearance_nm) == _exhaustive_polyline_boundary_metrics(points, boundary)


def test_polyline_boundary_metrics_returns_guarded_positive_clearance() -> None:
    points = sample_geodesic_points(
        GeoPoint(0.0, 0.0), _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0)
    )

    intersects, clearance_nm = polyline_boundary_metrics(points, _far_polygon())

    assert not intersects
    assert clearance_nm > 0.0
    assert (intersects, clearance_nm) == _exhaustive_polyline_boundary_metrics(
        points, _far_polygon()
    )


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


def test_boundary_model_error_blocks_a_route_clear_of_numeric_guard_only() -> None:
    origin = GeoPoint(0.0, 0.0)
    points = sample_geodesic_points(origin, _point_on_course(origin, 270.0, 30.0))
    boundary = (
        GeoPoint(0.0003, -0.55),
        GeoPoint(0.0003, -0.45),
        GeoPoint(0.0004, -0.45),
        GeoPoint(0.0004, -0.55),
    )

    clear, numeric_clearance_nm = polyline_boundary_metrics(points, boundary)
    blocked, guarded_clearance_nm = polyline_boundary_metrics(
        points, boundary, boundary_model_error_nm=0.02
    )

    assert not clear
    assert numeric_clearance_nm > 0.0
    assert blocked
    assert guarded_clearance_nm == 0.0
