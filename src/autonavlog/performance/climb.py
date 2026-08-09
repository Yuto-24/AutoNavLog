from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .schemas import ClimbRow, ClimbTemperaturePolicy


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
    temperature_policy: str = ClimbTemperaturePolicy.TABLE_GRID.value
    representative_altitude_ft: float | None = None
    representative_isa_temperature_c: float | None = None
    temperature_delta_above_standard_c: float = 0.0
    temperature_adjustment_factor: float = 1.0


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
    """Calculate climb performance using the manifest's temperature policy.

    ``ISA_BASELINE_10_PERCENT_PER_10C_ABOVE`` implements SR22 G6 POH
    P/N 13772-006 Reissue A, pp. 5-30/5-31: the table contains one ISA-OAT
    cumulative row per altitude, and computed time/fuel/distance are increased
    10 percent for each 10 deg C above standard.
    """

    def __init__(
        self,
        rows: list[ClimbRow],
        temperature_policy: ClimbTemperaturePolicy | str = (ClimbTemperaturePolicy.TABLE_GRID),
    ):
        self.rows = rows
        try:
            self.temperature_policy = ClimbTemperaturePolicy(temperature_policy)
        except ValueError as error:
            raise ClimbPerformanceError(
                f"unsupported climb temperature policy: {temperature_policy}"
            ) from error

    def _rows_for_weight(self, weight_lb: float) -> list[ClimbRow]:
        if not isfinite(weight_lb):
            raise ClimbPerformanceError("weight must be finite")
        weights = sorted({row.weight_lb for row in self.rows})
        matching_weight = next(
            (item for item in weights if abs(item - weight_lb) < 1e-6),
            None,
        )
        if matching_weight is None:
            raise ClimbPerformanceError("weight interpolation or extrapolation is not permitted")
        return [row for row in self.rows if row.weight_lb == matching_weight]

    def _table_grid_cumulative(
        self,
        altitude_ft: float,
        temperature_c: float,
        weight_lb: float,
    ) -> ClimbCumulative:
        rows = self._rows_for_weight(weight_lb)
        if not all(isfinite(value) for value in (altitude_ft, temperature_c)):
            raise ClimbPerformanceError("altitude and temperature must be finite")
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
        for field in (
            "cumulative_time_min",
            "cumulative_fuel_gal",
            "cumulative_distance_nm",
        ):
            lower = values_at_altitude(altitude_pair[0], field)
            upper = values_at_altitude(altitude_pair[1], field)
            outputs.append(
                _linear(
                    altitude_pair[0],
                    altitude_pair[1],
                    lower,
                    upper,
                    altitude_ft,
                )
            )
        warnings = ("EXTRAPOLATED_WITHIN_500FT",) if altitude_pair[2] else ()
        return ClimbCumulative(
            time_min=outputs[0],
            fuel_gal=outputs[1],
            distance_nm=outputs[2],
            warnings=warnings,
        )

    def _isa_baseline_at_altitude(
        self,
        altitude_ft: float,
        weight_lb: float,
    ) -> tuple[ClimbCumulative, float]:
        rows = self._rows_for_weight(weight_lb)
        if not isfinite(altitude_ft):
            raise ClimbPerformanceError("altitude must be finite")
        altitude_pair = _bracket(
            [row.pressure_altitude_ft for row in rows],
            altitude_ft,
            allow_altitude_extrapolation=True,
        )

        def row_at_altitude(altitude: float) -> ClimbRow:
            matches = [row for row in rows if row.pressure_altitude_ft == altitude]
            if len(matches) != 1:
                raise ClimbPerformanceError(
                    "ISA baseline policy requires exactly one row per altitude"
                )
            return matches[0]

        lower = row_at_altitude(altitude_pair[0])
        upper = row_at_altitude(altitude_pair[1])

        def interpolate(field: str) -> float:
            return _linear(
                altitude_pair[0],
                altitude_pair[1],
                float(getattr(lower, field)),
                float(getattr(upper, field)),
                altitude_ft,
            )

        warnings = ("EXTRAPOLATED_WITHIN_500FT",) if altitude_pair[2] else ()
        return (
            ClimbCumulative(
                time_min=interpolate("cumulative_time_min"),
                fuel_gal=interpolate("cumulative_fuel_gal"),
                distance_nm=interpolate("cumulative_distance_nm"),
                warnings=warnings,
            ),
            interpolate("temperature_c"),
        )

    @staticmethod
    def _temperature_adjustment(
        actual_temperature_c: float,
        representative_isa_temperature_c: float,
    ) -> tuple[float, float]:
        if not all(
            isfinite(value)
            for value in (
                actual_temperature_c,
                representative_isa_temperature_c,
            )
        ):
            raise ClimbPerformanceError("temperature must be finite")
        delta_above_standard = max(
            0.0,
            actual_temperature_c - representative_isa_temperature_c,
        )
        # POH: add 10% per 10 deg C above standard. Apply proportionally
        # (one percent per degree C) without reducing standard-or-colder values.
        return delta_above_standard, 1.0 + delta_above_standard / 100.0

    def _isa_baseline_climb(
        self,
        departure_pressure_altitude_ft: float,
        cruise_pressure_altitude_ft: float,
        temperature_c: float,
        weight_lb: float,
    ) -> ClimbPerformance:
        departure, _ = self._isa_baseline_at_altitude(
            departure_pressure_altitude_ft,
            weight_lb,
        )
        cruise, _ = self._isa_baseline_at_altitude(
            cruise_pressure_altitude_ft,
            weight_lb,
        )
        baseline_time = cruise.time_min - departure.time_min
        baseline_fuel = cruise.fuel_gal - departure.fuel_gal
        baseline_distance = cruise.distance_nm - departure.distance_nm
        if baseline_time <= 0 or baseline_fuel < 0 or baseline_distance < 0:
            raise ClimbPerformanceError("cumulative climb table produced a non-positive climb")

        representative_altitude = (
            departure_pressure_altitude_ft + cruise_pressure_altitude_ft
        ) / 2.0
        _, representative_isa_temperature = self._isa_baseline_at_altitude(
            representative_altitude,
            weight_lb,
        )
        delta_above_standard, adjustment_factor = self._temperature_adjustment(
            temperature_c,
            representative_isa_temperature,
        )
        time = baseline_time * adjustment_factor
        fuel = baseline_fuel * adjustment_factor
        distance = baseline_distance * adjustment_factor
        representative_tas = distance / (time / 60.0) if distance > 0 else 0.0
        warnings = tuple(dict.fromkeys(departure.warnings + cruise.warnings))
        return ClimbPerformance(
            time_min=time,
            fuel_gal=fuel,
            distance_nm=distance,
            representative_tas_kt=representative_tas,
            warnings=warnings,
            temperature_policy=self.temperature_policy.value,
            representative_altitude_ft=representative_altitude,
            representative_isa_temperature_c=(representative_isa_temperature),
            temperature_delta_above_standard_c=delta_above_standard,
            temperature_adjustment_factor=adjustment_factor,
        )

    def cumulative(
        self,
        altitude_ft: float,
        temperature_c: float,
        weight_lb: float,
    ) -> ClimbCumulative:
        if self.temperature_policy == ClimbTemperaturePolicy.TABLE_GRID:
            return self._table_grid_cumulative(
                altitude_ft,
                temperature_c,
                weight_lb,
            )
        if altitude_ft == 0:
            baseline, _ = self._isa_baseline_at_altitude(0.0, weight_lb)
            return baseline
        result = self._isa_baseline_climb(
            0.0,
            altitude_ft,
            temperature_c,
            weight_lb,
        )
        return ClimbCumulative(
            result.time_min,
            result.fuel_gal,
            result.distance_nm,
            result.warnings,
        )

    def calculate(
        self,
        departure_pressure_altitude_ft: float,
        cruise_pressure_altitude_ft: float,
        temperature_c: float,
        weight_lb: float,
    ) -> ClimbPerformance:
        if self.temperature_policy == ClimbTemperaturePolicy.ISA_BASELINE_10_PERCENT_PER_10C_ABOVE:
            return self._isa_baseline_climb(
                departure_pressure_altitude_ft,
                cruise_pressure_altitude_ft,
                temperature_c,
                weight_lb,
            )
        departure = self.cumulative(
            departure_pressure_altitude_ft,
            temperature_c,
            weight_lb,
        )
        cruise = self.cumulative(
            cruise_pressure_altitude_ft,
            temperature_c,
            weight_lb,
        )
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
            temperature_policy=self.temperature_policy.value,
        )
