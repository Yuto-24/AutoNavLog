"""Conservative geometry checks for RJFM inbound guidance."""

from __future__ import annotations

from math import ceil, cos, hypot, isfinite, radians

from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
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


class GeometryUnsupportedError(ValueError):
    """The geometry is outside the reviewed local approximation domain."""


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
    for index in range(1, segment_count):
        distance_nm = leg.distance_nm * index / segment_count
        latitude_deg, longitude_deg = point_along_leg(
            start.latitude_deg,
            start.longitude_deg,
            leg.initial_true_course_deg,
            distance_nm,
        )
        points.append(GeoPoint(latitude_deg, longitude_deg))
    points.append(end)
    result = tuple(points)
    if not _local_extent_supported(result):
        raise GeometryUnsupportedError("sampled route is outside the supported local domain")
    return result


def polyline_boundary_metrics(
    points: tuple[GeoPoint, ...], polygon: tuple[GeoPoint, ...]
) -> tuple[bool, float]:
    """Return conservative intersection and minimum clearance for a route.

    Polygon edges are interpreted as straight latitude/longitude edges. Route
    pieces are WGS84 geodesics approximated by <=0.5 NM chords. The returned
    clearance is reduced by the documented conservative numeric guard.
    """
    if len(points) < 2 or not all(_valid_point(point) for point in points):
        raise GeometryUnsupportedError("route samples are outside the supported domain")
    normalized_polygon = _normalized_polygon(polygon)
    if len(normalized_polygon) < 3 or not all(
        _valid_point(point) for point in normalized_polygon
    ):
        raise GeometryUnsupportedError("boundary polygon is outside the supported domain")
    route_distances = _route_distances(points)
    combined_points = points + normalized_polygon
    if not _route_supported(points, route_distances) or not _local_extent_supported(
        combined_points
    ):
        raise GeometryUnsupportedError("geometry exceeds the supported local domain")

    minimum_clearance_nm = float("inf")
    for (start, end), distance_nm in zip(
        zip(points, points[1:], strict=False), route_distances, strict=True
    ):
        if _segment_intersects_polygon(start, end, normalized_polygon):
            return True, 0.0
        raw_clearance_nm = _segment_clearance_nm(start, end, normalized_polygon)
        guard_nm = _chord_clearance_guard_nm(distance_nm)
        conservative_clearance_nm = raw_clearance_nm - guard_nm
        if conservative_clearance_nm <= 0.0:
            return True, 0.0
        minimum_clearance_nm = min(minimum_clearance_nm, conservative_clearance_nm)
    return False, minimum_clearance_nm


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


def _segment_clearance_nm(
    start: GeoPoint, end: GeoPoint, polygon: tuple[GeoPoint, ...]
) -> float:
    return min(
        _segment_distance_nm(start, end, edge_start, edge_end)
        for edge_start, edge_end in zip(polygon, polygon[1:] + polygon[:1], strict=True)
    )


def _segments_intersect(a: GeoPoint, b: GeoPoint, c: GeoPoint, d: GeoPoint) -> bool:
    reference_latitude = (
        a.latitude_deg + b.latitude_deg + c.latitude_deg + d.latitude_deg
    ) / 4.0
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
    reference_latitude = (
        a.latitude_deg + b.latitude_deg + c.latitude_deg + d.latitude_deg
    ) / 4.0
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
    reference_latitude = (
        point.latitude_deg + sum(vertex.latitude_deg for vertex in polygon)
    ) / (len(polygon) + 1)
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
