from __future__ import annotations

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.rjfm_departure_plan import (
    RjfmPlanReferences,
    apply_rjfm_departure_exception,
)
from autonavlog.domain.enums import DisplayCellState, FlightPhase
from autonavlog.domain.planning import RjfmCoordinate, load_persisted_ui_state
from autonavlog.nav.geodesy import geodesic_leg
from autonavlog.weather.fake_provider import FakeWeatherProvider


def _coordinate(latitude: float, longitude: float, name: str) -> RjfmCoordinate:
    return RjfmCoordinate(
        latitude_deg=latitude,
        longitude_deg=longitude,
        source=f"fixture:{name}",
        estimated_error_nm=0.2,
    )


def _references(*, umk: tuple[float, float], omaru: tuple[float, float]) -> RjfmPlanReferences:
    return RjfmPlanReferences(
        revision="fixture-rjfm-v1",
        content_fingerprint="b" * 64,
        umk=_coordinate(*umk, "UMK"),
        over_field=_coordinate(32.10, 131.47, "OVER_FIELD"),
        omaru=_coordinate(*omaru, "OMARU"),
    )


def _calculation_service(airports, performance_repository) -> CalculationService:
    return CalculationService(
        airports,
        performance_repository,
        expected_rjfm_reference_revision="fixture-rjfm-v1",
        expected_rjfm_reference_content_fingerprint="b" * 64,
    )


def _adopted_route_distance(project) -> float:
    nodes = project.ordered_nodes()
    return sum(
        start.manual_distance_nm
        if start.manual_distance_nm is not None
        else geodesic_leg(
            start.latitude_deg,
            start.longitude_deg,
            end.latitude_deg,
            end.longitude_deg,
        ).distance_nm
        for start, end in zip(nodes, nodes[1:], strict=False)
    )


def test_umk_physical_rca_uses_poh_ete_without_changing_direct_gs(
    airports,
    performance_repository,
    project,
) -> None:
    working = project.model_copy(deep=True)
    turn = working.ordered_nodes()[1]
    references = _references(
        umk=(turn.latitude_deg, turn.longitude_deg),
        omaru=(32.60, 131.62),
    )
    plan = apply_rjfm_departure_exception(working, references)
    assert plan is not None

    outcome = _calculation_service(airports, performance_repository).calculate(
        working,
        FakeWeatherProvider(),
    )

    assert not any(
        issue.code in {"PHASE_SEGMENTATION_FAILED", "RJFM_UMK_RCA_OUTSIDE_ROUTE"}
        for issue in outcome.issues
    )
    climb = [result for result in outcome.sections if result.phase == FlightPhase.CLIMB]
    assert climb
    assert climb[-1].to_name == "UMK/RCA"
    planned_duration = climb[0].performance_metadata["planned_duration_seconds"]
    assert sum(result.zone_ete_seconds.adopted() or 0 for result in climb) == pytest.approx(
        planned_duration,
        abs=1e-6,
    )
    direct_time = sum(
        (result.zone_distance_nm.adopted() or 0)
        / (result.ground_speed_kt.adopted() or 1)
        * 3600
        for result in climb
    )
    assert direct_time != pytest.approx(planned_duration, abs=1e-3)
    assert sum(result.section_fuel_gal.adopted() or 0 for result in climb) == pytest.approx(
        climb[0].performance_metadata["planned_fuel_gal"],
        abs=1e-6,
    )


def test_umk_physical_navlog_groups_rjfm_through_omaru_with_direct_parent_course(
    airports,
    performance_repository,
    project,
) -> None:
    working = project.model_copy(deep=True)
    umk = working.ordered_nodes()[1]
    plan = apply_rjfm_departure_exception(
        working,
        _references(
            umk=(umk.latitude_deg, umk.longitude_deg),
            omaru=(32.60, 131.62),
        ),
    )
    assert plan is not None
    controlled_section_ids = tuple(
        section.id for section in working.ordered_sections()[:2]
    )

    outcome = _calculation_service(airports, performance_repository).calculate(
        working,
        FakeWeatherProvider(),
    )

    separator_index = next(
        index
        for index, row in enumerate(outcome.display_rows)
        if row.row_type == "LEG_SEPARATOR"
    )
    parent, *children = outcome.display_rows[:separator_index]
    assert parent.row_type == "PHYSICAL_LEG_SUMMARY"
    assert (parent.from_name, parent.to_name) == ("RJFM", "OMARU")
    assert parent.section_id == controlled_section_ids[0]
    assert {row.section_id for row in children} == set(controlled_section_ids)
    assert all(row.row_type == "CALCULATION_ZONE" for row in children)
    assert any(row.to_name == "UMK/RCA" for row in children)

    expected_direct = geodesic_leg(
        working.ordered_nodes()[0].latitude_deg,
        working.ordered_nodes()[0].longitude_deg,
        plan.omaru.latitude_deg,
        plan.omaru.longitude_deg,
    )
    assert parent.tc.effective_value == pytest.approx(
        expected_direct.initial_true_course_deg
    )
    assert parent.variation.effective_value == pytest.approx(7.0)
    assert parent.mc.effective_value == pytest.approx(
        (expected_direct.initial_true_course_deg + 7.0) % 360.0
    )
    assert parent.wind.state == DisplayCellState.BLANK
    assert parent.wca.state == DisplayCellState.BLANK
    assert parent.mh.state == DisplayCellState.BLANK
    assert parent.gs.state == DisplayCellState.BLANK
    controlled_results = [
        result
        for result in outcome.sections
        if result.section_id in controlled_section_ids
    ]
    displayed_fuel_total = float(str(parent.fuel.effective_value).split("/", 1)[0])
    assert displayed_fuel_total == pytest.approx(
        sum(result.section_fuel_gal.adopted() or 0.0 for result in controlled_results),
        abs=0.051,
    )

    results_by_sequence = {result.sequence: result for result in outcome.sections}
    for child in children:
        assert child.source_result_sequence is not None
        source = results_by_sequence[child.source_result_sequence]
        assert child.section_id == source.section_id
        assert child.tc.effective_value == pytest.approx(
            source.true_course_deg.adopted()
        )


def test_omaru_first_keeps_parent_distance_and_labels_virtual_umk(
    airports,
    performance_repository,
    project,
) -> None:
    working = project.model_copy(deep=True)
    first = working.ordered_nodes()[1]
    first.latitude_deg = 32.60
    first.longitude_deg = 131.62
    references = _references(
        umk=(32.20, 131.50),
        omaru=(first.latitude_deg, first.longitude_deg),
    )
    parent_geodesic_distance = geodesic_leg(
        working.ordered_nodes()[0].latitude_deg,
        working.ordered_nodes()[0].longitude_deg,
        first.latitude_deg,
        first.longitude_deg,
    ).distance_nm
    parent_distance = parent_geodesic_distance * 1.25
    working.ordered_nodes()[0].manual_distance_nm = parent_distance
    umk_geodesic_distance = geodesic_leg(
        working.ordered_nodes()[0].latitude_deg,
        working.ordered_nodes()[0].longitude_deg,
        references.umk.latitude_deg,
        references.umk.longitude_deg,
    ).distance_nm
    expected_rca_distance = (
        parent_distance * umk_geodesic_distance / parent_geodesic_distance
    )
    plan = apply_rjfm_departure_exception(working, references)
    assert plan is not None

    outcome = _calculation_service(airports, performance_repository).calculate(
        working,
        FakeWeatherProvider(),
    )

    parent_results = [
        result
        for result in outcome.sections
        if result.section_id == working.ordered_sections()[0].id
    ]
    assert [result.phase for result in parent_results] == [
        FlightPhase.CLIMB,
        FlightPhase.CRUISE,
    ]
    assert parent_results[0].to_name == "UMK/RCA（仮定）"
    assert parent_results[1].from_name == "UMK/RCA（仮定）"
    parent_total_distance = sum(
        result.zone_distance_nm.adopted() or 0 for result in parent_results
    )
    assert parent_total_distance == pytest.approx(parent_distance, abs=1e-9)
    assert (parent_results[0].zone_distance_nm.adopted()) == pytest.approx(
        expected_rca_distance,
        abs=1e-9,
    )
    assert plan.virtual_rca_distance_nm == pytest.approx(expected_rca_distance, abs=1e-9)


def test_omaru_first_navlog_uses_rjfm_omaru_parent_and_virtual_umk_child(
    airports,
    performance_repository,
    project,
) -> None:
    working = project.model_copy(deep=True)
    first = working.ordered_nodes()[1]
    first.latitude_deg = 32.60
    first.longitude_deg = 131.62
    plan = apply_rjfm_departure_exception(
        working,
        _references(
            umk=(32.20, 131.50),
            omaru=(first.latitude_deg, first.longitude_deg),
        ),
    )
    assert plan is not None

    outcome = _calculation_service(airports, performance_repository).calculate(
        working,
        FakeWeatherProvider(),
    )

    separator_index = next(
        index
        for index, row in enumerate(outcome.display_rows)
        if row.row_type == "LEG_SEPARATOR"
    )
    parent, *children = outcome.display_rows[:separator_index]
    assert (parent.from_name, parent.to_name) == ("RJFM", "OMARU")
    assert {row.section_id for row in children} == {
        working.ordered_sections()[0].id
    }
    assert [row.to_name for row in children][:1] == ["UMK/RCA（仮定）"]


def test_direct_calculation_rejects_stale_persisted_rjfm_plan(
    airports,
    performance_repository,
    project,
) -> None:
    working = project.model_copy(deep=True)
    umk = working.ordered_nodes()[1]
    plan = apply_rjfm_departure_exception(
        working,
        _references(
            umk=(umk.latitude_deg, umk.longitude_deg),
            omaru=(32.60, 131.62),
        ),
    )
    assert plan is not None
    working.ordered_nodes()[0].manual_distance_nm = plan.virtual_rca_distance_nm * 1.25

    outcome = _calculation_service(airports, performance_repository).calculate(
        working,
        FakeWeatherProvider(),
    )

    stale = [issue for issue in outcome.issues if issue.code == "RJFM_DEPARTURE_PLAN_STALE"]
    assert len(stale) == 1
    assert stale[0].severity.value == "BLOCKER"
    assert all(
        result.performance_metadata.get("boundary_method") != "RJFM_UMK_FIXED_RCA"
        for result in outcome.sections
    )


@pytest.mark.parametrize(
    ("service_kwargs", "expected_reason"),
    [
        ({}, "CURRENT_REFERENCE_IDENTITY_UNAVAILABLE"),
        (
            {
                "expected_rjfm_reference_revision": "fixture-rjfm-v2",
                "expected_rjfm_reference_content_fingerprint": "c" * 64,
            },
            "REFERENCE_IDENTITY_MISMATCH",
        ),
    ],
)
def test_direct_calculation_rejects_unverified_or_old_rjfm_reference_identity(
    airports,
    performance_repository,
    project,
    service_kwargs: dict[str, str],
    expected_reason: str,
) -> None:
    working = project.model_copy(deep=True)
    umk = working.ordered_nodes()[1]
    assert apply_rjfm_departure_exception(
        working,
        _references(
            umk=(umk.latitude_deg, umk.longitude_deg),
            omaru=(32.60, 131.62),
        ),
    ) is not None

    outcome = CalculationService(
        airports,
        performance_repository,
        **service_kwargs,
    ).calculate(
        working,
        FakeWeatherProvider(),
    )

    stale = [issue for issue in outcome.issues if issue.code == "RJFM_DEPARTURE_PLAN_STALE"]
    assert len(stale) == 1
    assert stale[0].severity.value == "BLOCKER"
    assert stale[0].metadata["reason"] == expected_reason
    assert all(
        result.performance_metadata.get("boundary_method") != "RJFM_UMK_FIXED_RCA"
        for result in outcome.sections
    )


def test_synthetic_omaru_split_preserves_manual_route_distance(
    airports,
    performance_repository,
    project,
) -> None:
    working = project.model_copy(deep=True)
    umk = working.ordered_nodes()[1]
    umk.manual_true_course_deg = 24.0
    umk.manual_distance_nm = 18.0
    original_distance = _adopted_route_distance(working)

    plan = apply_rjfm_departure_exception(
        working,
        _references(
            umk=(umk.latitude_deg, umk.longitude_deg),
            omaru=(32.60, 131.62),
        ),
    )
    assert plan is not None
    assert _adopted_route_distance(working) == pytest.approx(original_distance, abs=1e-9)

    outcome = _calculation_service(airports, performance_repository).calculate(
        working,
        FakeWeatherProvider(),
    )
    calculated_distance = sum(
        result.zone_distance_nm.adopted() or 0.0 for result in outcome.sections
    )
    assert calculated_distance == pytest.approx(original_distance, abs=1e-9)


def test_direct_calculation_rejects_exception_section_altitude_change(
    airports,
    performance_repository,
    project,
) -> None:
    working = project.model_copy(deep=True)
    umk = working.ordered_nodes()[1]
    assert apply_rjfm_departure_exception(
        working,
        _references(
            umk=(umk.latitude_deg, umk.longitude_deg),
            omaru=(32.60, 131.62),
        ),
    ) is not None
    working.ordered_sections()[0].planned_altitude_ft_msl = 4000.0

    outcome = _calculation_service(airports, performance_repository).calculate(
        working,
        FakeWeatherProvider(),
    )

    assert any(issue.code == "RJFM_DEPARTURE_PLAN_STALE" for issue in outcome.issues)
    assert all(
        result.performance_metadata.get("boundary_method") != "RJFM_UMK_FIXED_RCA"
        for result in outcome.sections
    )


def test_direct_calculation_rejects_tampered_rjfm_rca_distance(
    airports,
    performance_repository,
    project,
) -> None:
    working = project.model_copy(deep=True)
    umk = working.ordered_nodes()[1]
    plan = apply_rjfm_departure_exception(
        working,
        _references(
            umk=(umk.latitude_deg, umk.longitude_deg),
            omaru=(32.60, 131.62),
        ),
    )
    assert plan is not None
    state = load_persisted_ui_state(working.metadata["ui_state"])
    working.metadata["ui_state"] = state.model_copy(
        update={
            "rjfm_departure_plan": plan.model_copy(
                update={"virtual_rca_distance_nm": plan.virtual_rca_distance_nm * 0.5}
            )
        }
    ).model_dump(mode="json")

    outcome = _calculation_service(airports, performance_repository).calculate(
        working,
        FakeWeatherProvider(),
    )

    assert any(issue.code == "RJFM_DEPARTURE_PLAN_STALE" for issue in outcome.issues)
    assert all(
        result.performance_metadata.get("boundary_method") != "RJFM_UMK_FIXED_RCA"
        for result in outcome.sections
    )
