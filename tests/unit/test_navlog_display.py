from types import SimpleNamespace
from uuid import uuid4

from autonavlog.application.navlog_display import (
    NavLogPhysicalLeg,
    _allocate_raw_largest_remainder_ticks,
    _display_distance_cells,
    _display_fuel_combined,
    build_navlog_summary,
)
from autonavlog.domain.calculation import SectionResult
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


def _summary_zone() -> SectionResult:
    """Only distance/time values are populated by each summary test."""
    return SectionResult(
        section_id=uuid4(),
        sequence=0,
        phase=FlightPhase.CLIMB,
        from_name="FROM",
        to_name="RCA",
        planned_altitude_ft_msl=_automatic_value(None),
        pressure_altitude_exact_ft=_automatic_value(None),
        pressure_altitude_planning_ft=_automatic_value(None),
        true_course_deg=_automatic_value(None),
        variation_deg_east=_automatic_value(None),
        magnetic_course_deg=_automatic_value(None),
        wind_direction_deg_from=_automatic_value(None),
        wind_speed_kt=_automatic_value(None),
        wca_deg=_automatic_value(None),
        magnetic_heading_deg=_automatic_value(None),
        temperature_c=_automatic_value(None),
        cas_kt=_automatic_value(None),
        tas_kt=_automatic_value(None),
        ground_speed_kt=_automatic_value(None),
        zone_distance_nm=_automatic_value(None),
        cumulative_distance_nm=_automatic_value(None),
        zone_ete_seconds=_automatic_value(None),
        cumulative_ete_seconds=_automatic_value(None),
        section_fuel_gal=_automatic_value(None),
        remaining_fuel_gal=_automatic_value(None),
    )


def _automatic_value(value: float | None) -> AdoptedValue[float]:
    return AdoptedValue(
        automatic_value=value,
        automatic_status=ValueState.AUTO if value is not None else ValueState.UNAVAILABLE,
        adopted_source=AdoptedSource.AUTOMATIC if value is not None else None,
    )


def test_navlog_summary_uses_canonical_split_zones_and_hhmm_rounding() -> None:
    source = _summary_zone()
    first = source.model_copy(
        update={
            "sequence": 0,
            "zone_distance_nm": _automatic_value(1.2),
            "cumulative_distance_nm": _automatic_value(1.2),
            "zone_ete_seconds": _automatic_value(29.5 * 60),
            "cumulative_ete_seconds": _automatic_value(29.5 * 60),
        }
    )
    second = source.model_copy(
        update={
            "sequence": 1,
            "zone_distance_nm": _automatic_value(1.3),
            "cumulative_distance_nm": _automatic_value(2.5),
            "zone_ete_seconds": _automatic_value(30 * 60),
            "cumulative_ete_seconds": _automatic_value(59.5 * 60),
        }
    )
    summary = build_navlog_summary(
        [first, second],
        [
            NavLogPhysicalLeg(
                section_ids=(source.section_id,),
                phase=source.phase,
                start_name=source.from_name,
                end_name=source.to_name,
                adopted_distance_nm=2.5,
            )
        ],
    )

    assert summary.distance.text == "2.5"
    assert summary.time.text == "1:00"


def test_navlog_summary_keeps_distance_when_an_early_ete_is_unavailable() -> None:
    first = _summary_zone()
    unavailable_first = first.model_copy(
        update={
            "zone_distance_nm": _automatic_value(1.2),
            "zone_ete_seconds": _automatic_value(None),
        }
    )
    available_second = first.model_copy(
        update={
            "section_id": uuid4(),
            "sequence": 1,
            "zone_distance_nm": _automatic_value(2.0),
            "zone_ete_seconds": _automatic_value(60.0),
        }
    )
    summary = build_navlog_summary(
        [unavailable_first, available_second],
        [
            NavLogPhysicalLeg(
                section_ids=(first.section_id,),
                phase=first.phase,
                start_name=first.from_name,
                end_name=first.to_name,
                adopted_distance_nm=1.2,
            ),
            NavLogPhysicalLeg(
                section_ids=(available_second.section_id,),
                phase=available_second.phase,
                start_name=available_second.from_name,
                end_name=available_second.to_name,
                adopted_distance_nm=2.0,
            ),
        ],
    )

    assert summary.distance.text == "3.0"
    assert summary.time.text == "未取得"
    assert summary.time.reason_code == "SUMMARY_TIME_UNAVAILABLE"
