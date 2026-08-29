from __future__ import annotations

from autonavlog.nav.geodesy import geodesic_leg

# The digitized RJFM route references carry up to 0.35 NM estimated error.
# Keep the route-slot matching policy explicit and shared by installation and
# subsequent departure-plan normalization.
TRIGGER_TOLERANCE_NM = 1.0


def coordinate_distance_nm(
    latitude_deg: float,
    longitude_deg: float,
    reference_latitude_deg: float,
    reference_longitude_deg: float,
) -> float:
    """Return WGS84 separation for one route coordinate and one reference."""

    return geodesic_leg(
        latitude_deg,
        longitude_deg,
        reference_latitude_deg,
        reference_longitude_deg,
    ).distance_nm


def coordinate_matches_reference(
    latitude_deg: float,
    longitude_deg: float,
    reference_latitude_deg: float,
    reference_longitude_deg: float,
    *,
    tolerance_nm: float = TRIGGER_TOLERANCE_NM,
) -> bool:
    return (
        coordinate_distance_nm(
            latitude_deg,
            longitude_deg,
            reference_latitude_deg,
            reference_longitude_deg,
        )
        <= tolerance_nm + 1e-9
    )
