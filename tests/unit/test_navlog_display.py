from types import SimpleNamespace
from uuid import uuid4

from autonavlog.application.navlog_display import (
    NavLogPhysicalLeg,
    _allocate_raw_largest_remainder_ticks,
    _display_distance_cells,
    _display_fuel_combined,
)
from autonavlog.domain.enums import AdoptedSource, DisplayCellState, FlightPhase, ValueState
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


def test_distance_children_allocate_the_rounded_parent_ticks_in_route_order() -> None:
    """A display total of 1.0 NM has two ticks, not three independently rounded zones."""
    zones = [SimpleNamespace(zone_distance_nm=_automatic(value)) for value in (0.3, 0.3, 0.4)]
    leg = NavLogPhysicalLeg(
        section_ids=(uuid4(),),
        phase=FlightPhase.CRUISE,
        start_name="FROM",
        end_name="TO",
        adopted_distance_nm=1.0,
    )

    parent, cumulative, children = _display_distance_cells(
        leg,
        zones,
        prior_display_cumulative=2.5,
    )

    assert parent.text == "1.0 / 3.5"
    assert cumulative == 3.5
    # The two equal fractional remainders use the earlier Zone as the tie-break.
    assert [cell.text for cell in children] == ["0.5", "0.0", "0.5"]
    assert [cell.effective_value for cell in children] == [0.3, 0.3, 0.4]
    assert all(cell.state == DisplayCellState.DISPLAY_VALUE for cell in children)


def test_distance_allocation_fails_closed_when_zone_distances_do_not_match_leg() -> None:
    zones = [SimpleNamespace(zone_distance_nm=_automatic(value)) for value in (0.3, 0.4)]
    leg = NavLogPhysicalLeg(
        section_ids=(uuid4(),),
        phase=FlightPhase.CRUISE,
        start_name="FROM",
        end_name="TO",
        adopted_distance_nm=1.0,
    )

    parent, cumulative, children = _display_distance_cells(
        leg,
        zones,
        prior_display_cumulative=0.0,
    )

    assert parent.state == DisplayCellState.UNAVAILABLE
    assert parent.reason_code == "DISPLAY_DISTANCE_ALLOCATION_UNAVAILABLE"
    assert cumulative is None
    assert all(cell.state == DisplayCellState.UNAVAILABLE for cell in children)


def test_rjfm_inbound_ete_keeps_raw_unit_largest_remainder_behavior() -> None:
    """ETE must not proportionally normalize the once-rounded profile total."""
    assert _allocate_raw_largest_remainder_ticks([0.25, 1.25], 2) == [1, 1]


def test_rjfm_inbound_ete_keeps_zero_duration_candidates() -> None:
    assert _allocate_raw_largest_remainder_ticks([0.0, 0.0], 0) == [0, 0]
