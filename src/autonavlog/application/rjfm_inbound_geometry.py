"""Conservative geometry checks for RJFM inbound guidance."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, cos, hypot, isfinite, radians

from autonavlog.nav.geodesy import _points_along_leg, geodesic_leg
from autonavlog.storage.rjfm_inbound_reference import GeoPoint

# The route search is capped at 240 NM and samples each WGS84 leg at <=0.5 NM.
# These latitude bounds cover RJFM and the equatorial synthetic fixtures while
# deliberately rejecting a different, unreviewed geographic projection regime.
MIN_SUPPORTED_LATITUDE_DEG = -10.0
MAX_SUPPORTED_LATITUDE_DEG = 45.0
MAX_SUPPORTED_LOCAL_EXTENT_DEG = 12.0
MAX_SUPPORTED_ROUTE_DISTANCE_NM = 240.0
MAX_SUPPORTED_SAMPLE_DISTANCE_NM = 0.5

# d^2/(8R) with R=3400 NM is used only as a scale estimate for the 3D
# WGS84 chord sagitta. It is not a strict upper bound for the projected
# latitude/longitude chord. The fixed 0.01 NM buffer is the declared,
# blocking numerical rejection buffer for this sampling approximation.
_CONSERVATIVE_NUMERIC_MARGIN_NM = 0.01
_CONSERVATIVE_EARTH_RADIUS_NM = 3400.0
_DISTANCE_EPSILON_NM = 1e-9
_BOUNDARY_BBOX_EPSILON_LAT_DEG = _DISTANCE_EPSILON_NM / 60.0
_BOUNDARY_BBOX_EPSILON_LON_DEG = _DISTANCE_EPSILON_NM / (
    60.0 * cos(radians(MAX_SUPPORTED_LATITUDE_DEG))
)


class GeometryUnsupportedError(ValueError):
    """The geometry is outside the reviewed local approximation domain."""


_SAMPLED_ROUTE_SENTINEL = object()


class _KnownValidSampledRoute:
    __slots__ = ("points",)

    def __init__(self, points: tuple[GeoPoint, ...], *, _sentinel: object) -> None:
        if _sentinel is not _SAMPLED_ROUTE_SENTINEL:
            raise TypeError("known-valid sampled routes must be created by _sampled_geodesic_route")
        self.points = points


@dataclass(frozen=True)
class _PreparedPolygonEdge:
    start: GeoPoint
    end: GeoPoint
    min_latitude_deg: float
    max_latitude_deg: float
    min_longitude_deg: float
    max_longitude_deg: float


@dataclass(frozen=True)
class _PreparedPolygon:
    vertices: tuple[GeoPoint, ...]
    edges: tuple[_PreparedPolygonEdge, ...]
    min_latitude_deg: float
    max_latitude_deg: float
    min_longitude_deg: float
    max_longitude_deg: float


def geometry_supported(points: tuple[GeoPoint, ...]) -> bool:
    """Return whether sampled route points fit the reviewed local domain."""
    if len(points) < 2 or not all(_valid_point(point) for point in points):
        return False
    return _route_supported(points, _route_distances(points))


def _route_distances(points: tuple[GeoPoint, ...]) -> tuple[float, ...]:
    return tuple(
        geodesic_leg(
            start.latitude_deg,
            start.longitude_deg,
            end.latitude_deg,
            end.longitude_deg,
        ).distance_nm
        for start, end in zip(points, points[1:], strict=False)
    )


def _route_supported(points: tuple[GeoPoint, ...], distances: tuple[float, ...]) -> bool:
    return (
        _local_extent_supported(points)
        and all(
            distance <= MAX_SUPPORTED_SAMPLE_DISTANCE_NM + _DISTANCE_EPSILON_NM
            for distance in distances
        )
        and sum(distances) <= MAX_SUPPORTED_ROUTE_DISTANCE_NM + _DISTANCE_EPSILON_NM
    )


def _local_extent_supported(points: tuple[GeoPoint, ...]) -> bool:
    if not points:
        return False
    latitude_extent = max(point.latitude_deg for point in points) - min(
        point.latitude_deg for point in points
    )
    longitude_extent = max(point.longitude_deg for point in points) - min(
        point.longitude_deg for point in points
    )
    return (
        latitude_extent <= MAX_SUPPORTED_LOCAL_EXTENT_DEG + _DISTANCE_EPSILON_NM
        and longitude_extent <= MAX_SUPPORTED_LOCAL_EXTENT_DEG + _DISTANCE_EPSILON_NM
    )


def sample_geodesic_points(start: GeoPoint, end: GeoPoint) -> tuple[GeoPoint, ...]:
    """Sample a WGS84 leg at no more than the supported half-NM interval."""
    if not _valid_point(start) or not _valid_point(end):
        raise GeometryUnsupportedError("route point is outside the supported domain")
    leg = geodesic_leg(start.latitude_deg, start.longitude_deg, end.latitude_deg, end.longitude_deg)
    if leg.distance_nm > MAX_SUPPORTED_ROUTE_DISTANCE_NM + _DISTANCE_EPSILON_NM:
        raise GeometryUnsupportedError("route leg exceeds the supported 240 NM domain")
    if leg.distance_nm <= MAX_SUPPORTED_SAMPLE_DISTANCE_NM:
        return (start, end)
    segment_count = max(1, ceil(leg.distance_nm / MAX_SUPPORTED_SAMPLE_DISTANCE_NM))
    points = [start]
    intermediate_distances_nm = tuple(
        leg.distance_nm * index / segment_count for index in range(1, segment_count)
    )
    for latitude_deg, longitude_deg in _points_along_leg(
        start.latitude_deg,
        start.longitude_deg,
        leg.initial_true_course_deg,
        intermediate_distances_nm,
    ):
        points.append(GeoPoint(latitude_deg, longitude_deg))
    points.append(end)
    result = tuple(points)
    if not _local_extent_supported(result):
        raise GeometryUnsupportedError("sampled route is outside the supported local domain")
    return result


def _sampled_geodesic_route(start: GeoPoint, end: GeoPoint) -> _KnownValidSampledRoute:
    points = sample_geodesic_points(start, end)
    if not all(_valid_point(point) for point in points):
        raise GeometryUnsupportedError("route samples are outside the supported domain")
    return _KnownValidSampledRoute(points, _sentinel=_SAMPLED_ROUTE_SENTINEL)


def polyline_boundary_metrics(
    points: tuple[GeoPoint, ...],
    polygon: tuple[GeoPoint, ...],
    *,
    boundary_model_error_nm: float = 0.0,
) -> tuple[bool, float]:
    """Return conservative intersection and minimum clearance for a route.

    Polygon edges are interpreted as straight latitude/longitude edges. Route
    pieces are WGS84 geodesics approximated by <=0.5 NM chords. The returned
    clearance is reduced by the documented conservative numeric guard and the
    declared maximum error of the normalized source-boundary model.
    """
    if (
        not isfinite(boundary_model_error_nm)
        or boundary_model_error_nm < 0.0
        or len(points) < 2
        or not all(_valid_point(point) for point in points)
    ):
        raise GeometryUnsupportedError("route samples are outside the supported domain")
    normalized_polygon = _normalized_polygon(polygon)
    if len(normalized_polygon) < 3 or not all(_valid_point(point) for point in normalized_polygon):
        raise GeometryUnsupportedError("boundary polygon is outside the supported domain")
    route_distances = _route_distances(points)
    combined_points = points + normalized_polygon
    if not _route_supported(points, route_distances) or not _local_extent_supported(
        combined_points
    ):
        raise GeometryUnsupportedError("geometry exceeds the supported local domain")
    prepared_polygon = _prepare_polygon(normalized_polygon)
    return _polyline_boundary_metrics_validated(
        points,
        route_distances,
        normalized_polygon,
        prepared_polygon=prepared_polygon,
        boundary_model_error_nm=boundary_model_error_nm,
    )


def _polyline_boundary_metrics_for_sampled_route(
    sampled_route: _KnownValidSampledRoute,
    polygon: tuple[GeoPoint, ...],
    *,
    boundary_model_error_nm: float = 0.0,
) -> tuple[bool, float]:
    if (
        not isfinite(boundary_model_error_nm)
        or boundary_model_error_nm < 0.0
        or len(sampled_route.points) < 2
        or not all(_valid_point(point) for point in sampled_route.points)
    ):
        raise GeometryUnsupportedError("route samples are outside the supported domain")
    points = sampled_route.points
    normalized_polygon = _normalized_polygon(polygon)
    if len(normalized_polygon) < 3 or not all(_valid_point(point) for point in normalized_polygon):
        raise GeometryUnsupportedError("boundary polygon is outside the supported domain")
    _validate_sampled_route_boundary_domain(points, normalized_polygon)
    prepared_polygon = _prepare_polygon(normalized_polygon)
    intersects = _sampled_route_intersects_boundary(
        points,
        normalized_polygon,
        prepared_polygon=prepared_polygon,
    )
    if intersects:
        return True, 0.0
    route_distances = _route_distances(points)
    if not _route_supported(points, route_distances):
        raise GeometryUnsupportedError("route samples are outside the supported domain")
    return _polyline_boundary_metrics_validated(
        points,
        route_distances,
        normalized_polygon,
        prepared_polygon=prepared_polygon,
        boundary_model_error_nm=boundary_model_error_nm,
        intersection_proven_clear=True,
    )


def _validate_sampled_route_boundary_domain(
    points: tuple[GeoPoint, ...],
    normalized_polygon: tuple[GeoPoint, ...],
) -> None:
    if len(points) < 2 or not all(_valid_point(point) for point in points):
        raise GeometryUnsupportedError("route samples are outside the supported domain")
    combined_points = points + normalized_polygon
    if not _local_extent_supported(combined_points):
        raise GeometryUnsupportedError("geometry exceeds the supported local domain")


def _polyline_boundary_metrics_validated(
    points: tuple[GeoPoint, ...],
    route_distances: tuple[float, ...],
    normalized_polygon: tuple[GeoPoint, ...],
    *,
    prepared_polygon: _PreparedPolygon,
    boundary_model_error_nm: float,
    intersection_proven_clear: bool = False,
) -> tuple[bool, float]:
    first_point_inside = _point_in_polygon(points[0], normalized_polygon)
    if first_point_inside:
        return True, 0.0

    route_segments = tuple(zip(points, points[1:], strict=False))
    if not intersection_proven_clear and _sampled_route_intersects_boundary(
        points,
        normalized_polygon,
        prepared_polygon=prepared_polygon,
        route_segments=route_segments,
    ):
        return True, 0.0

    minimum_clearance_nm = float("inf")
    for (start, end), distance_nm in zip(route_segments, route_distances, strict=True):
        raw_clearance_nm = _segment_clearance_nm(
            start,
            end,
            normalized_polygon,
            prepared_polygon=prepared_polygon,
        )
        guard_nm = _chord_clearance_guard_nm(distance_nm) + boundary_model_error_nm
        conservative_clearance_nm = raw_clearance_nm - guard_nm
        if conservative_clearance_nm <= 0.0:
            return True, 0.0
        minimum_clearance_nm = min(minimum_clearance_nm, conservative_clearance_nm)
    return False, minimum_clearance_nm


def _sampled_route_intersects_boundary(
    points: tuple[GeoPoint, ...],
    polygon: tuple[GeoPoint, ...],
    *,
    prepared_polygon: _PreparedPolygon,
    route_segments: tuple[tuple[GeoPoint, GeoPoint], ...] | None = None,
) -> bool:
    first_point_inside = _point_in_polygon(points[0], polygon)
    if first_point_inside:
        return True
    if route_segments is None:
        route_segments = tuple(zip(points, points[1:], strict=False))
    overlapping_route_segments = tuple(
        (start, end)
        for start, end in route_segments
        if _segment_bbox_overlaps_polygon_bbox(start, end, prepared_polygon)
    )
    return any(
        _segment_intersects_polygon(
            start,
            end,
            polygon,
            prepared_polygon=prepared_polygon,
            check_endpoints=False,
        )
        for start, end in overlapping_route_segments
    )


def _normalized_polygon(polygon: tuple[GeoPoint, ...]) -> tuple[GeoPoint, ...]:
    if len(polygon) >= 2 and polygon[0] == polygon[-1]:
        return polygon[:-1]
    return polygon


def _chord_clearance_guard_nm(distance_nm: float) -> float:
    if distance_nm > MAX_SUPPORTED_SAMPLE_DISTANCE_NM + _DISTANCE_EPSILON_NM:
        raise GeometryUnsupportedError("route sample exceeds the supported half-NM interval")
    sagitta_scale_nm = distance_nm * distance_nm / (8.0 * _CONSERVATIVE_EARTH_RADIUS_NM)
    return sagitta_scale_nm + _CONSERVATIVE_NUMERIC_MARGIN_NM


def _segment_intersects_polygon(
    start: GeoPoint,
    end: GeoPoint,
    polygon: tuple[GeoPoint, ...],
    *,
    prepared_polygon: _PreparedPolygon | None = None,
    start_inside: bool | None = None,
    end_inside: bool | None = None,
    check_endpoints: bool = True,
) -> bool:
    if check_endpoints:
        if start_inside is None:
            start_inside = _point_in_polygon(start, polygon)
        if end_inside is None:
            end_inside = _point_in_polygon(end, polygon)
    else:
        start_inside = False if start_inside is None else start_inside
        end_inside = False if end_inside is None else end_inside
    prepared = prepared_polygon or _prepare_polygon(polygon)
    return (
        start_inside
        or end_inside
        or (
            _segment_bbox_overlaps_polygon_bbox(start, end, prepared)
            and any(
                _segments_intersect(start, end, edge.start, edge.end)
                for edge in _candidate_polygon_edges(
                    start,
                    end,
                    prepared,
                )
            )
        )
    )


def _segment_clearance_nm(
    start: GeoPoint,
    end: GeoPoint,
    polygon: tuple[GeoPoint, ...],
    *,
    prepared_polygon: _PreparedPolygon | None = None,
) -> float:
    prepared = prepared_polygon or _prepare_polygon(polygon)
    best_distance_nm = float("inf")
    for edge, lower_bound_nm in _ordered_edges_by_clearance_lower_bound(start, end, prepared):
        if lower_bound_nm >= best_distance_nm:
            break
        best_distance_nm = min(
            best_distance_nm, _segment_distance_nm(start, end, edge.start, edge.end)
        )
    return best_distance_nm


def _prepare_polygon(polygon: tuple[GeoPoint, ...]) -> _PreparedPolygon:
    return _PreparedPolygon(
        vertices=polygon,
        edges=tuple(
            _PreparedPolygonEdge(
                start=edge_start,
                end=edge_end,
                min_latitude_deg=min(edge_start.latitude_deg, edge_end.latitude_deg),
                max_latitude_deg=max(edge_start.latitude_deg, edge_end.latitude_deg),
                min_longitude_deg=min(edge_start.longitude_deg, edge_end.longitude_deg),
                max_longitude_deg=max(edge_start.longitude_deg, edge_end.longitude_deg),
            )
            for edge_start, edge_end in zip(polygon, polygon[1:] + polygon[:1], strict=True)
        ),
        min_latitude_deg=min(point.latitude_deg for point in polygon),
        max_latitude_deg=max(point.latitude_deg for point in polygon),
        min_longitude_deg=min(point.longitude_deg for point in polygon),
        max_longitude_deg=max(point.longitude_deg for point in polygon),
    )


def _candidate_polygon_edges(
    start: GeoPoint, end: GeoPoint, prepared_polygon: _PreparedPolygon
) -> tuple[_PreparedPolygonEdge, ...]:
    (
        min_latitude_deg,
        max_latitude_deg,
        min_longitude_deg,
        max_longitude_deg,
    ) = _expanded_segment_bbox(start, end)
    return tuple(
        edge
        for edge in prepared_polygon.edges
        if not (
            edge.max_latitude_deg < min_latitude_deg
            or edge.min_latitude_deg > max_latitude_deg
            or edge.max_longitude_deg < min_longitude_deg
            or edge.min_longitude_deg > max_longitude_deg
        )
    )


def _segment_bbox_overlaps_polygon_bbox(
    start: GeoPoint, end: GeoPoint, prepared_polygon: _PreparedPolygon
) -> bool:
    (
        min_latitude_deg,
        max_latitude_deg,
        min_longitude_deg,
        max_longitude_deg,
    ) = _expanded_segment_bbox(start, end)
    return not (
        max_latitude_deg < prepared_polygon.min_latitude_deg
        or min_latitude_deg > prepared_polygon.max_latitude_deg
        or max_longitude_deg < prepared_polygon.min_longitude_deg
        or min_longitude_deg > prepared_polygon.max_longitude_deg
    )


def _expanded_segment_bbox(start: GeoPoint, end: GeoPoint) -> tuple[float, float, float, float]:
    min_latitude_deg = min(start.latitude_deg, end.latitude_deg) - _BOUNDARY_BBOX_EPSILON_LAT_DEG
    max_latitude_deg = max(start.latitude_deg, end.latitude_deg) + _BOUNDARY_BBOX_EPSILON_LAT_DEG
    min_longitude_deg = min(start.longitude_deg, end.longitude_deg) - _BOUNDARY_BBOX_EPSILON_LON_DEG
    max_longitude_deg = max(start.longitude_deg, end.longitude_deg) + _BOUNDARY_BBOX_EPSILON_LON_DEG
    return min_latitude_deg, max_latitude_deg, min_longitude_deg, max_longitude_deg


def _ordered_edges_by_clearance_lower_bound(
    start: GeoPoint, end: GeoPoint, prepared_polygon: _PreparedPolygon
) -> tuple[tuple[_PreparedPolygonEdge, float], ...]:
    min_latitude_deg = min(start.latitude_deg, end.latitude_deg)
    max_latitude_deg = max(start.latitude_deg, end.latitude_deg)
    min_longitude_deg = min(start.longitude_deg, end.longitude_deg)
    max_longitude_deg = max(start.longitude_deg, end.longitude_deg)
    return tuple(
        sorted(
            (
                (
                    edge,
                    _bbox_clearance_lower_bound_nm(
                        segment_min_latitude_deg=min_latitude_deg,
                        segment_max_latitude_deg=max_latitude_deg,
                        segment_min_longitude_deg=min_longitude_deg,
                        segment_max_longitude_deg=max_longitude_deg,
                        edge=edge,
                    ),
                )
                for edge in prepared_polygon.edges
            ),
            key=lambda item: item[1],
        )
    )


def _bbox_clearance_lower_bound_nm(
    *,
    segment_min_latitude_deg: float,
    segment_max_latitude_deg: float,
    segment_min_longitude_deg: float,
    segment_max_longitude_deg: float,
    edge: _PreparedPolygonEdge,
) -> float:
    latitude_gap_deg = max(
        0.0,
        edge.min_latitude_deg - segment_max_latitude_deg,
        segment_min_latitude_deg - edge.max_latitude_deg,
    )
    longitude_gap_deg = max(
        0.0,
        edge.min_longitude_deg - segment_max_longitude_deg,
        segment_min_longitude_deg - edge.max_longitude_deg,
    )
    lower_bound_dx_nm = longitude_gap_deg * 60.0 * cos(radians(MAX_SUPPORTED_LATITUDE_DEG))
    lower_bound_dy_nm = latitude_gap_deg * 60.0
    return hypot(lower_bound_dx_nm, lower_bound_dy_nm)


def _segments_intersect(a: GeoPoint, b: GeoPoint, c: GeoPoint, d: GeoPoint) -> bool:
    reference_latitude = (a.latitude_deg + b.latitude_deg + c.latitude_deg + d.latitude_deg) / 4.0
    ax, ay = _xy(a, reference_latitude)
    bx, by = _xy(b, reference_latitude)
    cx, cy = _xy(c, reference_latitude)
    dx, dy = _xy(d, reference_latitude)

    def orientation(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> float:
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)

    def on_segment(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> bool:
        return (
            min(px, rx) - _DISTANCE_EPSILON_NM <= qx <= max(px, rx) + _DISTANCE_EPSILON_NM
            and min(py, ry) - _DISTANCE_EPSILON_NM <= qy <= max(py, ry) + _DISTANCE_EPSILON_NM
            and abs(orientation(px, py, qx, qy, rx, ry)) <= _DISTANCE_EPSILON_NM
        )

    ab_c = orientation(ax, ay, bx, by, cx, cy)
    ab_d = orientation(ax, ay, bx, by, dx, dy)
    cd_a = orientation(cx, cy, dx, dy, ax, ay)
    cd_b = orientation(cx, cy, dx, dy, bx, by)
    if abs(ab_c) <= _DISTANCE_EPSILON_NM and on_segment(ax, ay, cx, cy, bx, by):
        return True
    if abs(ab_d) <= _DISTANCE_EPSILON_NM and on_segment(ax, ay, dx, dy, bx, by):
        return True
    if abs(cd_a) <= _DISTANCE_EPSILON_NM and on_segment(cx, cy, ax, ay, dx, dy):
        return True
    if abs(cd_b) <= _DISTANCE_EPSILON_NM and on_segment(cx, cy, bx, by, dx, dy):
        return True
    return (ab_c > 0) != (ab_d > 0) and (cd_a > 0) != (cd_b > 0)


def _segment_distance_nm(a: GeoPoint, b: GeoPoint, c: GeoPoint, d: GeoPoint) -> float:
    if _segments_intersect(a, b, c, d):
        return 0.0
    reference_latitude = (a.latitude_deg + b.latitude_deg + c.latitude_deg + d.latitude_deg) / 4.0
    ax, ay = _xy(a, reference_latitude)
    bx, by = _xy(b, reference_latitude)
    cx, cy = _xy(c, reference_latitude)
    dx, dy = _xy(d, reference_latitude)
    return min(
        _point_to_segment_distance_nm(ax, ay, cx, cy, dx, dy),
        _point_to_segment_distance_nm(bx, by, cx, cy, dx, dy),
        _point_to_segment_distance_nm(cx, cy, ax, ay, bx, by),
        _point_to_segment_distance_nm(dx, dy, ax, ay, bx, by),
    )


def _point_to_segment_distance_nm(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    dx = bx - ax
    dy = by - ay
    length_squared = dx * dx + dy * dy
    if length_squared <= _DISTANCE_EPSILON_NM:
        return hypot(px - ax, py - ay)
    projection = ((px - ax) * dx + (py - ay) * dy) / length_squared
    projection = max(0.0, min(1.0, projection))
    nearest_x = ax + projection * dx
    nearest_y = ay + projection * dy
    return hypot(px - nearest_x, py - nearest_y)


def _point_in_polygon(point: GeoPoint, polygon: tuple[GeoPoint, ...]) -> bool:
    reference_latitude = (point.latitude_deg + sum(vertex.latitude_deg for vertex in polygon)) / (
        len(polygon) + 1
    )
    px, py = _xy(point, reference_latitude)
    vertices = [_xy(vertex, reference_latitude) for vertex in polygon]
    inside = False
    for (ax, ay), (bx, by) in zip(vertices, vertices[1:] + vertices[:1], strict=True):
        if _point_to_segment_distance_nm(px, py, ax, ay, bx, by) <= _DISTANCE_EPSILON_NM:
            return True
        if (ay > py) != (by > py):
            x_at_y = (bx - ax) * (py - ay) / (by - ay) + ax
            if px < x_at_y:
                inside = not inside
    return inside


def _xy(point: GeoPoint, reference_latitude_deg: float) -> tuple[float, float]:
    scale_x = 60.0 * cos(radians(reference_latitude_deg))
    return point.longitude_deg * scale_x, point.latitude_deg * 60.0


def _valid_point(point: GeoPoint) -> bool:
    return (
        isfinite(point.latitude_deg)
        and isfinite(point.longitude_deg)
        and MIN_SUPPORTED_LATITUDE_DEG <= point.latitude_deg <= MAX_SUPPORTED_LATITUDE_DEG
        and -180.0 <= point.longitude_deg <= 180.0
    )
