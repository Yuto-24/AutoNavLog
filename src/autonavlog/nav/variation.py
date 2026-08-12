from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

VARIATION_LATITUDE_THRESHOLD_DEG = 32.0
VARIATION_NORTH_DEG_EAST = 8.0
VARIATION_SOUTH_DEG_EAST = 7.0
VARIATION_RULE_VERSION = "DEPARTURE_LATITUDE_32N_V1"


@dataclass(frozen=True)
class VariationDecision:
    degrees_east: float
    metadata: dict[str, Any]


def variation_for_departure_latitude(latitude_deg: float) -> VariationDecision:
    """Resolve east variation from a leg's departure latitude.

    The 32.0 N boundary belongs to the northern band. Invalid coordinates are
    rejected instead of silently falling back to either operational value.
    """

    latitude = float(latitude_deg)
    if not isfinite(latitude) or not -90.0 <= latitude <= 90.0:
        raise ValueError("departure latitude must be finite and within [-90, 90]")
    north_band = latitude >= VARIATION_LATITUDE_THRESHOLD_DEG
    degrees_east = VARIATION_NORTH_DEG_EAST if north_band else VARIATION_SOUTH_DEG_EAST
    return VariationDecision(
        degrees_east=degrees_east,
        metadata={
            "rule_version": VARIATION_RULE_VERSION,
            "method": "LEG_DEPARTURE_LATITUDE_BAND",
            "departure_latitude_deg": latitude,
            "threshold_latitude_deg": VARIATION_LATITUDE_THRESHOLD_DEG,
            "threshold_inclusive_side": "NORTH",
            "selected_band": "NORTH" if north_band else "SOUTH",
            "degrees_east": degrees_east,
        },
    )
