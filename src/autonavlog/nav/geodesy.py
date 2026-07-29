from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from geographiclib.geodesic import Geodesic

METERS_PER_NM = 1852.0


@dataclass(frozen=True)
class GeodesicLeg:
    distance_nm: float
    initial_true_course_deg: float
    midpoint_latitude_deg: float
    midpoint_longitude_deg: float


@dataclass(frozen=True)
class RouteProjection:
    latitude_deg: float
    longitude_deg: float
    section_index: int
    section_fraction: float


def normalize_degrees(value: float) -> float:
    return value % 360.0


def geodesic_leg(
    from_latitude_deg: float,
    from_longitude_deg: float,
    to_latitude_deg: float,
    to_longitude_deg: float,
) -> GeodesicLeg:
    inverse = Geodesic.WGS84.Inverse(
        from_latitude_deg,
        from_longitude_deg,
        to_latitude_deg,
        to_longitude_deg,
    )
    distance_m = float(inverse["s12"])
    line = Geodesic.WGS84.Line(
        from_latitude_deg,
        from_longitude_deg,
        float(inverse["azi1"]),
    )
    midpoint = line.Position(distance_m / 2, Geodesic.LATITUDE | Geodesic.LONGITUDE)
    return GeodesicLeg(
        distance_nm=distance_m / METERS_PER_NM,
        initial_true_course_deg=normalize_degrees(float(inverse["azi1"])),
        midpoint_latitude_deg=float(midpoint["lat2"]),
        midpoint_longitude_deg=float(midpoint["lon2"]),
    )


def point_along_leg(
    from_latitude_deg: float,
    from_longitude_deg: float,
    true_course_deg: float,
    distance_nm: float,
) -> tuple[float, float]:
    result = Geodesic.WGS84.Direct(
        from_latitude_deg,
        from_longitude_deg,
        true_course_deg,
        distance_nm * METERS_PER_NM,
    )
    return float(result["lat2"]), float(result["lon2"])


def point_along_route(
    coordinates: Sequence[tuple[float, float]],
    distance_nm: float,
) -> RouteProjection | None:
    if len(coordinates) < 2 or distance_nm < 0:
        return None
    remaining = distance_nm
    for index, (start, end) in enumerate(zip(coordinates, coordinates[1:], strict=False)):
        leg = geodesic_leg(start[0], start[1], end[0], end[1])
        if remaining <= leg.distance_nm:
            fraction = 0.0 if leg.distance_nm == 0 else remaining / leg.distance_nm
            latitude, longitude = point_along_leg(
                start[0],
                start[1],
                leg.initial_true_course_deg,
                remaining,
            )
            return RouteProjection(latitude, longitude, index, fraction)
        remaining -= leg.distance_nm
    if abs(remaining) < 1e-9:
        latitude, longitude = coordinates[-1]
        return RouteProjection(latitude, longitude, len(coordinates) - 2, 1.0)
    return None
