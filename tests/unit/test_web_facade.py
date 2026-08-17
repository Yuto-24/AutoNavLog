from __future__ import annotations

import pytest

from autonavlog.application.rjfm_departure_plan import (
    RjfmPlanReferences,
    apply_rjfm_departure_exception,
)
from autonavlog.domain.enums import FlightPhase
from autonavlog.domain.planning import RjfmCoordinate
from autonavlog.web.facade import AutoNavLogWebApplication


def _coordinate(latitude: float, longitude: float, source: str) -> RjfmCoordinate:
    return RjfmCoordinate(
        latitude_deg=latitude,
        longitude_deg=longitude,
        source=source,
        estimated_error_nm=0.2,
    )


def _rjfm_references(project, *, omaru_first: bool = False) -> RjfmPlanReferences:
    first = project.ordered_nodes()[1]
    return RjfmPlanReferences(
        revision="fixture-rjfm-input-v2",
        content_fingerprint="d" * 64,
        umk=(
            _coordinate(32.0, 131.50, "fixture:UMK")
            if omaru_first
            else _coordinate(first.latitude_deg, first.longitude_deg, "fixture:UMK")
        ),
        over_field=_coordinate(32.1, 131.53, "fixture:OVER_FIELD"),
        omaru=(
            _coordinate(first.latitude_deg, first.longitude_deg, "fixture:OMARU")
            if omaru_first
            else _coordinate(32.6, 131.62, "fixture:OMARU")
        ),
    )


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


def test_rjfm_physical_umk_sections_expose_fixed_input_modes(project) -> None:
    assert apply_rjfm_departure_exception(project, _rjfm_references(project)) is not None

    guidance = AutoNavLogWebApplication._section_guidance(project)

    assert [item["inputMode"] for item in guidance[:2]] == [
        "RJFM_DEPARTURE_TO_UMK_FIXED",
        "RJFM_UMK_TO_OMARU_FIXED",
    ]
    assert [item["fixedAltitudeFtMsl"] for item in guidance[:2]] == [5500.0, 5500.0]
    assert all(not item["appliesToCruisingAltitudeInput"] for item in guidance[:2])
    assert guidance[2]["inputMode"] == "EDITABLE"
    assert guidance[2]["fixedAltitudeFtMsl"] is None


def test_rjfm_omaru_first_parent_exposes_one_fixed_input_mode(project) -> None:
    first = project.ordered_nodes()[1]
    first.latitude_deg = 32.6
    first.longitude_deg = 131.62
    assert (
        apply_rjfm_departure_exception(
            project,
            _rjfm_references(project, omaru_first=True),
        )
        is not None
    )

    guidance = AutoNavLogWebApplication._section_guidance(project)

    assert guidance[0]["inputMode"] == "RJFM_PARENT_CONTAINS_UMK_FIXED"
    assert guidance[0]["fixedAltitudeFtMsl"] == 5500.0
    assert guidance[1]["inputMode"] == "EDITABLE"
