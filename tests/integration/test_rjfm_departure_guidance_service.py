from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.rjfm_departure_service import (
    MAX_DISPLAY_PATH_POINTS,
    _downsample_path,
    build_rjfm_departure_guidance,
    normalize_rjfm_departure_plan,
)
from autonavlog.domain.planning import (
    PersistedUiState,
    RjfmGuidanceStatus,
    load_persisted_ui_state,
)
from autonavlog.storage.rjfm_reference import RjfmReferencePack
from autonavlog.weather.fake_provider import FakeWeatherProvider

ROOT = Path(__file__).resolve().parents[2]


def _calculation_service(
    airports,
    performance_repository,
    pack: RjfmReferencePack,
) -> CalculationService:
    return CalculationService(
        airports,
        performance_repository,
        expected_rjfm_reference_revision=pack.revision,
        expected_rjfm_reference_content_fingerprint=pack.content_fingerprint,
    )


@pytest.mark.parametrize("point_count", [601, 1000, 1198, 1200, 2400])
def test_display_path_downsampling_caps_size_and_preserves_endpoints(
    point_count: int,
) -> None:
    path = tuple(range(point_count))

    displayed = _downsample_path(path)

    assert len(displayed) <= MAX_DISPLAY_PATH_POINTS
    assert displayed[0] == path[0]
    assert displayed[-1] == path[-1]
    assert all(left < right for left, right in zip(displayed, displayed[1:], strict=False))


def test_guidance_uses_calculated_climb_inputs_and_keeps_failures_diagnostic(
    airports,
    performance_repository,
    project,
) -> None:
    pack = RjfmReferencePack.from_directory(ROOT / "data" / "reference" / "rjfm")
    working = project.model_copy(deep=True)
    first = working.ordered_nodes()[1]
    first.name = "KML FIRST"
    first.latitude_deg = pack.points["UMK"].position.latitude_deg
    first.longitude_deg = pack.points["UMK"].position.longitude_deg

    plan = normalize_rjfm_departure_plan(working, pack)
    assert plan is not None
    outcome = _calculation_service(airports, performance_repository, pack).calculate(
        working,
        FakeWeatherProvider(),
    )
    state = load_persisted_ui_state(working.metadata["ui_state"])

    guidance = build_rjfm_departure_guidance(
        working,
        outcome,
        state,
        performance_repository,
        pack,
        generated_against_fingerprint="a" * 64,
    )

    assert guidance is not None
    assert guidance.generated_against_fingerprint == "a" * 64
    assert guidance.reference_content_fingerprint == pack.content_fingerprint
    assert [candidate.runway for candidate in guidance.candidates] == ["09", "27"]
    assert [point.source for point in guidance.center_route][0] == "KML:UMK"
    assert all(
        candidate.path or candidate.constraints
        for candidate in guidance.candidates
    )
    assert any(candidate.path for candidate in guidance.candidates)
    for candidate in guidance.candidates:
        if candidate.path:
            assert candidate.path[-1].altitude_ft_msl == pytest.approx(5500, abs=10)
            assert candidate.expected_time_delta_seconds is not None
        if candidate.status in {RjfmGuidanceStatus.VALID, RjfmGuidanceStatus.WARNING}:
            assert all(
                constraint.passed
                for constraint in candidate.constraints
                if constraint.hard
            )
    assert not any(issue.code.startswith("RJFM_GUIDANCE") for issue in outcome.issues)


def test_non_matching_route_has_no_guidance(
    airports,
    performance_repository,
    project,
) -> None:
    pack = RjfmReferencePack.from_directory(ROOT / "data" / "reference" / "rjfm")
    outcome = _calculation_service(airports, performance_repository, pack).calculate(
        project,
        FakeWeatherProvider(),
    )
    state = PersistedUiState()

    assert (
        build_rjfm_departure_guidance(
            project,
            outcome,
            state,
            performance_repository,
            pack,
            generated_against_fingerprint="b" * 64,
        )
        is None
    )


def test_stale_plan_cannot_be_relabelled_with_current_pack_provenance(
    airports,
    performance_repository,
    project,
) -> None:
    pack = RjfmReferencePack.from_directory(ROOT / "data" / "reference" / "rjfm")
    working = project.model_copy(deep=True)
    first = working.ordered_nodes()[1]
    first.latitude_deg = pack.points["UMK"].position.latitude_deg
    first.longitude_deg = pack.points["UMK"].position.longitude_deg
    plan = normalize_rjfm_departure_plan(working, pack)
    assert plan is not None
    outcome = _calculation_service(airports, performance_repository, pack).calculate(
        working,
        FakeWeatherProvider(),
    )
    state = load_persisted_ui_state(working.metadata["ui_state"])
    stale_state = state.model_copy(
        update={
            "rjfm_departure_plan": plan.model_copy(
                update={"virtual_rca_distance_nm": plan.virtual_rca_distance_nm * 0.5}
            )
        }
    )

    guidance = build_rjfm_departure_guidance(
        working,
        outcome,
        stale_state,
        performance_repository,
        pack,
        generated_against_fingerprint="c" * 64,
    )

    assert guidance is not None
    assert guidance.reference_revision == pack.revision
    assert guidance.reference_content_fingerprint == pack.content_fingerprint
    assert all(
        candidate.status == RjfmGuidanceStatus.UNAVAILABLE
        for candidate in guidance.candidates
    )
    assert all(point.source.startswith("RJFM_REFERENCE:") for point in guidance.center_route)
    assert "一致しません" in guidance.candidates[0].constraints[0].message


def test_guidance_rejects_outcome_from_another_project(
    airports,
    performance_repository,
    project,
) -> None:
    pack = RjfmReferencePack.from_directory(ROOT / "data" / "reference" / "rjfm")
    working = project.model_copy(deep=True)
    first = working.ordered_nodes()[1]
    first.latitude_deg = pack.points["UMK"].position.latitude_deg
    first.longitude_deg = pack.points["UMK"].position.longitude_deg
    plan = normalize_rjfm_departure_plan(working, pack)
    assert plan is not None
    outcome = _calculation_service(airports, performance_repository, pack).calculate(
        working,
        FakeWeatherProvider(),
    )
    foreign_outcome = outcome.model_copy(update={"project_id": uuid4()})
    state = load_persisted_ui_state(working.metadata["ui_state"])

    guidance = build_rjfm_departure_guidance(
        working,
        foreign_outcome,
        state,
        performance_repository,
        pack,
        generated_against_fingerprint="d" * 64,
    )

    assert guidance is not None
    assert all(
        candidate.status == RjfmGuidanceStatus.UNAVAILABLE
        for candidate in guidance.candidates
    )
    assert all(point.source.startswith("RJFM_REFERENCE:") for point in guidance.center_route)
    assert all(
        "現在のProjectに属していない" in candidate.constraints[0].message
        for candidate in guidance.candidates
    )
