from __future__ import annotations

from typing import Any

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.navlog_display import reproject_route_node_labels
from autonavlog.domain.enums import RouteNodeNameSource
from autonavlog.weather.fake_provider import FakeWeatherProvider


def _without_labels(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_labels(item)
            for key, item in value.items()
            if key not in {"from_name", "to_name"}
        }
    if isinstance(value, list):
        return [_without_labels(item) for item in value]
    return value


def test_route_node_name_defaults_to_imported() -> None:
    from autonavlog.domain.enums import RouteNodeRole
    from autonavlog.domain.project import RouteNode

    node = RouteNode(
        sequence=0,
        name="Imported point",
        latitude_deg=32,
        longitude_deg=131,
        role=RouteNodeRole.ROUTE_POINT,
    )

    assert node.name_source is RouteNodeNameSource.IMPORTED


def test_reprojected_name_changes_only_endpoint_labels_and_keeps_values(
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    outcome = CalculationService(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    renamed = project.route_nodes[1]
    renamed.name = "Training turn"
    renamed.name_source = RouteNodeNameSource.USER

    reprojected = reproject_route_node_labels(
        project,
        outcome,
        node_id=renamed.id,
        previous_name="TP1",
    )

    assert _without_labels(reprojected.model_dump(mode="json")) == _without_labels(
        outcome.model_dump(mode="json")
    )
    assert any(
        row.to_node_id == renamed.id and row.to_name == "Training turn"
        for row in reprojected.display_rows
    )
    assert all(
        row.to_name != "Training turn"
        for row in reprojected.display_rows
        if row.to_node_id is None
    )


def test_reprojection_preserves_derived_suffixes_without_parsing_user_slashes(
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    outcome = CalculationService(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    renamed = project.route_nodes[1]
    outcome = outcome.model_copy(
        update={
            "display_rows": [
                outcome.display_rows[0].model_copy(
                    update={"to_node_id": renamed.id, "to_name": "TP1 / EOC"}
                ),
                outcome.display_rows[1].model_copy(
                    update={"to_node_id": None, "to_name": "CP / EOC"}
                ),
            ]
        }
    )
    renamed.name = "Training / turn"

    reprojected = reproject_route_node_labels(
        project,
        outcome,
        node_id=renamed.id,
        previous_name="TP1",
    )

    assert reprojected.display_rows[0].to_name == "Training / turn / EOC"
    assert reprojected.display_rows[1].to_name == "CP / EOC"
