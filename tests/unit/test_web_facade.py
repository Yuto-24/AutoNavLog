from __future__ import annotations

import pytest

from autonavlog.domain.enums import FlightPhase
from autonavlog.web.facade import AutoNavLogWebApplication


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        (".", "route"),
        ("..", "route"),
        ("route. ", "route"),
        ("a" * 59 + ".x", "a" * 59),
        ("valid-name", "valid-name"),
    ],
)
def test_project_name_normalization_never_leaves_a_reserved_dot_suffix(
    raw_name: str,
    expected: str,
) -> None:
    assert AutoNavLogWebApplication._normalize_project_name(raw_name) == expected


@pytest.mark.parametrize(
    "phase",
    [FlightPhase.CLIMB, FlightPhase.CRUISE, FlightPhase.DESCENT],
)
def test_cruising_altitude_guidance_applies_to_climb_cruise_and_descent(
    project,
    phase: FlightPhase,
) -> None:
    section = project.sections[0]
    section.phase = phase

    guidance = next(
        item
        for item in AutoNavLogWebApplication._section_guidance(project)
        if item["sectionId"] == str(section.id)
    )
    section.planned_altitude_ft_msl = guidance["candidateAltitudesFtMsl"][0]
    matching_guidance = next(
        item
        for item in AutoNavLogWebApplication._section_guidance(project)
        if item["sectionId"] == str(section.id)
    )

    assert guidance["appliesToCruisingAltitudeInput"] is True
    assert guidance["appliesToCruise"] is (phase == FlightPhase.CRUISE)
    assert matching_guidance["requiresReview"] is False

    section.planned_altitude_ft_msl = 3_000
    non_matching_guidance = next(
        item
        for item in AutoNavLogWebApplication._section_guidance(project)
        if item["sectionId"] == str(section.id)
    )
    assert non_matching_guidance["requiresReview"] is True


def test_cruising_altitude_guidance_does_not_apply_to_visual_arrival(project) -> None:
    section = project.sections[0]
    section.phase = FlightPhase.VISUAL_ARRIVAL
    section.planned_altitude_ft_msl = 3_000

    guidance = next(
        item
        for item in AutoNavLogWebApplication._section_guidance(project)
        if item["sectionId"] == str(section.id)
    )

    assert guidance["appliesToCruisingAltitudeInput"] is False
    assert guidance["appliesToCruise"] is False
    assert guidance["requiresReview"] is False
