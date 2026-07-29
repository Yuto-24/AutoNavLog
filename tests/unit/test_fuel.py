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
