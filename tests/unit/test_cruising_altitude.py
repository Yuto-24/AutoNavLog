from __future__ import annotations

import pytest

from autonavlog.web.cruising_altitude import (
    LEGAL_THRESHOLD_NOTE_JA,
    TERRAIN_LIMITATION_NOTE_JA,
    magnetic_course_deg,
    matches_vfr_cruising_altitude,
    operational_magnetic_course_deg,
    vfr_cruising_altitude_candidates,
)


@pytest.mark.parametrize(
    ("magnetic_course", "first_candidate"),
    [
        (0.0, 3500),
        (179.499, 3500),
        (179.5, 4500),
        (179.999, 4500),
        (180.0, 4500),
        (359.499, 4500),
        (359.5, 3500),
        (359.999, 3500),
        (360.0, 3500),
        (-0.001, 3500),
    ],
)
def test_vfr_candidates_follow_magnetic_course_semicircle(
    magnetic_course: float,
    first_candidate: int,
) -> None:
    candidates = vfr_cruising_altitude_candidates(magnetic_course)

    assert candidates[0] == first_candidate
    assert all(
        next_value - value == 2000
        for value, next_value in zip(candidates, candidates[1:], strict=False)
    )
    assert candidates[-1] <= 25_000
    assert candidates[-1] == (23_500 if first_candidate == 3500 else 24_500)


def test_candidates_cover_the_full_below_29000_foot_legal_table_when_requested() -> None:
    assert vfr_cruising_altitude_candidates(90, maximum_ft_msl=28_500)[-1] == 27_500
    assert vfr_cruising_altitude_candidates(270, maximum_ft_msl=28_500)[-1] == 28_500


def test_magnetic_course_applies_east_variation_and_wraps() -> None:
    assert magnetic_course_deg(284.0, 8.0) == pytest.approx(292.0)
    assert magnetic_course_deg(356.0, 8.0) == pytest.approx(4.0)


@pytest.mark.parametrize(
    ("exact_course", "operational_course"),
    [
        (179.499, 179.0),
        (179.5, 180.0),
        (180.499, 180.0),
        (359.499, 359.0),
        (359.5, 0.0),
        (0.499, 0.0),
        (0.5, 1.0),
    ],
)
def test_operational_course_matches_whole_degree_nav_log_entry(
    exact_course: float,
    operational_course: float,
) -> None:
    assert operational_magnetic_course_deg(exact_course) == operational_course


def test_custom_altitude_match_is_exact_to_display_precision() -> None:
    assert matches_vfr_cruising_altitude(3500, 90)
    assert not matches_vfr_cruising_altitude(4500, 90)
    assert matches_vfr_cruising_altitude(4500, 270)
    assert not matches_vfr_cruising_altitude(3500, 270)


def test_guidance_states_legal_threshold_and_terrain_limitation() -> None:
    assert "900 m以上" in LEGAL_THRESHOLD_NOTE_JA
    assert "地表高を判定しない" in TERRAIN_LIMITATION_NOTE_JA
