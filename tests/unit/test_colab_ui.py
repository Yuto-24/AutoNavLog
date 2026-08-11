from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.project_service import ProjectService
from autonavlog.domain.enums import AdoptedSource, ProjectStatus
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.presentation.colab import AutoNavLogApp, ViewMode
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.weather.fake_provider import FakeWeatherProvider

KML_WITH_SHAPES = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <Placemark>
    <name>TP1</name>
    <Point><coordinates>131.05,31.05,0</coordinates></Point>
  </Placemark>
  <Placemark>
    <name>ModelRoute</name>
    <LineString><coordinates>
      131.0,31.0 131.1,31.1 131.2,31.2
    </coordinates></LineString>
  </Placemark>
  <Placemark>
    <name>Airspace</name>
    <MultiGeometry>
      <Polygon>
        <altitudeMode>absolute</altitudeMode>
        <outerBoundaryIs><LinearRing><coordinates>
          131.0,31.0,1219.2 131.2,31.0,1219.2
          131.2,31.2,1219.2 131.0,31.2,1219.2
          131.0,31.0,1219.2
        </coordinates></LinearRing></outerBoundaryIs>
      </Polygon>
      <Polygon>
        <altitudeMode>absolute</altitudeMode>
        <outerBoundaryIs><LinearRing><coordinates>
          131.0,31.0,0 131.2,31.0,0 131.2,31.0,1219.2
          131.0,31.0,1219.2 131.0,31.0,0
        </coordinates></LinearRing></outerBoundaryIs>
      </Polygon>
    </MultiGeometry>
  </Placemark>
</Document>
</kml>"""

SAFE_LINE_KML = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>SafeRoute</name><LineString><coordinates>
131.449,31.877 131.58,32.60 131.737,33.479
</coordinates></LineString></Placemark>
</Document></kml>"""

POINT_ONLY_KML = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>TP-A</name><Point><coordinates>
131.58,32.60
</coordinates></Point></Placemark>
</Document></kml>"""

POLYGON_ONLY_KML = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>TrainingArea</name><Polygon>
<outerBoundaryIs><LinearRing><coordinates>
131.0,31.0 131.2,31.0 131.2,31.2 131.0,31.2 131.0,31.0
</coordinates></LinearRing></outerBoundaryIs>
</Polygon></Placemark>
</Document></kml>"""

ATTACHED_MODEL_POLYGON_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>KS4-6(SFC/4000)</name><MultiGeometry>
<Polygon><altitudeMode>absolute</altitudeMode>
<outerBoundaryIs><LinearRing><coordinates>
131.042777777778,31.8522222222222,1219.2
131.214166666667,31.7727777777778,1219.2
131.1486,31.58806,1219.2
130.975555555556,31.8405555555556,1219.2
131.042777777778,31.8522222222222,1219.2
</coordinates></LinearRing></outerBoundaryIs></Polygon>
<Polygon><altitudeMode>absolute</altitudeMode>
<outerBoundaryIs><LinearRing><coordinates>
131.042777777778,31.8522222222222,0
131.214166666667,31.7727777777778,0
131.214166666667,31.7727777777778,1219.2
131.042777777778,31.8522222222222,1219.2
131.042777777778,31.8522222222222,0
</coordinates></LinearRing></outerBoundaryIs></Polygon>
</MultiGeometry></Placemark>
</Document></kml>"""

TWO_ALTITUDE_POLYGONS_KML = """<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<Placemark><name>A</name><Polygon><altitudeMode>absolute</altitudeMode>
<outerBoundaryIs><LinearRing><coordinates>
131.0,31.0,1219.2 131.2,31.0,1219.2 131.2,31.2,1219.2
131.0,31.2,1219.2 131.0,31.0,1219.2
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>B</name><Polygon><altitudeMode>absolute</altitudeMode>
<outerBoundaryIs><LinearRing><coordinates>
132.0,32.0,1828.8 132.2,32.0,1828.8 132.2,32.2,1828.8
132.0,32.2,1828.8 132.0,32.0,1828.8
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""

TWO_LINES_KML = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>A</name><LineString><coordinates>
131.0,31.0 131.1,31.1
</coordinates></LineString></Placemark>
<Placemark><name>B</name><LineString><coordinates>
132.0,32.0 132.1,32.1
</coordinates></LineString></Placemark>
</Document></kml>"""


def _app(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    weather_provider: Any | None = None,
) -> AutoNavLogApp:
    return AutoNavLogApp(
        ProjectService(LocalProjectRepository(tmp_path)),
        CalculationService(airports, performance_repository),
        FakeWeatherProvider() if weather_provider is None else weather_provider,
        download_directory=tmp_path,
    )


def test_startup_marks_unresolved_performance_profile_as_missing(
    tmp_path: Path,
    airports: Any,
    performance_repository: PerformanceRepository,
) -> None:
    mismatched = PerformanceRepository(
        performance_repository.manifest.model_copy(
            update={"aircraft": "C172"},
        ),
        performance_repository.climb_rows,
        performance_repository.cruise_rows,
    )

    app = _app(tmp_path, airports, mismatched)

    assert "データ未整備" in app.reference_status.value
    assert "機体Profile SR22_G6" in app.reference_status.value
    assert "C172" in app.reference_status.value


def test_pasted_kml_populates_counts_warnings_shapes_and_polygon_map(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)

    app.kml_text.value = KML_WITH_SHAPES
    app.import_text_button.click()

    assert app.import_result is not None
    assert len(app.import_result.points) == 1
    assert len(app.import_result.lines) == 1
    assert len(app.import_result.polygons) == 1
    assert "1点、1 LineString、1 Polygon" in app.import_summary.value
    assert "skipped 1 Polygon surface" in app.import_summary.value
    assert "自動的に飛行経路を意味しません" in app.import_summary.value
    assert [option[1] for option in app.shape_candidates.options] == [
        ("line", 0),
        ("polygon", 0),
    ]
    assert app.shape_candidates.value == ("line", 0)
    assert app.shape_candidates.layout.display == ""
    assert "3点 / " in app.shape_candidates.options[0][0]
    assert " NM)" in app.shape_candidates.options[0][0]
    assert "Airspace" in app.map_view.value
    assert "Polygon" in app.map_view.value
    assert app.all_leg_altitude.value == 5000
    assert "KML高度から自動入力" not in app.import_summary.value


def test_attached_single_absolute_polygon_seeds_4000_ft_and_requires_confirmation(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.kml_text.value = ATTACHED_MODEL_POLYGON_KML

    app.import_text_button.click()

    assert app.import_result is not None
    assert len(app.import_result.polygons) == 1
    assert app.import_result.polygons[0].maximum_altitude_m == 1219.2
    assert app.all_leg_altitude.value == 4000
    assert app.quick_run_confirmation.value is False
    assert "KML高度から自動入力" in app.import_summary.value
    assert "absolute上限高度 1219.2 m" in app.import_summary.value
    assert "4,000 ft" in app.import_summary.value

    app.quick_calculate_button.click()

    assert app.project is None
    assert app.outcome is None
    assert "KML高度由来" in app.quick_run_status.value
    assert "新規Leg計画高度=4000 ft" in app.quick_run_status.value
    assert "確認欄を選択" in app.quick_run_status.value


def test_kml_altitude_does_not_overwrite_user_route_or_ambiguous_inputs(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    user_value_app = _app(tmp_path / "user", airports, performance_repository)
    user_value_app.all_leg_altitude.value = 5500
    user_value_app.kml_text.value = ATTACHED_MODEL_POLYGON_KML
    user_value_app.import_text_button.click()
    assert user_value_app.all_leg_altitude.value == 5500
    assert "KML高度から自動入力" not in user_value_app.import_summary.value

    route_app = _app(tmp_path / "route", airports, performance_repository)
    route_app.project = project.model_copy(deep=True)
    route_app.kml_text.value = ATTACHED_MODEL_POLYGON_KML
    route_app.import_text_button.click()
    assert route_app.all_leg_altitude.value == 5000
    assert "KML高度から自動入力" not in route_app.import_summary.value

    multiple_app = _app(tmp_path / "multiple", airports, performance_repository)
    multiple_app.kml_text.value = TWO_ALTITUDE_POLYGONS_KML
    multiple_app.import_text_button.click()
    assert multiple_app.all_leg_altitude.value == 5000
    assert "KML高度から自動入力" not in multiple_app.import_summary.value

    unknown_app = _app(tmp_path / "unknown", airports, performance_repository)
    unknown_app.kml_text.value = POLYGON_ONLY_KML
    unknown_app.import_text_button.click()
    assert unknown_app.all_leg_altitude.value == 5000
    assert "KML高度から自動入力" not in unknown_app.import_summary.value


def test_linestring_can_be_added_to_route_in_one_action(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.project = project.model_copy(
        update={"route_nodes": [], "sections": []},
        deep=True,
    )
    app.kml_text.value = KML_WITH_SHAPES
    app.import_text_button.click()

    app.shape_candidates.value = ("line", 0)
    assert app.quick_run_confirmation.disabled is False
    app.add_shape_route.click()

    assert len(app.project.route_nodes) == 3
    assert len(app.project.sections) == 2
    assert [node.name for node in app.project.route_nodes] == [
        "ModelRoute 01",
        "ModelRoute 02",
        "ModelRoute 03",
    ]
    assert {node.source for node in app.project.route_nodes} == {"KML/KMZ LineString"}


def test_polygon_requires_confirmation_before_bulk_route_conversion(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.project = project.model_copy(
        update={"route_nodes": [], "sections": []},
        deep=True,
    )
    app.kml_text.value = KML_WITH_SHAPES
    app.import_text_button.click()
    app.shape_candidates.value = ("polygon", 0)

    assert app.polygon_route_confirmation.disabled is False
    app.add_shape_route.click()
    assert app.project.route_nodes == []
    assert "確認欄を選択" in app.message.value

    app.polygon_route_confirmation.value = True
    app.add_shape_route.click()

    assert len(app.project.route_nodes) == 5
    assert len(app.project.sections) == 4
    first, last = app.project.route_nodes[0], app.project.route_nodes[-1]
    assert (first.latitude_deg, first.longitude_deg) == (
        last.latitude_deg,
        last.longitude_deg,
    )
    assert {node.source for node in app.project.route_nodes} == {"KML/KMZ Polygon (confirmed)"}


def test_existing_file_and_point_candidate_flow_still_works(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.project = project.model_copy(
        update={"route_nodes": [], "sections": []},
        deep=True,
    )

    app._import_file(
        {
            "new": (
                {
                    "name": "route.kml",
                    "content": memoryview(KML_WITH_SHAPES.encode("utf-8")),
                },
            )
        }
    )
    app.candidates.value = 0
    app.add_candidate.click()

    assert len(app.project.route_nodes) == 1
    assert app.project.route_nodes[0].name == "TP1"
    assert app.project.route_nodes[0].source == "KML/KMZ"


def test_route_rebuild_preserves_existing_leg_inputs(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.project = project.model_copy(deep=True)
    original = app.project.sections[0]
    preserved = original.model_validate(
        original.model_dump()
        | {
            "planned_altitude_ft_msl": 6500,
            "safe_enroute_altitude_ft_msl": 4200,
            "manual_wind_direction_deg": 270,
            "manual_wind_speed_kt": 18,
            "manual_temperature_c": 4,
            "manual_tas_kt": 148,
            "loss_time_seconds": 45,
        }
    )
    app.project.sections[0] = preserved

    app._rebuild_sections()

    rebuilt = app.project.sections[0]
    assert rebuilt.id == preserved.id
    assert rebuilt.planned_altitude_ft_msl == 6500
    assert rebuilt.safe_enroute_altitude_ft_msl == 4200
    assert rebuilt.manual_wind_direction_deg == 270
    assert rebuilt.manual_wind_speed_kt == 18
    assert rebuilt.manual_temperature_c == 4
    assert rebuilt.manual_tas_kt == 148
    assert rebuilt.loss_time_seconds == 45


def test_one_click_creates_project_seeds_airports_route_altitude_and_calculates(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.pilot.value = "STUDENT"
    app.ship.value = "JA00XX"
    app.name.value = "One click NAV"
    app.kml_text.value = SAFE_LINE_KML
    app.all_leg_altitude.value = 5500
    app.import_text_button.click()
    app.quick_run_confirmation.value = True

    app.quick_calculate_button.click()

    assert app.project is not None
    assert app.project.name == "One click NAV"
    assert not hasattr(app, "defaults_review_confirmation")
    assert not hasattr(app, "confirm_defaults_button")
    assert app.destination_pattern_altitude.value == 1000
    state = app.readiness_service.ui_state(app.project)
    assert state is not None
    arrival_plan = state.arrival_plan
    assert arrival_plan is not None
    assert arrival_plan.selected_pattern_altitude_ft_msl == 1000
    assert arrival_plan.selected_pattern_altitude_source == AdoptedSource.AUTOMATIC
    assert app.departure.value == "RJFM"
    assert app.destination.value == "RJFO"
    assert [node.name for node in app.project.route_nodes] == [
        "RJFM",
        "SafeRoute 02",
        "RJFO",
    ]
    assert len(app.project.sections) == 2
    assert [section.phase.value for section in app.project.sections] == [
        "CLIMB",
        "VISUAL_ARRIVAL",
    ]
    assert [section.planned_altitude_ft_msl for section in app.project.sections] == [5500, 1019]
    assert app.project.metadata["auto_phase_assignment"] == {
        "method": "IMPORTED_NAV2_POSITIONAL_V1",
        "section_count": 2,
        "phases": ["CLIMB", "VISUAL_ARRIVAL"],
        "visual_arrival_altitude_ft_msl": 1019,
        "review_required": True,
    }
    assert app.outcome is not None
    assert app.outcome.status == ProjectStatus.READY_FOR_COPY
    assert len(app.outcome.blockers) == 0
    assert "転記可（要照合）" in app.clearcopy.value
    assert "NAV LOG計算完了" in app.quick_run_status.value
    assert "Phase初期案を位置規則で設定" in app.quick_run_status.value

    assert app.outcome is not None
    assert app.outcome.status == ProjectStatus.READY_FOR_COPY
    assert not app.outcome.blockers
    assert "転記可（要照合）" in app.clearcopy.value
    assert app.clearcopy.value
    assert "AutoNavLog 転記補助表（非公式）" in app.clearcopy.value
    assert "原票へ手書きで転記するための補助表" in app.clearcopy.value
    app.download_transfer_aid_button.click()
    downloads = list(tmp_path.glob("AutoNavLog_transfer_aid_*.html"))
    assert len(downloads) == 1
    assert "A4横で印刷" in downloads[0].read_text(encoding="utf-8")
    assert "A4印刷用HTMLを保存しました" in app.download_transfer_aid_status.value

    app.destination_pattern_altitude.value = 1300
    edited_state = app.readiness_service.ui_state(app.project)
    assert edited_state is not None
    assert edited_state.arrival_plan is not None
    assert edited_state.arrival_plan.selected_pattern_altitude_ft_msl is None
    assert edited_state.arrival_plan.selected_pattern_altitude_source is None
    assert app.readiness_evaluation is not None
    assert any(
        item.issue.code == "PATTERN_ALTITUDE_REQUIRED"
        for item in app.readiness_evaluation.effective_issues
    )


def test_one_click_uses_point_as_route_candidate_between_seeded_airports(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.kml_text.value = POINT_ONLY_KML
    app.import_text_button.click()
    app.quick_run_confirmation.value = True

    app.quick_calculate_button.click()

    assert app.project is not None
    assert [node.name for node in app.project.route_nodes] == [
        "RJFM",
        "TP-A",
        "RJFO",
    ]
    assert len(app.project.sections) == 2
    assert "PointをKML記載順" in app.quick_run_status.value


def test_one_click_polygon_stops_until_order_confirmation(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.kml_text.value = POLYGON_ONLY_KML

    app.quick_calculate_button.click()

    assert app.project is None
    assert app.shape_candidates.value == ("polygon", 0)
    assert app.polygon_route_confirmation.disabled is False
    assert "開始点・進行方向" in app.quick_run_status.value
    assert "確認欄を選択" in app.quick_run_status.value

    app.quick_run_confirmation.value = True
    app.quick_calculate_button.click()

    assert app.project is not None
    assert len(app.project.route_nodes) == 7
    assert app.outcome is not None
    assert "確認済みPolygon" in app.quick_run_status.value


def test_one_click_multiple_lines_requires_human_selection(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.kml_text.value = TWO_LINES_KML

    app.quick_calculate_button.click()

    assert app.project is None
    assert "LineStringが複数" in app.quick_run_status.value
    assert "形状を1件選択" in app.quick_run_status.value
    assert app.quick_calculate_button.disabled is True
    assert "複数のLineStringから飛行経路を1件選択" in app.action_reasons.value
    app.shape_candidates.value = ("line", 0)
    assert app.quick_calculate_button.disabled is False


def test_one_click_default_etd_and_altitude_require_explicit_confirmation(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.kml_text.value = SAFE_LINE_KML

    app.quick_calculate_button.click()

    assert app.project is None
    assert app.outcome is None
    assert app.clearcopy.value == ""
    assert app.quick_run_confirmation.disabled is False
    assert "ETD JST=09:00" in app.quick_run_status.value
    assert "新規Leg計画高度=5000 ft" in app.quick_run_status.value
    assert "未確認" in app.quick_run_status.value
    assert "確認欄を選択" in app.quick_run_status.value

    app.quick_run_confirmation.value = True
    app.quick_calculate_button.click()

    assert app.project is not None
    assert app.outcome is not None
    assert "NAV LOG計算完了" in app.quick_run_status.value


def test_quick_confirmation_is_invalidated_when_operational_inputs_change(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.kml_text.value = SAFE_LINE_KML
    app.import_text_button.click()

    app.quick_run_confirmation.value = True
    app.departure_time.value = "09:15"
    assert app.quick_run_confirmation.value is False

    app.quick_run_confirmation.value = True
    app.all_leg_altitude.value = 5500
    assert app.quick_run_confirmation.value is False


def test_explicit_bulk_altitude_overwrites_only_altitude_and_reports_it(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.project = project.model_copy(deep=True)
    first = app.project.sections[0]
    app.project.sections[0] = first.model_copy(
        update={
            "manual_wind_direction_deg": 270,
            "manual_wind_speed_kt": 15,
            "safe_enroute_altitude_ft_msl": 4200,
        }
    )
    app.all_leg_altitude.value = 5800

    app.apply_all_leg_altitude_button.click()

    assert {section.planned_altitude_ft_msl for section in app.project.sections} == {5800}
    updated = app.project.sections[0]
    assert updated.manual_wind_direction_deg == 270
    assert updated.manual_wind_speed_kt == 15
    assert updated.safe_enroute_altitude_ft_msl == 4200
    assert "明示操作により既存値を含む2 Leg" in app.message.value


def test_one_click_preserves_existing_route_and_leg_values(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.project = project.model_copy(deep=True)
    original_node_ids = [node.id for node in app.project.route_nodes]
    app.project.sections[0] = app.project.sections[0].model_copy(
        update={"planned_altitude_ft_msl": 5500}
    )
    app.all_leg_altitude.value = 4000
    app.kml_text.value = SAFE_LINE_KML
    app.import_text_button.click()
    app.quick_run_confirmation.value = True

    app.quick_calculate_button.click()

    assert app.project is not None
    assert [node.id for node in app.project.route_nodes] == original_node_ids
    assert app.project.sections[0].planned_altitude_ft_msl == 5500
    assert "既存RouteとLeg入力を保護" in app.quick_run_status.value


def test_single_linestring_is_auto_selected_and_hides_redundant_picker(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.kml_text.value = SAFE_LINE_KML

    app.import_text_button.click()

    assert app.shape_candidates.value == ("line", 0)
    assert app.shape_candidates.layout.display == "none"
    assert "3点 / " in app.shape_candidates.options[0][0]
    assert " NM)" in app.shape_candidates.options[0][0]


def test_render_keeps_initial_surface_minimal_and_groups_import_tools(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)

    root = app.render()
    primary, details = root.children

    for control in (
        app.upload,
        app.kml_text,
        app.flight_date,
        app.departure_time,
        app.quick_calculate_button,
        app.project_id,
        app.load_button,
    ):
        assert control in primary.children
    for folded_control in (
        app.name,
        app.pilot,
        app.ship,
        app.import_text_button,
        app.shape_candidates,
        app.manual_lat,
        app.manual_lon,
    ):
        assert folded_control not in primary.children
    import_panel = details.children[5]
    for import_control in (
        app.import_text_button,
        app.shape_candidates,
        app.candidates,
        app.manual_lat,
        app.manual_lon,
    ):
        assert import_control in import_panel.children


def test_phase_b_auto_opens_panel_containing_pilot_ship_blockers(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
) -> None:
    app = _app(tmp_path, airports, performance_repository)
    app.kml_text.value = SAFE_LINE_KML
    app.import_text_button.click()
    app.all_leg_altitude.value = 5500
    app.quick_run_confirmation.value = True
    root = app.render()
    details = root.children[1]

    app.quick_calculate_button.click()

    assert app.project is not None
    assert app.outcome is not None
    assert app.route_state.value == "ready"
    assert "PILOT_REQUIRED" in app.status_bar.value
    assert "SHIP_REQUIRED" in app.status_bar.value
    assert details.selected_index == 0


def test_forecast_update_waits_for_explicit_switch_then_requires_recalculation(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    latest_run = "20260728060000"
    saved_run = "20260728000000"
    provider = FakeWeatherProvider(runs=(latest_run, saved_run))
    app = _app(
        tmp_path,
        airports,
        performance_repository,
        weather_provider=provider,
    )
    app.project = project.model_copy(
        deep=True,
        update={"selected_forecast_run_id": saved_run},
    )
    app._refresh_route()
    app._populate_project_inputs()

    old_outcome = app._calculate_project()

    assert app.project.selected_forecast_run_id == saved_run
    assert old_outcome.selected_forecast_run_id == saved_run
    assert provider.query_history
    assert all(run_id == saved_run for run_id, _ in provider.query_history)
    assert saved_run in app.run_label.value
    assert "2026-07-28T00:00:00Z" in app.run_label.value
    switch_buttons = [
        child
        for issue_row in app.issues.children
        for child in issue_row.children
        if getattr(child, "description", "").startswith("最新Run")
    ]
    assert len(switch_buttons) == 1

    switch_buttons[0].click()

    assert app.project.selected_forecast_run_id == latest_run
    assert app.outcome.model_dump(mode="json") == old_outcome.model_dump(mode="json")
    assert app.outcome.selected_forecast_run_id == saved_run
    assert "再計算要" in app.status_bar.value
    assert app.readiness_evaluation is not None
    assert any(
        issue.ctx.code == "RECALCULATION_REQUIRED"
        for issue in app.readiness_evaluation.effective_issues
    )
    assert "再計算してください" in app.message.value

    provider.query_history.clear()
    latest_outcome = app._calculate_project()

    assert latest_outcome.selected_forecast_run_id == latest_run
    assert all(run_id == latest_run for run_id, _ in provider.query_history)
    assert latest_run in app.run_label.value
    assert "2026-07-28T06:00:00Z" in app.run_label.value


def test_loaded_snapshot_is_fully_readonly_and_does_not_fetch_or_write(
    tmp_path: Path,
    airports: Any,
    performance_repository: Any,
    project: Any,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    projects = ProjectService(repository)
    calculation = CalculationService(airports, performance_repository)
    author_provider = FakeWeatherProvider()
    author = AutoNavLogApp(
        projects,
        calculation,
        author_provider,
        download_directory=tmp_path,
    )
    author.project = project.model_copy(deep=True)
    author._refresh_route()
    author._populate_project_inputs()
    outcome = author._calculate_project()
    saved = projects.save(author.project).project
    assert author.readiness_evaluation is not None
    snapshot_path = projects.snapshot(
        saved,
        outcome,
        calculation,
        msm_package_version=None,
        effective_issues=author.readiness_evaluation.effective_issues,
    )

    viewer_provider = FakeWeatherProvider()
    viewer = AutoNavLogApp(
        projects,
        CalculationService(airports, performance_repository),
        viewer_provider,
        download_directory=tmp_path,
    )
    viewer.project_id.value = str(saved.id)
    viewer.snapshot_id.value = snapshot_path.stem
    viewer.load_snapshot_button.click()

    assert viewer.view_mode == ViewMode.SNAPSHOT_READONLY
    assert viewer.project is not None
    assert viewer.outcome is not None
    assert viewer_provider.query_history == []
    assert "SNAPSHOT_READONLY" in viewer.status_bar.value
    assert "2026-07-28T00:00:00Z" in viewer.run_label.value
    for control in (
        viewer.pilot,
        viewer.ship,
        viewer.flight_date,
        viewer.departure_time,
        viewer.kml_text,
        viewer.route,
        viewer.phase,
        viewer.altitude,
        viewer.manual_qnh,
    ):
        assert control.disabled is True
    for action in (
        viewer.save_button,
        viewer.calculate_button,
        viewer.quick_calculate_button,
        viewer.snapshot_button,
        viewer.download_transfer_aid_button,
        viewer.edit_name_button,
    ):
        assert action.disabled is True

    before = viewer.project.model_dump(mode="json")
    selected_run = viewer.project.selected_forecast_run_id
    viewer._switch_forecast_run("20260728060000")
    viewer.save_button.click()

    assert viewer.project.model_dump(mode="json") == before
    assert viewer.project.selected_forecast_run_id == selected_run
    assert repository.load(saved.id).revision == saved.revision
    assert viewer_provider.query_history == []
    with pytest.raises(ValueError, match="Snapshot読取専用"):
        viewer._calculate_project()
    with pytest.raises(ValueError, match="Snapshot読取専用"):
        viewer.write_transfer_aid_html()
