from __future__ import annotations

from autonavlog.application.calculation_service import CalculationService
from autonavlog.domain.enums import FlightPhase, ValueState
from autonavlog.weather.fake_provider import FakeWeatherProvider


def _source_rows(outcome, section_id):
    return [
        row
        for row in outcome.sections
        if row.performance_metadata["phase_segment"]["source_section_id"] == str(section_id)
    ]


def test_rca_split_cruise_tas_does_not_override_climb_cas_111(
    airports, performance_repository, project
) -> None:
    section = project.sections[0]
    section.manual_tas_kt_by_phase = {FlightPhase.CRUISE: 140.0}

    outcome = CalculationService(airports, performance_repository).calculate(
        project, FakeWeatherProvider()
    )

    rows = _source_rows(outcome, section.id)
    climb = next(row for row in rows if row.phase == FlightPhase.CLIMB)
    cruise = next(row for row in rows if row.phase == FlightPhase.CRUISE)
    assert climb.cas_kt.adopted() == 111.0
    assert climb.tas_kt.state != ValueState.MANUAL_OVERRIDE
    assert cruise.tas_kt.adopted() == 140.0
    assert cruise.tas_kt.state == ValueState.MANUAL_OVERRIDE


def test_rca_split_climb_tas_does_not_override_post_rca_cruise(
    airports, performance_repository, project
) -> None:
    section = project.sections[0]
    section.manual_tas_kt_by_phase = {FlightPhase.CLIMB: 130.0}

    outcome = CalculationService(airports, performance_repository).calculate(
        project, FakeWeatherProvider()
    )

    rows = _source_rows(outcome, section.id)
    climb = next(row for row in rows if row.phase == FlightPhase.CLIMB)
    cruise = next(row for row in rows if row.phase == FlightPhase.CRUISE)
    assert climb.tas_kt.adopted() == 130.0
    assert climb.tas_kt.state == ValueState.MANUAL_OVERRIDE
    assert cruise.tas_kt.state != ValueState.MANUAL_OVERRIDE
    assert cruise.tas_kt.adopted() != 130.0
