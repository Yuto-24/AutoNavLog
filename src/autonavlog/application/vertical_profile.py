from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class DescentProfile:
    """Vertical path for descent followed by level deceleration."""

    cruise_altitude_ft_msl: float
    target_altitude_ft_msl: float
    descent_rate_fpm: float
    vertical_duration_seconds: float
    deceleration_duration_seconds: float

    @property
    def planned_duration_seconds(self) -> float:
        return self.vertical_duration_seconds + self.deceleration_duration_seconds

    def altitude_at_elapsed(self, elapsed_seconds: float) -> float:
        """Return altitude after ``elapsed_seconds`` from EOC."""

        elapsed = min(max(float(elapsed_seconds), 0.0), self.vertical_duration_seconds)
        altitude = self.cruise_altitude_ft_msl - (
            elapsed / 60.0 * self.descent_rate_fpm
        )
        return max(self.target_altitude_ft_msl, altitude)


def descent_profile_from_metadata(
    metadata: Mapping[str, Any],
) -> DescentProfile | None:
    """Build a validated profile from calculation metadata."""

    cruise = _finite_number(metadata.get("cruise_altitude_ft_msl"))
    target = _finite_number(metadata.get("target_altitude_ft_msl"))
    rate = _finite_number(metadata.get("descent_rate_fpm"))
    vertical = _finite_number(metadata.get("vertical_descent_duration_seconds"))
    deceleration = _finite_number(metadata.get("deceleration_duration_seconds"))
    if None in {cruise, target, rate, vertical, deceleration}:
        return None
    assert cruise is not None
    assert target is not None
    assert rate is not None
    assert vertical is not None
    assert deceleration is not None
    if cruise < target or rate <= 0 or vertical < 0 or deceleration < 0:
        return None
    expected_vertical = (cruise - target) / rate * 60.0
    if abs(expected_vertical - vertical) > 1e-6:
        return None
    return DescentProfile(
        cruise_altitude_ft_msl=cruise,
        target_altitude_ft_msl=target,
        descent_rate_fpm=rate,
        vertical_duration_seconds=vertical,
        deceleration_duration_seconds=deceleration,
    )


def _finite_number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if isfinite(number) else None
