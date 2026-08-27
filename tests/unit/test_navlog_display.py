from autonavlog.application.navlog_display import _display_fuel_combined
from autonavlog.domain.enums import AdoptedSource, DisplayCellState, ValueState
from autonavlog.domain.values import AdoptedValue


def _automatic(value: float) -> AdoptedValue[float]:
    return AdoptedValue[float](
        automatic_value=value,
        automatic_status=ValueState.AUTO,
        adopted_source=AdoptedSource.AUTOMATIC,
    )


def test_fuel_parent_sums_rounded_children_and_subtracts_display_remaining() -> None:
    values = [_automatic(0.14), _automatic(0.14)]
    exact_remaining = _automatic(49.72)

    cell, display_remaining_tenths = _display_fuel_combined(
        values,
        exact_remaining,
        prior_display_remaining_tenths=500,
        fallback_reason="DISPLAY_FUEL_SUBTOTAL_UNAVAILABLE",
    )

    assert cell.state == DisplayCellState.DISPLAY_VALUE
    assert cell.text == "0.2 / 49.8"
    assert cell.effective_value == "0.28/49.72"
    assert display_remaining_tenths == 498
    assert [value.adopted() for value in values] == [0.14, 0.14]
    assert exact_remaining.adopted() == 49.72
