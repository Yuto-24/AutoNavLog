from __future__ import annotations

from dataclasses import dataclass

from autonavlog.nav.wind_triangle import WindTriangleError, solve_wind_triangle

from .schemas import CruiseRow


class CruisePerformanceError(ValueError):
    pass


@dataclass(frozen=True)
class CruiseSelection:
    row: CruiseRow
    ete_seconds: float
    fuel_gal: float
    ground_speed_kt: float
    reason: str
    warnings: tuple[str, ...] = ()


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
        altitude_candidates = _candidate_axis(
            [row.pressure_altitude_ft for row in self.rows],
            pressure_altitude_ft,
        )
        temperature_candidates = _candidate_axis(
            [row.isa_deviation_c for row in self.rows],
            isa_deviation_c,
        )
        selections: list[CruiseSelection] = []
        for altitude in altitude_candidates:
            for temperature in temperature_candidates:
                group = [
                    row
                    for row in self.rows
                    if row.pressure_altitude_ft == altitude
                    and row.isa_deviation_c == temperature
                    and row.power_percent <= 85
                ]
                if not group:
                    continue
                row = min(group, key=lambda item: abs(item.power_percent - 65))
                try:
                    wind = solve_wind_triangle(
                        true_course_deg,
                        row.ktas,
                        wind_direction_deg_from,
                        wind_speed_kt,
                    )
                except WindTriangleError:
                    continue
                ete_seconds = distance_nm / wind.ground_speed_kt * 3600.0
                fuel = row.gph * ete_seconds / 3600.0
                warnings = () if row.power_percent == 65 else ("PWR_NOT_EXACTLY_65_PERCENT",)
                selections.append(
                    CruiseSelection(
                        row,
                        ete_seconds,
                        fuel,
                        wind.ground_speed_kt,
                        "maximum fuel, then maximum ETE, then lowest KTAS",
                        warnings,
                    )
                )
        if not selections:
            raise CruisePerformanceError("no usable 65% cruise performance candidate")
        return max(
            selections,
            key=lambda item: (item.fuel_gal, item.ete_seconds, -item.row.ktas),
        )
