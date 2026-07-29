from __future__ import annotations

from dataclasses import dataclass

from .schemas import ClimbRow


class ClimbPerformanceError(ValueError):
    pass


@dataclass(frozen=True)
class ClimbCumulative:
    time_min: float
    fuel_gal: float
    distance_nm: float
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClimbPerformance:
    time_min: float
    fuel_gal: float
    distance_nm: float
    representative_tas_kt: float
    warnings: tuple[str, ...] = ()


def _bracket(
    values: list[float],
    target: float,
    allow_altitude_extrapolation: bool,
) -> tuple[float, float, bool]:
    ordered = sorted(set(values))
    if not ordered:
        raise ClimbPerformanceError("performance axis is empty")
    if target in ordered:
        return target, target, False
    if target < ordered[0]:
        if allow_altitude_extrapolation and len(ordered) >= 2 and ordered[0] - target <= 500:
            return ordered[0], ordered[1], True
        raise ClimbPerformanceError("value is below the performance table")
    if target > ordered[-1]:
        if allow_altitude_extrapolation and len(ordered) >= 2 and target - ordered[-1] <= 500:
            return ordered[-2], ordered[-1], True
        raise ClimbPerformanceError("value is above the performance table")
    for lower, upper in zip(ordered, ordered[1:], strict=False):
        if lower < target < upper:
            return lower, upper, False
    raise ClimbPerformanceError("unable to bracket performance value")


def _linear(lower_x: float, upper_x: float, lower_y: float, upper_y: float, target: float) -> float:
    if lower_x == upper_x:
        return lower_y
    fraction = (target - lower_x) / (upper_x - lower_x)
    return lower_y + fraction * (upper_y - lower_y)


class ClimbCalculator:
    def __init__(self, rows: list[ClimbRow]):
        self.rows = rows

    def cumulative(
        self,
        altitude_ft: float,
        temperature_c: float,
        weight_lb: float,
    ) -> ClimbCumulative:
        weights = sorted({row.weight_lb for row in self.rows})
        matching_weight = next((item for item in weights if abs(item - weight_lb) < 1e-6), None)
        if matching_weight is None:
            raise ClimbPerformanceError("weight interpolation or extrapolation is not permitted")
        rows = [row for row in self.rows if row.weight_lb == matching_weight]
        altitude_pair = _bracket(
            [row.pressure_altitude_ft for row in rows],
            altitude_ft,
            allow_altitude_extrapolation=True,
        )
        temperature_pair = _bracket(
            [row.temperature_c for row in rows],
            temperature_c,
            allow_altitude_extrapolation=False,
        )

        def values_at_altitude(altitude: float, field: str) -> float:
            indexed = {
                row.temperature_c: float(getattr(row, field))
                for row in rows
                if row.pressure_altitude_ft == altitude
            }
            if not all(value in indexed for value in temperature_pair[:2]):
                raise ClimbPerformanceError("performance table has a missing interpolation corner")
            return _linear(
                temperature_pair[0],
                temperature_pair[1],
                indexed[temperature_pair[0]],
                indexed[temperature_pair[1]],
                temperature_c,
            )

        outputs: list[float] = []
        for field in ("cumulative_time_min", "cumulative_fuel_gal", "cumulative_distance_nm"):
            lower = values_at_altitude(altitude_pair[0], field)
            upper = values_at_altitude(altitude_pair[1], field)
            outputs.append(
                _linear(altitude_pair[0], altitude_pair[1], lower, upper, altitude_ft)
            )
        warnings = ("EXTRAPOLATED_WITHIN_500FT",) if altitude_pair[2] else ()
        return ClimbCumulative(
            time_min=outputs[0],
            fuel_gal=outputs[1],
            distance_nm=outputs[2],
            warnings=warnings,
        )

    def calculate(
        self,
        departure_pressure_altitude_ft: float,
        cruise_pressure_altitude_ft: float,
        temperature_c: float,
        weight_lb: float,
    ) -> ClimbPerformance:
        departure = self.cumulative(departure_pressure_altitude_ft, temperature_c, weight_lb)
        cruise = self.cumulative(cruise_pressure_altitude_ft, temperature_c, weight_lb)
        time = cruise.time_min - departure.time_min
        fuel = cruise.fuel_gal - departure.fuel_gal
        distance = cruise.distance_nm - departure.distance_nm
        if time <= 0 or fuel < 0 or distance < 0:
            raise ClimbPerformanceError("cumulative climb table produced a non-positive climb")
        representative_tas = distance / (time / 60.0) if distance > 0 else 0.0
        return ClimbPerformance(
            time,
            fuel,
            distance,
            representative_tas,
            tuple(dict.fromkeys(departure.warnings + cruise.warnings)),
        )
