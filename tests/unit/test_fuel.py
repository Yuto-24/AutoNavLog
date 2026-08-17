from autonavlog.domain.enums import FlightPhase
from autonavlog.nav.fuel import build_fuel_plan, remaining_fuel


def test_unknown_section_fuel_invalidates_following_remaining() -> None:
    assert remaining_fuel(50, [2, None, 3]) == [46.5, None, None]


def test_fuel_plan_fixed_rules() -> None:
    plan = build_fuel_plan(
        81,
        [FlightPhase.CLIMB, FlightPhase.CRUISE, FlightPhase.DESCENT],
        [4, 12, 2],
        1,
    )
    assert plan.taxi_runup_gal == 1.5
    assert plan.additional_gal == 2.8
    assert plan.tgl_gal == 2
    assert plan.reserve_gal == 12.4
    assert plan.min_required_gal == 36.7
    assert plan.extra_gal == 44.3
    assert plan.taxi_runup_minutes == 10
    assert plan.bof_gal == 22.8


def test_run_up_exclusion_changes_required_extra_and_remaining_by_1_5_gal() -> None:
    phases = [FlightPhase.CLIMB, FlightPhase.CRUISE, FlightPhase.DESCENT]
    fuels = [4.0, 12.0, 2.0]
    included = build_fuel_plan(50, phases, fuels, 0, run_up_included=True)
    excluded = build_fuel_plan(50, phases, fuels, 0, run_up_included=False)

    assert included.taxi_runup_minutes == 10
    assert excluded.taxi_runup_minutes == 0
    assert included.taxi_runup_gal == 1.5
    assert excluded.taxi_runup_gal == 0.0
    assert included.min_required_gal == excluded.min_required_gal + 1.5
    assert included.extra_gal == excluded.extra_gal - 1.5
    assert included.bof_gal == excluded.bof_gal == 20.8
    included_remaining = remaining_fuel(50, fuels, run_up_included=True)
    excluded_remaining = remaining_fuel(50, fuels, run_up_included=False)
    assert all(
        excluded_value == included_value + 1.5
        for included_value, excluded_value in zip(
            included_remaining,
            excluded_remaining,
            strict=True,
        )
    )


def test_bof_is_unknown_when_any_route_fuel_component_is_unknown() -> None:
    plan = build_fuel_plan(
        50,
        [FlightPhase.CLIMB, FlightPhase.CRUISE],
        [4.0, None],
        0,
    )
    assert plan.bof_gal is None
