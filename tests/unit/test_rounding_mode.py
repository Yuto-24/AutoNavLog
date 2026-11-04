from math import copysign

import pytest

from autonavlog.domain.project import ManualWind
from autonavlog.nav.rounding import round_half_up
from autonavlog.nav.rounding_mode import (
    rounded_dme_nm,
    rounded_toat_c,
    wind_direction_for_calculation,
)
from autonavlog.nav.wind_triangle import WindTriangleError, solve_wind_triangle


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (0.0, 0.0), (360.0, 0.0), (-0.0, 0.0),
        (4.999, 0.0), (5.0, 10.0), (5.001, 10.0),
        (354.999, 350.0), (355.0, 0.0), (355.001, 0.0),
        (359.999, 0.0), (365.0, 10.0), (-5.0, 0.0), (-5.001, 350.0),
    ],
)
def test_enabled_wind_direction_normalizes_and_rounds(
    direction: float, expected: float
) -> None:
    actual = wind_direction_for_calculation(direction, enabled=True)
    assert actual == expected
    assert actual is not None and 0.0 <= actual < 360.0
    if actual == 0.0:
        assert copysign(1.0, actual) == 1.0


@pytest.mark.parametrize("direction", [0.0, 5.0, 355.0, 360.0, -5.0, 365.0])
def test_disabled_wind_is_unchanged_including_solver_result(direction: float) -> None:
    selected = wind_direction_for_calculation(direction, enabled=False)
    assert selected == direction
    assert solve_wind_triangle(83.0, 143.0, selected, 21.0) == solve_wind_triangle(
        83.0, 143.0, direction, 21.0
    )


@pytest.mark.parametrize(
    ("direction", "expected"), [(5, 10.0), (355, 0.0), (360, 0.0)]
)
def test_domain_normalization_then_rounded_wind_drives_triangle(
    direction: int, expected: float
) -> None:
    wind = ManualWind(direction_deg_from=direction, speed_kt=21.0)
    original = wind.model_dump()
    assert wind.direction_deg_from == direction % 360
    selected = wind_direction_for_calculation(wind.direction_deg_from, enabled=True)
    actual = solve_wind_triangle(83.0, 143.0, selected, wind.speed_kt)
    expected_result = solve_wind_triangle(83.0, 143.0, expected, wind.speed_kt)
    assert selected == expected
    assert actual == expected_result
    if direction != 360:
        raw = solve_wind_triangle(83.0, 143.0, wind.direction_deg_from, wind.speed_kt)
        assert actual.true_heading_deg != pytest.approx(raw.true_heading_deg)
        assert actual.ground_speed_kt != pytest.approx(raw.ground_speed_kt)
    # MH is TH plus the existing east-positive variation. Neither MH nor GS
    # itself is quantized to 10 units by selecting the wind input.
    magnetic_heading = (actual.true_heading_deg + 8.0) % 360.0
    assert magnetic_heading % 10.0 != pytest.approx(0.0)
    assert actual.ground_speed_kt % 10.0 != pytest.approx(0.0)
    assert wind.model_dump() == original


@pytest.mark.parametrize("enabled", [False, True])
def test_missing_direction_is_preserved_and_solver_retains_calm_contract(enabled: bool) -> None:
    selected = wind_direction_for_calculation(None, enabled=enabled)
    assert selected is None
    calm = solve_wind_triangle(83.0, 143.0, selected, 0.0)
    assert calm.calm and calm.ground_speed_kt == 143.0
    with pytest.raises(WindTriangleError, match="direction is required"):
        solve_wind_triangle(83.0, 143.0, selected, 21.0)


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(0.0, 0.0), (1.249, 1.0), (1.25, 1.5), (1.251, 1.5), (1.75, 2.0)],
)
def test_dme_display_projection(distance: float, expected: float) -> None:
    assert rounded_dme_nm(distance) == expected


@pytest.mark.parametrize(
    ("temperature", "expected"),
    [
        (-1.501, -2.0), (-1.5, -2.0), (-1.499, -1.0),
        (-0.501, -1.0), (-0.5, -1.0), (-0.499, 0.0),
        (-0.0, 0.0), (0.0, 0.0), (0.499, 0.0), (0.5, 1.0), (1.5, 2.0),
    ],
)
def test_toat_display_half_up_and_positive_zero(temperature: float, expected: float) -> None:
    actual = rounded_toat_c(temperature)
    assert actual == expected
    if actual == 0.0:
        assert copysign(1.0, actual) == 1.0
        assert f"{actual:.0f}" == "0"


def test_existing_half_up_negative_rule_and_source_values_are_retained() -> None:
    assert round_half_up(-1.5, 1.0) == -2.0
    assert round_half_up(-1.25, 0.5) == -1.5
    # The Python primitive can return -0.0; the TOAT helper removes its sign.
    assert copysign(1.0, round_half_up(-0.1, 1.0)) == -1.0
    exact = {"dme_nm": 1.25, "temperature_c": -1.5}
    assert rounded_dme_nm(exact["dme_nm"]) == 1.5
    assert rounded_toat_c(exact["temperature_c"]) == -2.0
    assert exact == {"dme_nm": 1.25, "temperature_c": -1.5}
    assert rounded_dme_nm(None) is None
    assert rounded_toat_c(None) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_values_are_rejected_instead_of_becoming_valid_numbers(value: float) -> None:
    for enabled in (False, True):
        with pytest.raises(ValueError, match="finite"):
            wind_direction_for_calculation(value, enabled=enabled)
    with pytest.raises(ValueError, match="finite"):
        rounded_dme_nm(value)
    with pytest.raises(ValueError, match="finite"):
        rounded_toat_c(value)


def test_rounding_can_make_an_otherwise_solvable_crosswind_unusable() -> None:
    raw = solve_wind_triangle(10, 100, 101, 100)
    assert raw.ground_speed_kt == pytest.approx(3.4904812874567)
    with pytest.raises(WindTriangleError):
        solve_wind_triangle(10, 100, wind_direction_for_calculation(101, enabled=True), 100)
