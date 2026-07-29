from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, isfinite, radians, sin


class WindTriangleError(ValueError):
    pass


@dataclass(frozen=True)
class WindTriangleResult:
    wca_deg: float
    true_heading_deg: float
    ground_speed_kt: float
    calm: bool


def solve_wind_triangle(
    true_course_deg: float,
    tas_kt: float,
    wind_direction_deg_from: float | None,
    wind_speed_kt: float,
) -> WindTriangleResult:
    values = (true_course_deg, tas_kt, wind_speed_kt)
    if not all(isfinite(value) for value in values):
        raise WindTriangleError("course, TAS, and wind speed must be finite")
    if tas_kt <= 0:
        raise WindTriangleError("TAS must be greater than zero")
    if wind_speed_kt < 0:
        raise WindTriangleError("wind speed cannot be negative")
    if wind_speed_kt == 0:
        return WindTriangleResult(0.0, true_course_deg % 360, tas_kt, True)
    if wind_direction_deg_from is None or not isfinite(wind_direction_deg_from):
        raise WindTriangleError("wind direction is required for non-calm wind")

    delta = radians(wind_direction_deg_from - true_course_deg)
    crosswind_ratio = (wind_speed_kt / tas_kt) * sin(delta)
    if abs(crosswind_ratio) >= 1:
        raise WindTriangleError("crosswind component is greater than or equal to TAS")
    wca_rad = asin(crosswind_ratio)
    ground_speed = tas_kt * cos(wca_rad) - wind_speed_kt * cos(delta)
    if ground_speed <= 0 or not isfinite(ground_speed):
        raise WindTriangleError("computed ground speed is not positive")
    wca_deg = wca_rad * 180.0 / 3.141592653589793
    return WindTriangleResult(
        wca_deg=wca_deg,
        true_heading_deg=(true_course_deg + wca_deg) % 360,
        ground_speed_kt=ground_speed,
        calm=False,
    )
