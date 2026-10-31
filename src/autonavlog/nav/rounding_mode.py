"""Numeric helpers for Issue #234's confirmed rounding rules.

Only wind direction is a calculation input. DME and TOAT results are display
projections; callers must retain their exact source values for calculations.
"""

from math import isfinite

from .rounding import DisplayRoundingPolicy, round_half_up


def wind_direction_for_calculation(
    direction_deg_from: float | None, *, enabled: bool
) -> float | None:
    """Select a wind direction without choosing a default mode.

    OFF returns the input unchanged. ON normalizes to [0, 360), rounds half-up
    to 10 degrees, then maps rounded 360 to 0. Missing direction stays missing,
    including for CALM; the existing wind solver decides whether it is usable.
    """
    if direction_deg_from is None:
        return None
    if not isfinite(direction_deg_from):
        raise ValueError("wind direction must be finite")
    if not enabled:
        return direction_deg_from
    return round_half_up(direction_deg_from % 360.0, 10.0) % 360.0


def rounded_dme_nm(distance_nm: float | None) -> float | None:
    """Project an available DME distance to 0.5 NM using the existing policy."""
    if distance_nm is None:
        return None
    if not isfinite(distance_nm):
        raise ValueError("DME distance must be finite")
    return DisplayRoundingPolicy().distance(distance_nm)


def rounded_toat_c(temperature_c: float | None) -> float | None:
    """Project TOAT to whole degrees, with half ties away from zero and +0."""
    if temperature_c is None:
        return None
    if not isfinite(temperature_c):
        raise ValueError("TOAT must be finite")
    rounded = DisplayRoundingPolicy().temperature(temperature_c)
    return 0.0 if rounded == 0.0 else rounded
