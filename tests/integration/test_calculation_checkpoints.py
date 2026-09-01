from __future__ import annotations

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.domain.enums import VisualReferenceRole
from autonavlog.domain.project import VisualReference
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
from autonavlog.weather.fake_provider import FakeWeatherProvider


def _displayed_parent_distance_totals(outcome) -> list[float]:
    return [
        float(row.distance.text.split(" / ")[0])
        for row in outcome.display_rows
        if row.row_type == "PHYSICAL_LEG_SUMMARY" and row.distance.text is not None
    ]


def _displayed_ttl_distance(outcome) -> float:
    parent_rows = [
        row for row in outcome.display_rows if row.row_type == "PHYSICAL_LEG_SUMMARY"
    ]
    assert parent_rows
    assert parent_rows[-1].distance.text is not None
    return float(parent_rows[-1].distance.text.split(" / ")[1])


def _assert_display_distance_invariants(outcome) -> None:
    parent_rows = [
        row for row in outcome.display_rows if row.row_type == "PHYSICAL_LEG_SUMMARY"
    ]
    assert parent_rows
    displayed_parent_total = 0.0
    for parent in parent_rows:
        assert parent.distance.text is not None
        parent_total = float(parent.distance.text.split(" / ")[0])
        group = [row for row in outcome.display_rows if row.section_id == parent.section_id]
        detail_rows = [row for row in group if row.row_type == "CALCULATION_ZONE"]
        if detail_rows:
            assert sum(float(row.distance.text or "nan") for row in detail_rows) == pytest.approx(
                parent_total
            )
        displayed_parent_total += parent_total
    assert displayed_parent_total == pytest.approx(_displayed_ttl_distance(outcome))


def test_checkpoint_abeam_splits_calculation_without_changing_route_totals(
    airports,
    performance_repository,
    project,
) -> None:
    service = CalculationService(airports, performance_repository)
    baseline = service.calculate(project, FakeWeatherProvider())
    with_check_point = project.model_copy(deep=True)
    section = with_check_point.ordered_sections()[0]
    nodes = {node.id: node for node in with_check_point.route_nodes}
    start = nodes[section.from_node_id]
    end = nodes[section.to_node_id]
    leg = geodesic_leg(
        start.latitude_deg,
        start.longitude_deg,
        end.latitude_deg,
        end.longitude_deg,
    )
    on_route = point_along_leg(
        start.latitude_deg,
        start.longitude_deg,
        leg.initial_true_course_deg,
        leg.distance_nm * 0.25,
    )
    check_point = VisualReference(
        name="CP TEST",
        latitude_deg=on_route[0] + 0.03,
        longitude_deg=on_route[1],
        role=VisualReferenceRole.CHECK_POINT,
        linked_section_id=section.id,
    )
    with_check_point.visual_references.append(check_point)

    outcome = service.calculate(with_check_point, FakeWeatherProvider())

    assert not outcome.blockers
    assert len(outcome.check_point_projections) == 1
    projection = outcome.check_point_projections[0]
    assert projection.checkpoint_id == check_point.id
    assert projection.section_id == section.id
    assert projection.cross_track_distance_nm > 0
    assert any(result.to_name == "CP:CP TEST" for result in outcome.sections)
    split_at_cp = next(result for result in outcome.sections if result.to_name == "CP:CP TEST")
    assert split_at_cp.cumulative_distance_nm.adopted() == pytest.approx(
        projection.cumulative_distance_nm,
        abs=1e-9,
    )
    assert outcome.sections[-1].cumulative_distance_nm.adopted() == pytest.approx(
        baseline.sections[-1].cumulative_distance_nm.adopted(),
        abs=1e-9,
    )
    assert outcome.sections[-1].cumulative_ete_seconds.adopted() == pytest.approx(
        baseline.sections[-1].cumulative_ete_seconds.adopted(),
        abs=1e-6,
    )
    assert sum(
        section_result.section_fuel_gal.adopted() or 0.0 for section_result in outcome.sections
    ) == pytest.approx(
        sum(
            section_result.section_fuel_gal.adopted() or 0.0 for section_result in baseline.sections
        ),
        abs=1e-9,
    )
    _assert_display_distance_invariants(baseline)
    _assert_display_distance_invariants(outcome)
    assert _displayed_parent_distance_totals(outcome) == _displayed_parent_distance_totals(
        baseline
    )
    assert _displayed_ttl_distance(outcome) == pytest.approx(
        _displayed_ttl_distance(baseline)
    )
