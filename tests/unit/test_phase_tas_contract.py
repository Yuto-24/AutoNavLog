from __future__ import annotations

from autonavlog.domain.enums import FlightPhase


def test_phase_tas_overrides_are_isolated_within_one_physical_section(project) -> None:
    """RCA/EOC zones may share a source section but never a TAS override."""
    section = project.sections[0]
    section.phase = FlightPhase.CLIMB
    section.manual_tas_kt_by_phase = {FlightPhase.CRUISE: 142.0}

    assert section.manual_tas_for_phase(FlightPhase.CRUISE) == 142.0
    assert section.manual_tas_for_phase(FlightPhase.CLIMB) is None
    assert section.manual_tas_for_phase(FlightPhase.DESCENT) is None


def test_descent_override_is_distinct_from_pre_eoc_cruise_override(project) -> None:
    section = project.sections[0]
    section.phase = FlightPhase.DESCENT
    section.manual_tas_kt_by_phase = {
        FlightPhase.CRUISE: 145.0,
        FlightPhase.DESCENT: 120.0,
    }

    assert section.manual_tas_for_phase(FlightPhase.CRUISE) == 145.0
    assert section.manual_tas_for_phase(FlightPhase.DESCENT) == 120.0
