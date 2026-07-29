from __future__ import annotations

import pytest

from autonavlog.performance.climb import ClimbCalculator, ClimbPerformanceError
from autonavlog.performance.cruise import CruisePerformanceSelectionPolicy
from autonavlog.performance.repository import PerformanceRepository


def test_climb_interpolation_and_500ft_extrapolation(
    performance_repository: PerformanceRepository,
) -> None:
    calculator = ClimbCalculator(performance_repository.climb_rows)
    interpolated = calculator.cumulative(2500, 10, 3400)
    assert interpolated.time_min == pytest.approx(5.1)
    boundary = calculator.cumulative(6500, 10, 3400)
    assert boundary.warnings == ("EXTRAPOLATED_WITHIN_500FT",)
    with pytest.raises(ClimbPerformanceError):
        calculator.cumulative(6501, 10, 3400)
    with pytest.raises(ClimbPerformanceError):
        calculator.cumulative(5000, 21, 3400)


def test_climb_uses_cumulative_difference(
    performance_repository: PerformanceRepository,
) -> None:
    result = ClimbCalculator(performance_repository.climb_rows).calculate(0, 5000, 10, 3400)
    assert result.time_min == pytest.approx(10)
    assert result.fuel_gal == pytest.approx(4)
    assert result.distance_nm == pytest.approx(15)
    assert result.representative_tas_kt == pytest.approx(90)


def test_cruise_policy_returns_uninterpolated_adverse_cell(
    performance_repository: PerformanceRepository,
) -> None:
    result = CruisePerformanceSelectionPolicy(
        performance_repository.cruise_rows
    ).select(
        pressure_altitude_ft=5000,
        isa_deviation_c=0,
        distance_nm=100,
        true_course_deg=0,
        wind_direction_deg_from=None,
        wind_speed_kt=0,
    )
    assert result.row.pressure_altitude_ft in {4000, 6000}
    assert result.row.isa_deviation_c in {-15, 15}
    assert result.row.power_percent == 65
    assert result.row.isa_deviation_c == 15
