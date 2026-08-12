from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from autonavlog.nav.wind_triangle import WindTriangleError, solve_wind_triangle

from .schemas import CruiseInterpolatedRow, CruiseRow


class CruisePerformanceError(ValueError):
    pass


@dataclass(frozen=True)
class CruiseSelection:
    row: CruiseRow | CruiseInterpolatedRow
    ete_seconds: float
    fuel_gal: float
    ground_speed_kt: float
    reason: str
    warnings: tuple[str, ...] = ()
    interpolation: CruiseInterpolationTrace | None = None


@dataclass(frozen=True)
class AxisBracket:
    lower: float
    upper: float
    fraction: float


@dataclass(frozen=True)
class PowerInterpolationCorner:
    pressure_altitude_ft: float
    isa_deviation_c: float
    power: AxisBracket
    source_pages: tuple[str, ...]


@dataclass(frozen=True)
class CruiseInterpolationTrace:
    method: str
    altitude: AxisBracket
    isa_deviation: AxisBracket
    power_percent: float
    corners: tuple[PowerInterpolationCorner, ...]


def _candidate_axis(values: list[float], target: float) -> tuple[float, ...]:
    ordered = sorted(set(values))
    if target in ordered:
        return (target,)
    if not ordered or target < ordered[0] or target > ordered[-1]:
        raise CruisePerformanceError("cruise condition is outside the performance table")
    for lower, upper in zip(ordered, ordered[1:], strict=False):
        if lower < target < upper:
            return lower, upper
    raise CruisePerformanceError("unable to bracket cruise condition")


def _axis_bracket(values: list[float], target: float, label: str) -> AxisBracket:
    if not isfinite(target):
        raise CruisePerformanceError(f"{label} must be finite")
    candidates = _candidate_axis(values, target)
    lower = candidates[0]
    upper = candidates[-1]
    fraction = 0.0 if lower == upper else (target - lower) / (upper - lower)
    return AxisBracket(lower, upper, fraction)


def _linear(bracket: AxisBracket, lower: float, upper: float) -> float:
    return lower + bracket.fraction * (upper - lower)


class CruisePerformanceSelectionPolicy:
    def __init__(self, rows: list[CruiseRow]):
        self.rows = rows

    def select(
        self,
        pressure_altitude_ft: float,
        isa_deviation_c: float,
        distance_nm: float,
        true_course_deg: float,
        wind_direction_deg_from: float | None,
        wind_speed_kt: float,
    ) -> CruiseSelection:
        if not all(
            isfinite(value)
            for value in (
                distance_nm,
                true_course_deg,
                wind_speed_kt,
            )
        ) or distance_nm < 0:
            raise CruisePerformanceError("cruise route inputs must be finite and non-negative")
        altitude = _axis_bracket(
            [row.pressure_altitude_ft for row in self.rows],
            pressure_altitude_ft,
            "pressure altitude",
        )
        isa_deviation = _axis_bracket(
            [row.isa_deviation_c for row in self.rows],
            isa_deviation_c,
            "ISA deviation",
        )
        power_percent = 65.0
        corner_traces: list[PowerInterpolationCorner] = []

        def interpolate_power(
            pressure_altitude: float,
            temperature: float,
            field: str,
        ) -> float:
            rows = [
                row
                for row in self.rows
                if row.pressure_altitude_ft == pressure_altitude
                and row.isa_deviation_c == temperature
            ]
            power = _axis_bracket(
                [row.power_percent for row in rows],
                power_percent,
                "65% power",
            )
            indexed = {row.power_percent: row for row in rows}
            if power.lower not in indexed or power.upper not in indexed:
                raise CruisePerformanceError(
                    "cruise table has a missing power interpolation corner"
                )
            if field == "ktas":
                corner_traces.append(
                    PowerInterpolationCorner(
                        pressure_altitude,
                        temperature,
                        power,
                        tuple(
                            dict.fromkeys(
                                (
                                    indexed[power.lower].source_page,
                                    indexed[power.upper].source_page,
                                )
                            )
                        ),
                    )
                )
            return _linear(
                power,
                float(getattr(indexed[power.lower], field)),
                float(getattr(indexed[power.upper], field)),
            )

        def interpolate_temperature(pressure_altitude: float, field: str) -> float:
            lower = interpolate_power(pressure_altitude, isa_deviation.lower, field)
            upper = (
                lower
                if isa_deviation.lower == isa_deviation.upper
                else interpolate_power(pressure_altitude, isa_deviation.upper, field)
            )
            return _linear(isa_deviation, lower, upper)

        interpolated: dict[str, float] = {}
        for field in ("ktas", "gph"):
            lower = interpolate_temperature(altitude.lower, field)
            upper = (
                lower
                if altitude.lower == altitude.upper
                else interpolate_temperature(altitude.upper, field)
            )
            # The Issue #15 workbook publishes KTAS/GPH to one decimal place.
            # Python's ties-to-even round reproduces all 6,419 workbook rows.
            interpolated[field] = round(_linear(altitude, lower, upper), 1)

        trace = CruiseInterpolationTrace(
            method="PWR_LINEAR_THEN_ISA_LINEAR_THEN_ALTITUDE_LINEAR_NO_EXTRAPOLATION",
            altitude=altitude,
            isa_deviation=isa_deviation,
            power_percent=power_percent,
            corners=tuple(corner_traces),
        )
        source_pages = tuple(
            dict.fromkeys(page for corner in corner_traces for page in corner.source_pages)
        )
        row = CruiseInterpolatedRow(
            pressure_altitude_ft=pressure_altitude_ft,
            isa_deviation_c=isa_deviation_c,
            rpm=None,
            map_in_hg=None,
            power_percent=power_percent,
            ktas=interpolated["ktas"],
            gph=interpolated["gph"],
            source_page=" / ".join(source_pages),
        )
        try:
            wind = solve_wind_triangle(
                true_course_deg,
                row.ktas,
                wind_direction_deg_from,
                wind_speed_kt,
            )
        except WindTriangleError as error:
            raise CruisePerformanceError(str(error)) from error
        ete_seconds = distance_nm / wind.ground_speed_kt * 3600.0
        fuel = row.gph * ete_seconds / 3600.0
        return CruiseSelection(
            row,
            ete_seconds,
            fuel,
            wind.ground_speed_kt,
            trace.method,
            (),
            trace,
        )
