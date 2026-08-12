from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from html import escape
from math import floor, isclose, isfinite
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import folium
import ipywidgets as widgets
from IPython.display import display

from autonavlog.application.arrival import standard_vrep_altitude_ft_msl
from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.project_service import ProjectService
from autonavlog.application.readiness import ReadinessEvaluation
from autonavlog.application.readiness_service import ReadinessService
from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.enums import (
    AdoptedSource,
    DerivedPointType,
    FlightPhase,
    IssueSeverity,
    RouteNodeRole,
    VisualReferenceRole,
)
from autonavlog.domain.planning import (
    AirportSelection,
    ArrivalAltitudeMode,
    ArrivalPlan,
    CheckPointSelection,
    PatternAltitudeValidationStatus,
    PersistedUiState,
    PointSelection,
    ReferenceDataSnapshot,
)
from autonavlog.domain.project import (
    Airport,
    NavSection,
    Project,
    RouteNode,
    VisualReference,
)
from autonavlog.importers.kml import (
    KmlDocumentSelectionRequired,
    KmlImportResult,
    import_kml_or_kmz,
    import_kml_text,
    imported_line_length_nm,
    select_imported_line,
    select_imported_polygon_outer,
)
from autonavlog.nav.geodesy import geodesic_leg
from autonavlog.nav.variation import variation_for_departure_latitude
from autonavlog.presentation.transfer_aid import (
    render_transfer_aid_document,
    render_transfer_aid_html,
)
from autonavlog.storage.reference_data import (
    ReferenceCatalog,
    ReferenceCatalogDiff,
    ReferenceDataCatalogRepository,
    ReferenceDataError,
)
from autonavlog.storage.safe_json import JsonStorageError, validate_json_bytes
from autonavlog.weather.provider import WeatherProvider


class RouteState(str, Enum):
    EMPTY = "empty"
    PARSING = "parsing"
    INVALID = "invalid"
    NEEDS_SELECTION = "needs_selection"
    READY = "ready"


class StorageState(str, Enum):
    TEMP_ONLY = "temp_only"
    DRIVE_READY = "drive_ready"
    WRITING = "writing"
    ERROR = "error"


class ViewMode(str, Enum):
    EDITABLE = "editable"
    SNAPSHOT_READONLY = "snapshot_readonly"


JST = ZoneInfo("Asia/Tokyo")


def _pattern_altitude_input_value(value: float | None) -> int:
    if value is None:
        raise ValueError("目的空港のmaster場周経路高度がありません。")
    return max(100, min(25_000, 100 * floor(float(value) / 100.0 + 0.5)))


def _pattern_altitude_source(
    selected_pattern_altitude_ft_msl: int,
    master_pattern_altitude_ft_msl: float | None,
) -> AdoptedSource:
    return (
        AdoptedSource.AUTOMATIC
        if selected_pattern_altitude_ft_msl
        == _pattern_altitude_input_value(master_pattern_altitude_ft_msl)
        else AdoptedSource.MANUAL
    )


RouteEntry = tuple[str, float, float, str]
FT_TO_M = 0.3048


DATA_ISSUE_CODES = frozenset(
    {
        "AIRPORT_DATA_UNAVAILABLE",
        "PATTERN_ALTITUDE_REQUIRED",
        "PERFORMANCE_DATA_UNAVAILABLE",
        "PERFORMANCE_DATA_UNVERIFIED",
        "CLIMB_PERFORMANCE_UNAVAILABLE",
        "CRUISE_PERFORMANCE_UNAVAILABLE",
        "REFERENCE_DATA_PACK_INVALID",
    }
)
ROUTE_ISSUE_CODES = frozenset(
    {
        "ROUTE_INCOMPLETE",
        "VISUAL_REPORTING_POINT_REQUIRED",
        "VISUAL_REPORTING_POINT_ROUTE_INVALID",
        "CP_LINK_REQUIRED",
        "CP_NOT_ABEAM_LINKED_SECTION",
    }
)
ISSUE_ACTIONS = {
    "AIRPORT_DATA_UNAVAILABLE": "参照データ欄でactive packとFROM/TOを確認してください。",
    "PATTERN_ALTITUDE_REQUIRED": "VERIFIEDの目的空港を選択するか、参照行の出典を検証してください。",
    "PERFORMANCE_DATA_UNAVAILABLE": "検証済み性能データpackを読み込んでください。",
    "PERFORMANCE_DATA_UNVERIFIED": "性能manifestと実CSV hashを検証済み版へ差し替えてください。",
    "REFERENCE_DATA_PACK_INVALID": "参照データ管理欄でpackを修復・取込・active化してください。",
    "ROUTE_INCOMPLETE": "KML/KMZを読み込み、2点以上のRouteを確定してください。",
    "RECALCULATION_REQUIRED": "「NAV LOGを作る / 再計算」を実行してください。",
    "DEFAULTS_NOT_REVIEWED": "ALT・Phase・FUEL・VAR・TGLを照合し、既定値確認を記録してください。",
    "MANUAL_QNH_RECONFIRM_REQUIRED": "DATE・ETD・FROMに対する手動QNHを再確認してください。",
    "VISUAL_REPORTING_POINT_REQUIRED": "目的空港直前のVREPを選択してください。",
    "VISUAL_REPORTING_POINT_ROUTE_INVALID": (
        "VREPの位置・高度・目的空港との順序を見直してください。"
    ),
    "ARRIVAL_ALTITUDE_OVERRIDE_REASON_REQUIRED": (
        "変則Entryの100 ft単位高度と理由を入力してください。"
    ),
    "CP_LINK_REQUIRED": "各CPを飛行経路を曲げずに関連Legへ明示的にリンクしてください。",
    "CP_NOT_ABEAM_LINKED_SECTION": "CPがLeg端点に重ならない別の関連Legを選択してください。",
    "FORECAST_PREPARE_FAILED": (
        "入力を保持したまま再計算し、通信・terrain・Forecast Runを確認してください。"
    ),
    "FORECAST_RUN_OUT_OF_COVERAGE": "互換Forecast Runへ切り替えて再計算してください。",
    "WEATHER_QUERY_FAILED": (
        "入力を保持したまま再計算し、ネットワークと気象providerを確認してください。"
    ),
    "WIND_UNAVAILABLE": "該当Legの風向・風速を両方入力するか、気象取得を再試行してください。",
    "TEMPERATURE_UNAVAILABLE": "該当Legの気温を入力するか、気象取得を再試行してください。",
    "QNH_UNAVAILABLE": "MSM推定QNHを再取得するか、手動QNHを入力・確認してください。",
    "PILOT_REQUIRED": "PILOT欄を入力してください。",
    "SHIP_REQUIRED": "SHIP欄を入力してください。",
}


class AutoNavLogApp:
    """Thin, one-column Colab UI. All business decisions stay in services."""

    def __init__(
        self,
        project_service: ProjectService,
        calculation_service: CalculationService,
        weather_provider: WeatherProvider,
        reference_data_repository: ReferenceDataCatalogRepository | None = None,
        *,
        download_directory: str | Path | None = None,
    ):
        self.project_service = project_service
        self.calculation_service = calculation_service
        self.weather_provider = weather_provider
        self.download_directory = None if download_directory is None else Path(download_directory)
        self.reference_data_repository = reference_data_repository
        self.reference_catalog: ReferenceCatalog | None = None
        self.route_state = RouteState.EMPTY
        self.storage_state = StorageState.TEMP_ONLY
        self.view_mode = ViewMode.EDITABLE
        self.readiness_service = ReadinessService(
            calculation_service,
            msm_package_version=getattr(weather_provider, "package_version", None),
            require_defaults_review=False,
        )
        repository_name = project_service.repository.__class__.__name__
        if repository_name == "GoogleDriveProjectRepository":
            self.storage_state = StorageState.DRIVE_READY
            storage_message = "<strong>保存先:</strong> Google Drive / MyDrive/AutoNavLog"
        else:
            self.storage_state = StorageState.TEMP_ONLY
            storage_message = (
                "<strong style='color:#9a6700'>保存先:</strong> 一時/local storage。"
                "Drive未接続のため、必要ならpack書出し・Project保存先を移行してください。"
            )
        self.storage_status = widgets.HTML(storage_message)
        self.action_reasons = widgets.HTML()
        self.endpoint_alignment_status = widgets.HTML()
        self.selected_reference_update_status = widgets.HTML()
        self.readiness_evaluation: ReadinessEvaluation | None = None
        self._suspend_readiness_sync = False
        self._details_accordion: widgets.Accordion | None = None
        self._pending_kmz: tuple[bytes, str] | None = None
        self._forecast_initial_time_utc: str | None = None
        self._name_editing = False
        self.status_bar = widgets.HTML()
        self.reference_status = widgets.HTML()
        self.reference_catalog_label = widgets.HTML()
        self.departure_reference = widgets.Dropdown(description="FROM master")
        self.destination_reference = widgets.Dropdown(description="TO master")
        self.refresh_reference_button = widgets.Button(
            description="active参照データを再読込",
            icon="refresh",
        )
        self.refresh_reference_button.on_click(self._refresh_reference_data)
        self.rollback_reference_button = widgets.Button(description="参照版を戻す")
        self.rollback_reference_button.on_click(self._rollback_reference_data)
        reference_controls_disabled = reference_data_repository is None
        self.reference_revision_selector = widgets.Dropdown(
            description="参照版",
            options=(),
            disabled=reference_controls_disabled,
        )
        self.activate_reference_button = widgets.Button(
            description="選択版をactive化",
            disabled=reference_controls_disabled,
        )
        self.activate_reference_button.on_click(self._activate_reference_revision)
        self.reference_pack_path = widgets.Text(
            description="pack path",
            placeholder="取込元または書出先ディレクトリ",
            disabled=reference_controls_disabled,
        )
        self.import_reference_button = widgets.Button(
            description="pack取込・active化",
            disabled=reference_controls_disabled,
        )
        self.import_reference_button.on_click(self._import_reference_pack)
        self.export_reference_button = widgets.Button(
            description="active packを書出",
            disabled=reference_controls_disabled,
        )
        self.export_reference_button.on_click(self._export_reference_pack)
        self.reference_new_revision = widgets.Text(
            description="新revision",
            value=datetime.now(JST).strftime("user-%Y%m%d-%H%M%S"),
            disabled=reference_controls_disabled,
        )
        self.reference_kind = widgets.Dropdown(
            description="行kind",
            options=(
                ("空港", "AIRPORT"),
                ("地点", "POINT"),
                ("CP", "CHECK_POINT"),
            ),
            disabled=reference_controls_disabled,
        )
        self.reference_row_id = widgets.Text(
            description="行ID",
            disabled=reference_controls_disabled,
        )
        self.reference_search = widgets.Text(
            description="参照検索",
            placeholder="ID・ICAO・名称・出典を検索",
            disabled=reference_controls_disabled,
        )
        self.reference_search_results = widgets.Select(
            description="検索結果",
            options=(),
            rows=6,
            disabled=reference_controls_disabled,
        )
        self.reference_search.observe(
            self._refresh_reference_search,
            names="value",
        )
        self.reference_search_results.observe(
            self._reference_search_selected,
            names="value",
        )
        self.reference_row_json = widgets.Textarea(
            description="行JSON",
            placeholder="既存行を読込、または新規canonical row JSONを入力",
            layout=widgets.Layout(width="100%", height="180px"),
            disabled=reference_controls_disabled,
        )
        self.load_reference_row_button = widgets.Button(
            description="行を読込",
            disabled=reference_controls_disabled,
        )
        self.master_reference_kind = widgets.Dropdown(
            description="master種別",
            options=(("地点", "POINT"), ("CP", "CHECK_POINT")),
            disabled=reference_controls_disabled,
        )
        self.master_reference_row = widgets.Dropdown(
            description="master行",
            options=(),
            disabled=reference_controls_disabled,
        )
        self.add_master_reference_button = widgets.Button(
            description="選択masterをProjectへ追加",
            icon="plus",
            disabled=reference_controls_disabled,
        )
        self.master_reference_kind.observe(
            self._refresh_master_reference_options,
            names="value",
        )
        self.add_master_reference_button.on_click(
            self._add_master_reference,
        )
        self.selected_reference_updates = widgets.SelectMultiple(
            description="更新候補",
            options=(),
            rows=6,
            disabled=reference_controls_disabled,
        )
        self.apply_selected_reference_updates_button = widgets.Button(
            description="選択した最新masterを反映",
            icon="refresh",
            disabled=reference_controls_disabled,
        )
        self.apply_selected_reference_updates_button.on_click(
            self._apply_selected_reference_updates,
        )
        self.load_reference_row_button.on_click(self._load_reference_row)
        self.save_reference_row_button = widgets.Button(
            description="新revisionへ追加・編集",
            disabled=reference_controls_disabled,
        )
        self.save_reference_row_button.on_click(self._save_reference_row)
        self.delete_reference_row_button = widgets.Button(
            description="新revisionで削除",
            disabled=reference_controls_disabled,
        )
        self.delete_reference_row_button.on_click(self._delete_reference_row)
        self.reference_diff = widgets.HTML()
        self.project: Project | None = None
        self.outcome: CalculationOutcome | None = None
        self.import_result: KmlImportResult | None = None
        self._imported_kml_text: str | None = None
        self._kml_altitude_note: str | None = None
        self._setting_kml_altitude = False
        self._all_leg_altitude_user_edited = False
        self.message = widgets.HTML()
        self.name = widgets.Text(description="保存名", disabled=True)
        self.edit_name_button = widgets.Button(
            description="保存名を変更",
            icon="pencil",
            disabled=True,
        )
        self.edit_name_button.on_click(self._enable_name_editing)
        self.pilot = widgets.Text(description="PILOT")
        self.ship = widgets.Text(description="SHIP")
        self.flight_date = widgets.DatePicker(
            description="DATE",
            value=datetime.now(JST).date() + timedelta(days=1),
        )
        self.departure_time = widgets.Text(description="ETD JST", value="09:00")
        self.departure = widgets.Text(description="FROM")
        self.destination = widgets.Text(description="TO")
        self.fuel = widgets.FloatText(description="FUEL gal", value=81.0)
        # Kept in the Project payload for schema compatibility; calculations use
        # the per-leg departure-latitude rule below.
        self.variation = widgets.FloatText(description="旧VAR", value=8.0, disabled=True)
        self.variation_rule_summary = widgets.HTML(
            "<strong>VAR E:</strong> Leg出発緯度32.0°N以上は+8°、未満は+7°を自動採用"
        )
        self.leg_variation = widgets.HTML(
            "<strong>選択Leg VAR E:</strong> Routeを確定してください。"
        )
        self.create_button = widgets.Button(description="新規Project", icon="plus")
        self.create_button.on_click(self._create_project)
        self.kmz_document_candidates = widgets.Dropdown(
            description="KMZ内KML",
            options=(),
        )
        self.align_departure_endpoint_button = widgets.Button(
            description="FROM端点を公示座標へ合わせる",
            icon="crosshairs",
            disabled=reference_controls_disabled,
        )
        self.align_destination_endpoint_button = widgets.Button(
            description="TO端点を公示座標へ合わせる",
            icon="crosshairs",
            disabled=reference_controls_disabled,
        )
        self.align_departure_endpoint_button.on_click(
            lambda _: self._align_endpoint_to_airport(departure=True)
        )
        self.align_destination_endpoint_button.on_click(
            lambda _: self._align_endpoint_to_airport(departure=False)
        )
        self.select_kmz_document_button = widgets.Button(
            description="選択KMLを読み込む",
            disabled=True,
        )
        self.select_kmz_document_button.on_click(self._select_kmz_document)
        self.project_id = widgets.Dropdown(
            description="保存Project",
            options=(("未選択", None),),
            value=None,
        )
        self.load_button = widgets.Button(description="読込", icon="folder-open")
        self.load_button.on_click(self._load_project)
        self.upload = widgets.FileUpload(accept=".kml,.kmz", multiple=False)
        self.upload.observe(self._import_file, names="value")
        self.kml_text = widgets.Textarea(
            description="KML/XML",
            placeholder="Google EarthからコピーしたKML/XML全文を貼り付けてください",
            layout=widgets.Layout(width="100%", height="180px"),
        )
        self.import_text_button = widgets.Button(
            description="貼付KMLを読み込む",
            icon="paste",
        )
        self.import_text_button.on_click(self._import_text)
        self.import_summary = widgets.HTML()
        self.candidates = widgets.Select(description="候補", rows=6)
        self.map_view = widgets.HTML()
        self.add_candidate = widgets.Button(description="Routeへ追加", icon="plus")
        point_role_options: list[tuple[str, str | None]] = [
            ("経路点", RouteNodeRole.ROUTE_POINT.value),
            ("VREP", RouteNodeRole.VISUAL_REPORTING_POINT.value),
            ("CP（経路外）", "CHECK_POINT"),
            ("参照のみ", "REFERENCE_ONLY"),
        ]
        if reference_data_repository is not None:
            point_role_options.insert(0, ("役割を選択", None))
        self.point_role = widgets.Dropdown(
            description="Point役割",
            options=point_role_options,
            value=(
                None if reference_data_repository is not None else RouteNodeRole.ROUTE_POINT.value
            ),
        )
        self.point_linked_section = widgets.Dropdown(
            description="CP関連Leg",
            options=(),
        )
        self.add_candidate.on_click(self._add_imported_point)
        self.shape_candidates = widgets.Select(description="形状", rows=6)
        self.shape_candidates.observe(self._shape_selected, names="value")
        self.quick_run_confirmation = widgets.Checkbox(
            description=(
                "ETD・全Leg高度を確認した。Polygonの場合は、境界・開始点・"
                "進行方向もKML記載順でRouteに使うことを確認した"
            ),
            value=False,
            disabled=True,
            indent=False,
            layout=widgets.Layout(width="100%"),
        )
        # Compatibility name for callers that used the original Polygon-only control.
        self.polygon_route_confirmation = self.quick_run_confirmation
        self.add_shape_route = widgets.Button(
            description="選択形状をRouteへ一括追加",
            icon="plus",
            disabled=True,
        )
        self.add_shape_route.on_click(self._add_imported_shape)
        self.manual_name = widgets.Text(description="名称")
        self.manual_lat = widgets.FloatText(description="緯度")
        self.manual_lon = widgets.FloatText(description="経度")
        self.add_manual = widgets.Button(description="座標を追加", icon="plus")
        self.add_manual.on_click(self._add_manual_point)
        self.route = widgets.Select(description="Route", rows=8)
        self.route.observe(self._route_selected, names="value")
        self.delete_node = widgets.Button(icon="trash", tooltip="選択点を削除")
        self.move_up = widgets.Button(icon="arrow-up", tooltip="上へ移動")
        self.move_down = widgets.Button(icon="arrow-down", tooltip="下へ移動")
        self.delete_node.on_click(self._delete_node)
        self.move_up.on_click(lambda _: self._move_node(-1))
        self.move_down.on_click(lambda _: self._move_node(1))
        self.phase = widgets.Dropdown(
            description="Phase",
            options=[item.value for item in FlightPhase],
            value=FlightPhase.CRUISE.value,
        )
        self.altitude = widgets.FloatText(description="ALT ft", value=5000)
        self.wind_direction = widgets.Text(description="WIND°")
        self.wind_speed = widgets.Text(description="WIND kt")
        self.vrep_selector = widgets.Dropdown(description="VREP")
        self.arrival_mode = widgets.Dropdown(
            description="到着高度",
            options=[
                ("標準距離則", ArrivalAltitudeMode.STANDARD_DISTANCE_RULE.value),
                ("変則Entry", ArrivalAltitudeMode.MANUAL_NON_STANDARD_ENTRY.value),
            ],
            value=ArrivalAltitudeMode.STANDARD_DISTANCE_RULE.value,
        )
        self.manual_vrep_altitude = widgets.Text(description="手動VREP ft")
        self.manual_vrep_reason = widgets.Text(description="変則理由")
        self.destination_pattern_altitude = widgets.BoundedIntText(
            description="場周高度 MSL",
            min=100,
            max=25_000,
            step=100,
            value=1000,
            disabled=True,
        )
        self.destination_pattern_altitude_help = widgets.HTML(
            "<small>master値を初期表示します。東西場周など運用差がある場合は、"
            "今回使う100 ft単位のMSL高度へ編集してください。</small>"
        )
        self.apply_arrival_plan_button = widgets.Button(
            description="目的空港・場周高度・VREPを確定",
            icon="check",
        )
        self.destination_pattern_altitude.observe(
            self._destination_pattern_altitude_changed,
            names="value",
        )
        self.apply_arrival_plan_button.on_click(self._apply_arrival_plan)
        self.manual_qnh_confirmation = widgets.Checkbox(
            description="DATE・ETD・出発地に対する手動QNHを確認した",
            indent=False,
        )
        self.confirm_manual_qnh_button = widgets.Button(
            description="手動QNH確認を記録",
            icon="check",
        )
        self.confirm_manual_qnh_button.on_click(self._confirm_manual_qnh)
        self.temperature = widgets.Text(description="TEMP °C")
        self.tas = widgets.Text(description="TAS kt")
        self.all_leg_altitude = widgets.FloatText(
            description="全Leg ALT ft",
            value=5000,
        )
        for control in (
            self.flight_date,
            self.departure_time,
            self.kml_text,
        ):
            control.observe(
                self._invalidate_quick_run_confirmation,
                names="value",
            )
        self.all_leg_altitude.observe(
            self._all_leg_altitude_changed,
            names="value",
        )
        self.apply_all_leg_altitude_button = widgets.Button(
            description="全Legへ高度を明示適用",
            icon="check",
        )
        self.apply_all_leg_altitude_button.on_click(self._apply_all_leg_altitude)
        self.manual_qnh = widgets.Text(description="QNH hPa")
        self.tgl_count = widgets.BoundedIntText(description="TGL", min=0, value=0)
        self.apply_phase = widgets.Button(description="Legへ適用", icon="check")
        self.apply_phase.on_click(self._apply_phase)
        self.run_label = widgets.HTML("<strong>Forecast Run:</strong> 未選択")
        self.calculate_button = widgets.Button(
            description="計算実行",
            icon="calculator",
            button_style="primary",
        )
        self.calculate_button.on_click(self._calculate)
        self.quick_calculate_button = widgets.Button(
            description="貼付KMLからNAV LOG計算",
            icon="calculator",
            button_style="success",
            layout=widgets.Layout(width="100%", height="48px"),
        )
        self.quick_calculate_button.on_click(self._calculate_from_pasted_kml)
        self.quick_run_status = widgets.HTML()
        self.issues = widgets.VBox()
        self.clearcopy = widgets.HTML()
        self.download_transfer_aid_button = widgets.Button(
            description="A4印刷用HTMLをダウンロード",
            icon="download",
        )
        self.download_transfer_aid_button.on_click(self._download_transfer_aid)
        self.download_transfer_aid_status = widgets.HTML()
        self.save_button = widgets.Button(description="保存", icon="save")
        self.snapshot_button = widgets.Button(description="Snapshot", icon="camera")
        self.save_button.on_click(self._save)
        self.departure_reference.observe(
            lambda change: self._airport_reference_changed(
                change,
                departure=True,
            ),
            names="value",
        )
        self.destination_reference.observe(
            lambda change: self._airport_reference_changed(change, departure=False),
            names="value",
        )
        self.snapshot_button.on_click(self._snapshot)
        self.snapshot_id = widgets.Text(description="Snapshot ID")
        self.load_snapshot_button = widgets.Button(
            description="Snapshotを読取専用で開く",
            icon="eye",
        )
        self.load_snapshot_button.on_click(self._load_snapshot)
        for control in (
            self.flight_date,
            self.pilot,
            self.ship,
            self.departure_time,
            self.fuel,
            self.variation,
            self.manual_qnh,
            self.tgl_count,
        ):
            control.observe(
                self._readiness_input_changed,
                names="value",
            )
        self._refresh_reference_catalog()
        self._refresh_project_list()
        self._refresh_readiness()

    @staticmethod
    def _airport_from_selection(selection: AirportSelection) -> Airport:
        return Airport(
            id=selection.id,
            icao=selection.icao,
            name=selection.name,
            latitude_deg=selection.latitude_deg,
            longitude_deg=selection.longitude_deg,
            elevation_ft_msl=selection.elevation_ft_msl,
            pattern_altitude_ft_msl=selection.pattern_altitude_ft_msl,
            source=selection.source,
            source_revision=selection.source_revision,
        )

    @staticmethod
    def _legacy_airport_selection(airport: Airport) -> AirportSelection:
        verified_fixture = airport.source_revision.lower().startswith("fixture")
        raw_pattern_altitude = (
            airport.pattern_altitude_ft_msl
            if airport.pattern_altitude_ft_msl is not None
            else airport.elevation_ft_msl + 1000.0
        )
        pattern_altitude = float(_pattern_altitude_input_value(raw_pattern_altitude))
        return AirportSelection(
            id=airport.id,
            icao=airport.icao,
            name=airport.name,
            latitude_deg=airport.latitude_deg,
            longitude_deg=airport.longitude_deg,
            elevation_ft_msl=airport.elevation_ft_msl,
            pattern_altitude_ft_msl=pattern_altitude,
            pattern_altitude_source=airport.source,
            pattern_altitude_source_revision=airport.source_revision,
            pattern_altitude_validation_status=(
                PatternAltitudeValidationStatus.VERIFIED
                if verified_fixture
                else PatternAltitudeValidationStatus.UNVERIFIED
            ),
            source=airport.source,
            source_revision=airport.source_revision,
        )

    def _enable_name_editing(self, _: Any) -> None:
        if self.project is None or self.outcome is None:
            self._notify(
                "計算完了後に提示された保存名を変更できます。",
                error=True,
            )
            return
        if self.view_mode != ViewMode.EDITABLE:
            self._notify("Snapshotの保存名は変更できません。", error=True)
            return
        self._name_editing = True
        self.name.disabled = False
        self.edit_name_button.disabled = True
        self.name.focus()

    def _set_storage_state(
        self,
        state: StorageState,
        detail: str | None = None,
    ) -> None:
        self.storage_state = state
        if state == StorageState.WRITING:
            message = "<strong>保存先:</strong> 書込み中…"
        elif state == StorageState.ERROR:
            message = "<strong style='color:#922b21'>保存エラー:</strong> " + escape(
                detail or "保存先を確認し、もう一度実行してください。"
            )
        elif self.project_service.repository.__class__.__name__ == "GoogleDriveProjectRepository":
            self.storage_state = StorageState.DRIVE_READY
            message = "<strong>保存先:</strong> Google Drive / MyDrive/AutoNavLog"
        else:
            self.storage_state = StorageState.TEMP_ONLY
            message = (
                "<strong style='color:#9a6700'>保存先:</strong> 一時/local storage。"
                "Drive未接続のため、必要ならpack書出し・Project保存先を移行してください。"
            )
        self.storage_status.value = message

    def _refresh_data_status(self) -> None:
        performance = self.calculation_service.performance
        performance_status = performance.manifest.validation_status
        missing: list[str] = []
        notices: list[str] = []
        missing.extend(performance.readiness_problems("SR22_G6"))
        if performance.load_failure_reason is None and (
            performance_status in {"REJECTED", ""}
            or performance_status
            not in {
                "VERIFIED",
                "UNVERIFIED",
                "PENDING",
                "REJECTED",
            }
        ):
            missing.append(f"性能データ状態を解釈できません ({performance_status or '空'})")
        elif performance.load_failure_reason is None and performance_status != "VERIFIED":
            notices.append(
                f"性能 {performance.manifest.source_revision} / {performance_status}"
                "（計算確認は可能、転記補助HTMLは不可）"
            )
        elif performance.load_failure_reason is None:
            notices.append(f"性能 {performance.manifest.source_revision} / VERIFIED")

        if self.reference_data_repository is None:
            notices.append("参照データ legacy adapter")
        elif self.reference_catalog is None:
            missing.append("active参照データpackを検証できません")
        else:
            verified_destinations = sum(
                row.pattern_altitude_validation_status == PatternAltitudeValidationStatus.VERIFIED
                for row in self.reference_catalog.airports.values()
            )
            if verified_destinations == 0:
                missing.append("目的空港として使えるVERIFIED場周経路高度がありません")
            notices.append(
                "参照 "
                f"{self.reference_catalog.manifest.dataset_id} / "
                f"{self.reference_catalog.manifest.revision}"
            )

        if missing:
            items = "".join(f"<li>{escape(item)}</li>" for item in missing)
            self.reference_status.value = (
                "<div style='color:#922b21'><strong>⚠ データ未整備:</strong> "
                "経路取込・入力確認・下書き保存はできますが、"
                "不足データを使う計算と転記補助HTMLは完了できません。"
                f"<ul>{items}</ul></div>"
            )
        else:
            self.reference_status.value = "<strong>データ版:</strong> " + escape(
                " / ".join(notices)
            )

    def _refresh_reference_search(
        self,
        _: dict[str, Any] | None = None,
    ) -> None:
        self._reference_search_rows: dict[str, tuple[str, str]] = {}
        if self.reference_catalog is None:
            self.reference_search_results.options = ()
            return
        query = self.reference_search.value.strip().casefold()
        options: list[tuple[str, str]] = []
        collections = (
            ("AIRPORT", self.reference_catalog.airports),
            ("POINT", self.reference_catalog.points),
            ("CHECK_POINT", self.reference_catalog.check_points),
        )
        for kind, rows in collections:
            for row in rows.values():
                document = " ".join(
                    str(value)
                    for value in row.model_dump(mode="json").values()
                    if value is not None
                )
                if query and query not in document.casefold():
                    continue
                token = f"{kind}:{row.id}"
                self._reference_search_rows[token] = (kind, row.id)
                icao = getattr(row, "icao", "")
                label_id = f"{row.id} {icao}".strip()
                options.append(
                    (
                        f"{kind} / {label_id} / {row.name} / {row.source_revision}",
                        token,
                    )
                )
        self.reference_search_results.options = options

    def _reference_search_selected(self, change: dict[str, Any]) -> None:
        token = change.get("new")
        if token is None:
            return
        selected = self._reference_search_rows.get(str(token))
        if selected is None:
            return
        kind, row_id = selected
        self.reference_kind.value = kind
        self.reference_row_id.value = row_id
        self._load_reference_row(None)

    def _airport_reference_changed(
        self,
        change: dict[str, Any],
        *,
        departure: bool,
    ) -> None:
        if (
            self._suspend_readiness_sync
            or self.project is None
            or change.get("new") is None
            or change.get("old") == change.get("new")
        ):
            return
        try:
            selected_id = str(change["new"])
            departure_id = selected_id if departure else self.project.departure_airport_id
            destination_id = self.project.destination_airport_id if departure else selected_id
            selected_departure, selected_destination = self._selected_airport_rows(
                departure_id,
                destination_id,
            )
            self.project.departure_airport_id = selected_departure.id
            self.project.destination_airport_id = selected_destination.id
            self.departure.value = selected_departure.id
            self.destination.value = selected_destination.id
            state = self.readiness_service.ui_state(self.project) or PersistedUiState()
            snapshot = state.reference_data_snapshot
            if snapshot is None:
                self._store_reference_snapshot(
                    selected_departure.id,
                    selected_destination.id,
                )
                state = self.readiness_service.ui_state(self.project) or PersistedUiState()
            else:
                updated = snapshot.model_copy(
                    deep=True,
                    update={
                        "departure_airport": selected_departure,
                        "destination_airport": selected_destination,
                    },
                )
                state = state.model_copy(update={"reference_data_snapshot": updated})
            if not departure:
                arrival_plan = state.arrival_plan
                if arrival_plan is not None:
                    arrival_plan = arrival_plan.model_copy(
                        update={
                            "selected_pattern_altitude_ft_msl": None,
                            "selected_pattern_altitude_source": None,
                        }
                    )
                state = state.model_copy(update={"arrival_plan": arrival_plan})
                self._set_destination_pattern_altitude(selected_destination.pattern_altitude_ft_msl)
            self.project_service.set_ui_state(
                self.project,
                state,
                reconfirmed=True,
            )
            self._refresh_selected_reference_updates()
            self._refresh_readiness()
            label = "FROM" if departure else "TO"
            self._notify(
                f"{label}の参照空港を{selected_id}へ変更しました。"
                "経路端点座標は移動していません。再計算してください。"
            )
        except (KeyError, TypeError, ValueError) as error:
            self._notify(str(error), error=True)

    def _set_destination_pattern_altitude(self, value: float | None) -> None:
        previous_suspend = self._suspend_readiness_sync
        self._suspend_readiness_sync = True
        try:
            self.destination_pattern_altitude.value = _pattern_altitude_input_value(value)
        finally:
            self._suspend_readiness_sync = previous_suspend

    def _destination_pattern_altitude_changed(self, change: dict[str, Any]) -> None:
        if (
            self._suspend_readiness_sync
            or self.project is None
            or change.get("old") == change.get("new")
        ):
            return
        state = self.readiness_service.ui_state(self.project) or PersistedUiState()
        arrival_plan = state.arrival_plan
        if arrival_plan is not None:
            cleared_plan = arrival_plan.model_copy(
                update={
                    "selected_pattern_altitude_ft_msl": None,
                    "selected_pattern_altitude_source": None,
                }
            )
            self.project_service.set_ui_state(
                self.project,
                state.model_copy(update={"arrival_plan": cleared_plan}),
                reconfirmed=True,
            )
        self._refresh_readiness()

    def _refresh_reference_catalog(self) -> None:
        previous_departure = self.departure_reference.value
        previous_destination = self.destination_reference.value
        if self.reference_data_repository is None:
            selections = {
                airport.id: self._legacy_airport_selection(airport)
                for airport in self.calculation_service.airports.all()
            }
            self.reference_catalog = None
            self.reference_catalog_label.value = (
                "<strong>参照データ:</strong> legacy adapter。配布時はmanifest付き"
                "active packを指定してください。"
            )
        else:
            try:
                catalog = self.reference_data_repository.open_active()
            except ReferenceDataError as error:
                self.master_reference_row.options = ()
                self.reference_search_results.options = ()
                self.selected_reference_updates.options = ()
                self.reference_catalog = None
                self.departure_reference.options = ()
                self.destination_reference.options = ()
                self._refresh_data_status()
                self.reference_status.value += (
                    "<br><strong>REFERENCE_DATA_PACK_INVALID:</strong> " + escape(str(error))
                )
                return
            self.reference_catalog = catalog
            selections = catalog.airports
            self.reference_catalog_label.value = (
                "<strong>active参照データ:</strong> "
                f"{escape(catalog.manifest.dataset_id)} / "
                f"{escape(catalog.manifest.revision)}"
            )
        ordered = sorted(
            selections.values(),
            key=lambda item: (item.icao, item.id),
        )
        departure_options: list[tuple[str, str | None]] = [
            (f"{item.icao} {item.name}", item.id) for item in ordered
        ]
        destination_options: list[tuple[str, str | None]] = [
            (f"{item.icao} {item.name}", item.id)
            for item in ordered
            if item.pattern_altitude_validation_status == PatternAltitudeValidationStatus.VERIFIED
        ]
        if self.reference_data_repository is not None:
            departure_options.insert(0, ("選択してください", None))
            destination_options.insert(0, ("選択してください", None))
        previous_suspend = self._suspend_readiness_sync
        self._suspend_readiness_sync = True
        try:
            self.departure_reference.options = departure_options
            self.destination_reference.options = destination_options
        finally:
            self._suspend_readiness_sync = previous_suspend
        departure_ids = {value for _, value in departure_options if value is not None}
        destination_ids = {value for _, value in destination_options if value is not None}
        previous_suspend = self._suspend_readiness_sync
        self._suspend_readiness_sync = True
        try:
            if previous_departure in departure_ids:
                self.departure_reference.value = previous_departure
            if previous_destination in destination_ids:
                self.destination_reference.value = previous_destination
        finally:
            self._suspend_readiness_sync = previous_suspend
        if self.reference_data_repository is not None:
            revisions = list(self.reference_data_repository.list_revisions())
            if self.reference_catalog is None:
                raise ReferenceDataError("active reference catalog is unavailable")
            active_identity = (
                self.reference_catalog.manifest.dataset_id,
                self.reference_catalog.manifest.revision,
            )
            if active_identity not in revisions:
                revisions.insert(0, active_identity)
            self.reference_revision_selector.options = [
                (f"{dataset_id} / {revision}", (dataset_id, revision))
                for dataset_id, revision in revisions
            ]
            self.reference_revision_selector.value = active_identity
        self._refresh_master_reference_options()
        self._refresh_selected_reference_updates()
        self._refresh_data_status()

    def _refresh_master_reference_options(
        self,
        _: dict[str, Any] | None = None,
    ) -> None:
        if self.reference_catalog is None:
            self.master_reference_row.options = ()
            return
        rows: dict[str, PointSelection | CheckPointSelection]
        self._refresh_reference_search()
        if self.master_reference_kind.value == "CHECK_POINT":
            rows = dict(self.reference_catalog.check_points)
        else:
            rows = dict(self.reference_catalog.points)
        self.master_reference_row.options = [
            (
                f"{row.id} {row.name} / {row.source_revision}",
                row.id,
            )
            for row in sorted(rows.values(), key=lambda item: (item.name, item.id))
        ]

    def _add_master_reference(self, _: Any) -> None:
        if (
            self.project is None
            or self.reference_catalog is None
            or self.master_reference_row.value is None
        ):
            self._notify(
                "Projectとactive master行を選択してください。",
                error=True,
            )
            return
        state = self.readiness_service.ui_state(self.project)
        if state is None or state.reference_data_snapshot is None:
            self._notify(
                "FROM/TOを確定して参照snapshotを作成してください。",
                error=True,
            )
            return
        snapshot = state.reference_data_snapshot
        route_points = dict(snapshot.route_points)
        check_points = dict(snapshot.check_points)
        try:
            row_id = str(self.master_reference_row.value)
            if self.master_reference_kind.value == "POINT":
                point_row = self.reference_catalog.points[row_id]
                node = RouteNode(
                    project_id=self.project.id,
                    sequence=len(self.project.route_nodes),
                    name=point_row.name,
                    latitude_deg=point_row.latitude_deg,
                    longitude_deg=point_row.longitude_deg,
                    role=point_row.point_role,
                    source=(f"REFERENCE {point_row.source} / {point_row.source_revision}"),
                )
                self.project.route_nodes.append(node)
                route_points[node.id] = point_row
                self._rebuild_sections()
            else:
                check_point_row = self.reference_catalog.check_points[row_id]
                if self.point_linked_section.value is None:
                    raise ValueError("CPを関連付けるLegを選択してください。")
                linked_section_id = UUID(str(self.point_linked_section.value))
                if linked_section_id not in {section.id for section in self.project.sections}:
                    raise ValueError("選択したCP関連Legが現在のRouteにありません。")
                reference = VisualReference(
                    project_id=self.project.id,
                    name=check_point_row.name,
                    latitude_deg=check_point_row.latitude_deg,
                    longitude_deg=check_point_row.longitude_deg,
                    role=VisualReferenceRole.CHECK_POINT,
                    linked_section_id=linked_section_id,
                    source=(
                        f"REFERENCE {check_point_row.source} / {check_point_row.source_revision}"
                    ),
                )
                self.project.visual_references.append(reference)
                check_points[reference.id] = check_point_row
            updated_snapshot = snapshot.model_copy(
                deep=True,
                update={
                    "route_points": route_points,
                    "check_points": check_points,
                },
            )
            self.project_service.set_ui_state(
                self.project,
                state.model_copy(update={"reference_data_snapshot": updated_snapshot}),
                reconfirmed=True,
            )
            self._refresh_route()
            self._refresh_selected_reference_updates()
            self._refresh_readiness()
            self._notify(f"active master行 {row_id} をProjectへ追加しました。")
        except (KeyError, TypeError, ValueError) as error:
            self._notify(str(error), error=True)

    def _refresh_selected_reference_updates(self) -> None:
        self._selected_reference_update_rows: dict[
            str,
            tuple[
                str,
                UUID | None,
                AirportSelection | PointSelection | CheckPointSelection,
            ],
        ] = {}
        if self.project is None or self.reference_catalog is None:
            self.selected_reference_updates.options = ()
            self.selected_reference_update_status.value = ""
            return
        state = self.readiness_service.ui_state(self.project)
        if state is None or state.reference_data_snapshot is None:
            self.selected_reference_updates.options = ()
            self.selected_reference_update_status.value = "Projectの参照snapshotが未確定です。"
            return
        snapshot = state.reference_data_snapshot
        options: list[tuple[str, str]] = []
        missing: list[str] = []

        def register(
            token: str,
            label: str,
            kind: str,
            key: UUID | None,
            saved: AirportSelection | PointSelection | CheckPointSelection,
            current: AirportSelection | PointSelection | CheckPointSelection | None,
        ) -> None:
            if current is None:
                missing.append(f"{label}（active版から削除済み）")
                return
            if saved.canonical_row() == current.canonical_row():
                return
            self._selected_reference_update_rows[token] = (
                kind,
                key,
                current,
            )
            options.append(
                (
                    f"{label}: {saved.source_revision} → {current.source_revision}",
                    token,
                )
            )

        register(
            "departure",
            f"FROM {snapshot.departure_airport.id}",
            "AIRPORT",
            None,
            snapshot.departure_airport,
            self.reference_catalog.airports.get(snapshot.departure_airport.id),
        )
        register(
            "destination",
            f"TO {snapshot.destination_airport.id}",
            "AIRPORT",
            None,
            snapshot.destination_airport,
            self.reference_catalog.airports.get(snapshot.destination_airport.id),
        )
        for node_id, saved_point in snapshot.route_points.items():
            register(
                f"route:{node_id}",
                f"Route {saved_point.id}",
                "POINT",
                node_id,
                saved_point,
                self.reference_catalog.points.get(saved_point.id),
            )
        for reference_id, saved_check_point in snapshot.check_points.items():
            register(
                f"cp:{reference_id}",
                f"CP {saved_check_point.id}",
                "CHECK_POINT",
                reference_id,
                saved_check_point,
                self.reference_catalog.check_points.get(saved_check_point.id),
            )
        self.selected_reference_updates.options = options
        messages = []
        if options:
            messages.append(
                f"active版との更新差分 {len(options)}件。反映する行だけ選択してください。"
            )
        else:
            messages.append("選択済み参照行にactive版との差分はありません。")
        if missing:
            messages.append(" / ".join(missing))
        self.selected_reference_update_status.value = "<br>".join(
            escape(message) for message in messages
        )

    def _apply_selected_reference_updates(self, _: Any) -> None:
        if self.project is None:
            self._notify("Projectを先に開いてください。", error=True)
            return
        selected = tuple(str(value) for value in self.selected_reference_updates.value)
        if not selected:
            self._notify("反映する更新行を選択してください。", error=True)
            return
        state = self.readiness_service.ui_state(self.project)
        if state is None or state.reference_data_snapshot is None:
            self._notify("参照snapshotを検証できません。", error=True)
            return
        snapshot = state.reference_data_snapshot
        departure = snapshot.departure_airport
        destination = snapshot.destination_airport
        route_points = dict(snapshot.route_points)
        check_points = dict(snapshot.check_points)
        try:
            for token in selected:
                kind, key, current = self._selected_reference_update_rows[token]
                if kind == "AIRPORT":
                    if not isinstance(current, AirportSelection):
                        raise ValueError("空港更新行の型が不正です。")
                    if token == "departure":
                        departure = current
                    else:
                        if (
                            current.pattern_altitude_validation_status
                            != PatternAltitudeValidationStatus.VERIFIED
                        ):
                            raise ValueError("目的空港の場周経路高度がVERIFIEDではありません。")
                        destination = current
                elif kind == "POINT":
                    if key is None or not isinstance(current, PointSelection):
                        raise ValueError("地点更新行の型が不正です。")
                    route_points[key] = current
                    node = next(item for item in self.project.route_nodes if item.id == key)
                    node.name = current.name
                    node.latitude_deg = current.latitude_deg
                    node.longitude_deg = current.longitude_deg
                    node.role = current.point_role
                    node.source = f"REFERENCE {current.source} / {current.source_revision}"
                else:
                    if key is None or not isinstance(current, CheckPointSelection):
                        raise ValueError("CP更新行の型が不正です。")
                    check_points[key] = current
                    reference = next(
                        item for item in self.project.visual_references if item.id == key
                    )
                    reference.name = current.name
                    reference.latitude_deg = current.latitude_deg
                    reference.longitude_deg = current.longitude_deg
                    reference.source = f"REFERENCE {current.source} / {current.source_revision}"
            updated = ReferenceDataSnapshot(
                departure_airport=departure,
                destination_airport=destination,
                route_points=route_points,
                check_points=check_points,
            )
            self.project_service.set_ui_state(
                self.project,
                state.model_copy(update={"reference_data_snapshot": updated}),
                reconfirmed=True,
            )
            self._refresh_route()
            self._refresh_selected_reference_updates()
            self._refresh_readiness()
            self._notify(
                f"選択した最新master {len(selected)}件を反映しました。再計算してください。"
            )
        except (KeyError, StopIteration, TypeError, ValueError) as error:
            self._notify(str(error), error=True)

    def _refresh_reference_data(self, _: Any) -> None:
        self._refresh_reference_catalog()
        self._notify("active参照データを再検証しました。")

    def _rollback_reference_data(self, _: Any) -> None:
        if self.reference_data_repository is None:
            self._notify("参照データrepositoryが設定されていません。", error=True)
            return
        try:
            catalog = self.reference_data_repository.rollback()
            self._refresh_reference_catalog()
            self._notify(
                f"参照データを{catalog.manifest.dataset_id}/"
                f"{catalog.manifest.revision}へ戻しました。"
            )
        except (ReferenceDataError, OSError) as error:
            self._notify(str(error), error=True)

    def _activate_reference_revision(self, _: Any) -> None:
        if self.reference_data_repository is None or self.reference_revision_selector.value is None:
            self._notify("有効化する参照版を選択してください。", error=True)
            return
        try:
            dataset_id, revision = self.reference_revision_selector.value
            self.reference_data_repository.activate(str(dataset_id), str(revision))
            self._refresh_reference_catalog()
            self._notify(f"参照データ {dataset_id}/{revision} をactive化しました。")
        except (ReferenceDataError, OSError, ValueError) as error:
            self._notify(str(error), error=True)

    def _import_reference_pack(self, _: Any) -> None:
        if self.reference_data_repository is None:
            self._notify("参照データrepositoryが設定されていません。", error=True)
            return
        try:
            source = self.reference_pack_path.value.strip()
            if not source:
                raise ValueError("取込元packディレクトリを入力してください。")
            catalog = self.reference_data_repository.import_pack(source, activate=True)
            self._refresh_reference_catalog()
            self._notify(
                f"参照pack {catalog.manifest.dataset_id}/"
                f"{catalog.manifest.revision} を取込・active化しました。"
            )
        except (ReferenceDataError, OSError, ValueError) as error:
            self._notify(str(error), error=True)

    def _export_reference_pack(self, _: Any) -> None:
        if self.reference_data_repository is None:
            self._notify("参照データrepositoryが設定されていません。", error=True)
            return
        try:
            destination = self.reference_pack_path.value.strip()
            if not destination:
                raise ValueError("書出先ディレクトリを入力してください。")
            exported = self.reference_data_repository.export_pack(destination)
            self._notify(f"active参照packを書き出しました: {exported}")
        except (ReferenceDataError, OSError, ValueError) as error:
            self._notify(str(error), error=True)

    def _reference_rows(self) -> dict[str, Any]:
        if self.reference_catalog is None:
            raise ValueError("active参照データを読み込めません。")
        kind = str(self.reference_kind.value)
        if kind == "AIRPORT":
            return dict(self.reference_catalog.airports)
        if kind == "POINT":
            return dict(self.reference_catalog.points)
        if kind == "CHECK_POINT":
            return dict(self.reference_catalog.check_points)
        raise ValueError("参照データkindが不正です。")

    def _load_reference_row(self, _: Any) -> None:
        try:
            row_id = self.reference_row_id.value.strip()
            if not row_id:
                raise ValueError("読込む行IDを入力してください。")
            row = self._reference_rows()[row_id]
            self.reference_row_json.value = row.model_dump_json(
                indent=2,
                exclude={"origin"},
            )
            self._notify(f"参照行 {row_id} を読込みました。")
        except KeyError:
            self._notify("指定した参照行がactive版にありません。", error=True)
        except ValueError as error:
            self._notify(str(error), error=True)

    def _parse_reference_row(
        self,
    ) -> AirportSelection | PointSelection | CheckPointSelection:
        raw = self.reference_row_json.value.encode("utf-8")
        kind = str(self.reference_kind.value)
        if kind == "AIRPORT":
            return validate_json_bytes(raw, AirportSelection)
        if kind == "POINT":
            return validate_json_bytes(raw, PointSelection)
        if kind == "CHECK_POINT":
            return validate_json_bytes(raw, CheckPointSelection)
        raise ValueError("参照データkindが不正です。")

    def _show_reference_diff(self, diff: ReferenceCatalogDiff) -> None:
        items: list[str] = []
        for label, changes in (
            ("追加", diff.added),
            ("編集", diff.updated),
            ("削除", diff.deleted),
        ):
            for kind, row_ids in changes.items():
                if row_ids:
                    items.append(
                        f"<li>{escape(label)} {escape(kind)}: {escape(', '.join(row_ids))}</li>"
                    )
        self.reference_diff.value = "<strong>変更差分:</strong><ul>" + "".join(items) + "</ul>"

    def _reference_revision_value(self) -> str:
        revision = str(self.reference_new_revision.value).strip()
        if not revision:
            raise ValueError("新しいrevision名を入力してください。")
        return revision

    def _save_reference_row(self, _: Any) -> None:
        if self.reference_data_repository is None:
            self._notify("参照データrepositoryが設定されていません。", error=True)
            return
        try:
            row = self._parse_reference_row()
            entered_id = self.reference_row_id.value.strip()
            if entered_id and entered_id != row.id:
                raise ValueError("行ID欄とJSONのidが一致しません。")
            revision = self._reference_revision_value()
            if isinstance(row, AirportSelection):
                catalog, diff = self.reference_data_repository.revise_active(
                    revision=revision,
                    upsert_airports=(row,),
                )
            elif isinstance(row, PointSelection):
                catalog, diff = self.reference_data_repository.revise_active(
                    revision=revision,
                    upsert_points=(row,),
                )
            else:
                catalog, diff = self.reference_data_repository.revise_active(
                    revision=revision,
                    upsert_check_points=(row,),
                )
            self.reference_row_id.value = row.id
            self._show_reference_diff(diff)
            self._refresh_reference_catalog()
            self._notify(f"参照行を新revision {catalog.manifest.revision} へ保存しました。")
        except (JsonStorageError, ReferenceDataError, OSError, ValueError) as error:
            self._notify(str(error), error=True)

    def _delete_reference_row(self, _: Any) -> None:
        if self.reference_data_repository is None:
            self._notify("参照データrepositoryが設定されていません。", error=True)
            return
        try:
            row_id = self.reference_row_id.value.strip()
            if not row_id:
                raise ValueError("削除する行IDを入力してください。")
            kind = str(self.reference_kind.value)
            revision = self._reference_revision_value()
            if kind == "AIRPORT":
                catalog, diff = self.reference_data_repository.revise_active(
                    revision=revision,
                    delete_airport_ids=(row_id,),
                )
            elif kind == "POINT":
                catalog, diff = self.reference_data_repository.revise_active(
                    revision=revision,
                    delete_point_ids=(row_id,),
                )
            elif kind == "CHECK_POINT":
                catalog, diff = self.reference_data_repository.revise_active(
                    revision=revision,
                    delete_check_point_ids=(row_id,),
                )
            else:
                raise ValueError("参照データkindが不正です。")
            self._show_reference_diff(diff)
            self._refresh_reference_catalog()
            self._notify(f"参照行を新revision {catalog.manifest.revision} で削除しました。")
        except (KeyError, ReferenceDataError, OSError, ValueError) as error:
            self._notify(str(error), error=True)

    def _selected_airport_rows(
        self,
        departure_id: str,
        destination_id: str,
    ) -> tuple[AirportSelection, AirportSelection]:
        if self.reference_catalog is not None:
            try:
                return (
                    self.reference_catalog.airports[departure_id],
                    self.reference_catalog.airports[destination_id],
                )
            except KeyError as error:
                raise ValueError("選択空港がactive参照データにありません。") from error
        by_id = {
            airport.id: self._legacy_airport_selection(airport)
            for airport in self.calculation_service.airports.all()
        }
        try:
            return by_id[departure_id], by_id[destination_id]
        except KeyError as error:
            raise ValueError("選択空港を参照データから解決できません。") from error

    def _store_reference_snapshot(
        self,
        departure_id: str,
        destination_id: str,
    ) -> None:
        if self.project is None:
            return
        departure, destination = self._selected_airport_rows(
            departure_id,
            destination_id,
        )
        state = self.readiness_service.ui_state(self.project) or PersistedUiState()
        snapshot = (
            self.reference_catalog.snapshot(
                departure_airport_id=departure_id,
                destination_airport_id=destination_id,
            )
            if self.reference_catalog is not None
            else ReferenceDataSnapshot(
                departure_airport=departure,
                destination_airport=destination,
            )
        )
        self.project_service.set_ui_state(
            self.project,
            state.model_copy(update={"reference_data_snapshot": snapshot}),
            reconfirmed=True,
        )

    def _align_endpoint_to_airport(self, *, departure: bool) -> None:
        if self.project is None or self.reference_catalog is None:
            self._notify(
                "Projectとactive参照データを先に読み込んでください。",
                error=True,
            )
            return
        ordered = self.project.ordered_nodes()
        if len(ordered) < 2:
            self._notify("2点以上のRouteを先に確定してください。", error=True)
            return
        selected_id = (
            self.departure_reference.value if departure else self.destination_reference.value
        )
        if selected_id is None:
            self._notify(
                "合わせる空港をFROM/TO masterから選択してください。",
                error=True,
            )
            return
        try:
            airport = self.reference_catalog.airports[str(selected_id)]
            if (
                not departure
                and airport.pattern_altitude_validation_status
                != PatternAltitudeValidationStatus.VERIFIED
            ):
                raise ValueError("目的空港の場周経路高度がVERIFIEDではありません。")
            state = self.readiness_service.ui_state(self.project)
            if state is None or state.reference_data_snapshot is None:
                raise ValueError("FROM/TOの参照snapshotを先に確定してください。")
            endpoint = ordered[0] if departure else ordered[-1]
            movement = geodesic_leg(
                endpoint.latitude_deg,
                endpoint.longitude_deg,
                airport.latitude_deg,
                airport.longitude_deg,
            ).distance_nm
            endpoint.latitude_deg = airport.latitude_deg
            endpoint.longitude_deg = airport.longitude_deg
            snapshot = state.reference_data_snapshot
            if departure:
                self.project.departure_airport_id = airport.id
                self.departure.value = airport.id
                updated_snapshot = snapshot.model_copy(
                    deep=True,
                    update={"departure_airport": airport},
                )
                label = "FROM"
            else:
                self.project.destination_airport_id = airport.id
                self.destination.value = airport.id
                updated_snapshot = snapshot.model_copy(
                    deep=True,
                    update={"destination_airport": airport},
                )
                current_plan = state.arrival_plan
                if current_plan is not None:
                    current_plan = current_plan.model_copy(
                        update={
                            "selected_pattern_altitude_ft_msl": None,
                            "selected_pattern_altitude_source": None,
                        }
                    )
                state = state.model_copy(update={"arrival_plan": current_plan})
                self._set_destination_pattern_altitude(airport.pattern_altitude_ft_msl)
                label = "TO"
            self.project_service.set_ui_state(
                self.project,
                state.model_copy(update={"reference_data_snapshot": updated_snapshot}),
                reconfirmed=True,
            )
            self.endpoint_alignment_status.value = (
                f"{label}端点を{escape(airport.icao)}の公示座標へ移動: {movement:.3f} NM"
            )
            self._refresh_route()
            self._refresh_selected_reference_updates()
            self._refresh_readiness()
            self._notify(f"{label}端点を公示座標へ合わせました。再計算してください。")
        except (KeyError, TypeError, ValueError) as error:
            self._notify(str(error), error=True)

    def _apply_arrival_plan(self, _: Any) -> None:
        if self.project is None or self.vrep_selector.value is None:
            self._notify("目的空港直前のVREPを選択してください。", error=True)
            return
        try:
            vrep_id = UUID(str(self.vrep_selector.value))
            ordered = self.project.ordered_nodes()
            if len(ordered) < 2 or ordered[-2].id != vrep_id:
                raise ValueError("VREPは目的空港直前のRoute点にしてください。")
            for node in self.project.route_nodes:
                if node.id == vrep_id:
                    node.role = RouteNodeRole.VISUAL_REPORTING_POINT
            if self.project.sections:
                self.project.sections[-1].phase = FlightPhase.VISUAL_ARRIVAL
            if len(self.project.sections) >= 2:
                self.project.sections[-2].phase = FlightPhase.DESCENT
            mode = ArrivalAltitudeMode(self.arrival_mode.value)
            manual_altitude = (
                None
                if mode == ArrivalAltitudeMode.STANDARD_DISTANCE_RULE
                else int(self.manual_vrep_altitude.value.strip())
            )
            reason = (
                None
                if mode == ArrivalAltitudeMode.STANDARD_DISTANCE_RULE
                else self.manual_vrep_reason.value.strip()
            )
            state = self.readiness_service.ui_state(self.project) or PersistedUiState()
            snapshot = state.reference_data_snapshot
            if snapshot is None:
                raise ValueError("目的空港の参照snapshotを先に確定してください。")
            selected_pattern = int(self.destination_pattern_altitude.value)
            selected_source = _pattern_altitude_source(
                selected_pattern,
                snapshot.destination_airport.pattern_altitude_ft_msl,
            )
            plan = ArrivalPlan(
                visual_reporting_point_node_id=vrep_id,
                selected_pattern_altitude_ft_msl=selected_pattern,
                selected_pattern_altitude_source=selected_source,
                altitude_mode=mode,
                manual_vrep_altitude_ft_msl=manual_altitude,
                manual_override_reason=reason,
            )
            if self.project.sections:
                destination = snapshot.destination_airport
                distance_nm = geodesic_leg(
                    ordered[-2].latitude_deg,
                    ordered[-2].longitude_deg,
                    destination.latitude_deg,
                    destination.longitude_deg,
                ).distance_nm
                self.project.sections[-1].planned_altitude_ft_msl = (
                    manual_altitude
                    if manual_altitude is not None
                    else standard_vrep_altitude_ft_msl(distance_nm, selected_pattern)
                )
            self.project_service.set_ui_state(
                self.project,
                state.model_copy(update={"arrival_plan": plan}),
                reconfirmed=True,
            )
            self._refresh_route()
            self._refresh_readiness()
            self._notify("VREPと到着高度方式を確定しました。")
        except (TypeError, ValueError) as error:
            self._notify(str(error), error=True)

    def _confirm_manual_qnh(self, _: Any) -> None:
        if self.project is None or not self.manual_qnh_confirmation.value:
            self._notify("手動QNHの確認欄を選択してください。", error=True)
            return
        try:
            self._sync_project_inputs()
            self.readiness_service.confirm_manual_qnh(
                self.project,
                self.outcome,
            )
            self._refresh_readiness()
            self._notify("手動QNH確認を現在のDATE・ETD・出発地へ記録しました。")
        except ValueError as error:
            self._notify(str(error), error=True)

    def _refresh_readiness(self) -> None:
        if self.project is None:
            self.readiness_evaluation = None
            self.status_bar.value = (
                "<strong>① 次の操作:</strong> KML/KMZを入力し、"
                "DATE・ETDを確認して「NAV LOGを作る」を実行してください。"
            )
            self.clearcopy.value = ""
            self.run_label.value = (
                "<strong>Forecast Run:</strong> 未選択 / <strong>Initial UTC:</strong> 未確定"
            )
            self._render_issues()
            self._update_action_states()
            return
        materialized = self.readiness_service.evaluate(
            self.project,
            self.outcome,
            editable=self.view_mode == ViewMode.EDITABLE,
        )
        self.project = materialized.project
        self.outcome = materialized.outcome
        self.readiness_evaluation = materialized.evaluation
        effective = materialized.evaluation.effective_issues
        blockers = [item for item in effective if item.effective_severity == IssueSeverity.BLOCKER]
        pending_warnings = [
            item
            for item in effective
            if item.effective_acknowledgement_required
            and item.ctx.ack_key not in self.project.acknowledged_warning_codes
        ]

        def codes(items: list[Any]) -> str:
            counts: dict[str, int] = {}
            order: list[str] = []
            for item in items:
                code = item.ctx.code
                if code not in counts:
                    order.append(code)
                    counts[code] = 0
                counts[code] += 1
            return ", ".join(
                code if counts[code] == 1 else f"{code} {counts[code]}件" for code in order
            )

        data = [item for item in blockers if item.ctx.code in DATA_ISSUE_CODES]
        route = [item for item in blockers if item.ctx.code in ROUTE_ISSUE_CODES]
        recalculation = [item for item in blockers if item.ctx.code == "RECALCULATION_REQUIRED"]
        assigned = {id(item) for item in (*data, *route, *recalculation)}
        other = [item for item in blockers if id(item) not in assigned]
        remaining: list[str] = []
        if data:
            remaining.append(f"データ欠落: {codes(data)}")
        if route:
            remaining.append(f"経路: {codes(route)}")
        if recalculation:
            remaining.append("再計算要")
        if other:
            remaining.append(f"その他Blocker: {codes(other)}")
        if pending_warnings:
            remaining.append(f"承認待ちWarning: {codes(pending_warnings)}")
        mode = " / SNAPSHOT_READONLY" if self.view_mode == ViewMode.SNAPSHOT_READONLY else ""
        self.status_bar.value = (
            f"<strong>地上準備状態:</strong> {materialized.evaluation.status.value}"
            f"{mode} / Blocker {len(blockers)}件"
            + (
                "<br><strong>② 地上準備完了まで:</strong> " + escape(" / ".join(remaining))
                if remaining
                else "<br><strong>② 地上準備の計画値が揃いました。</strong>"
            )
        )
        if self.outcome is not None:
            self.clearcopy.value = render_transfer_aid_html(
                self.project,
                self.outcome,
                effective_issues=materialized.evaluation.effective_issues,
                calculation_is_current=(materialized.evaluation.calculation_is_current),
                editable=self.view_mode == ViewMode.EDITABLE,
            )
        else:
            self.clearcopy.value = ""
        initial_time = self._forecast_initial_time_utc or "未確定"
        selected_run = (
            "未選択" if self.outcome is None else self.outcome.selected_forecast_run_id or "未確定"
        )
        self.run_label.value = (
            f"<strong>Forecast Run:</strong> {escape(selected_run)} / "
            f"<strong>Initial UTC:</strong> {escape(initial_time)}"
        )
        self._render_issues()
        self._auto_open_blocking_panel()
        self._update_action_states()

    def _auto_open_blocking_panel(self) -> None:
        details = self._details_accordion
        evaluation = self.readiness_evaluation
        if (
            details is None
            or details.selected_index is not None
            or self.route_state != RouteState.READY
            or evaluation is None
            or self.project is None
        ):
            return
        blocking = [
            item
            for item in evaluation.effective_issues
            if item.effective_severity == IssueSeverity.BLOCKER
            or (
                item.effective_acknowledgement_required
                and item.ctx.ack_key not in self.project.acknowledged_warning_codes
            )
        ]
        if not blocking:
            return
        codes = {item.ctx.code for item in blocking}
        if codes & DATA_ISSUE_CODES:
            details.selected_index = 4
        elif codes & ROUTE_ISSUE_CODES:
            details.selected_index = 1
        elif codes & {"PILOT_REQUIRED", "SHIP_REQUIRED"}:
            details.selected_index = 0
        else:
            details.selected_index = 2

    def _update_action_states(self) -> None:
        editable = self.view_mode == ViewMode.EDITABLE
        project_exists = self.project is not None
        outcome_exists = self.outcome is not None
        reference_enabled = editable and self.reference_data_repository is not None
        route_ready = bool(
            project_exists
            and self.route_state == RouteState.READY
            and self.project is not None
            and self.project.sections
        )
        allowed = bool(
            self.readiness_evaluation is not None and self.readiness_evaluation.transfer_aid_allowed
        )

        self.save_button.disabled = not editable or not project_exists
        self.calculate_button.disabled = not editable or not project_exists
        selected_shape = self.shape_candidates.value
        line_selection_required = bool(
            self.import_result is not None
            and len(self.import_result.lines) > 1
            and not (
                isinstance(selected_shape, tuple)
                and len(selected_shape) == 2
                and selected_shape[0] == "line"
            )
        )
        self.quick_calculate_button.disabled = not editable or line_selection_required
        self.snapshot_button.disabled = not editable or not project_exists or not outcome_exists
        self.download_transfer_aid_button.disabled = not allowed
        self.edit_name_button.disabled = (
            not editable or not project_exists or not outcome_exists or self._name_editing
        )
        if not editable:
            self._name_editing = False
        self.name.disabled = not (editable and self._name_editing)

        editable_controls = (
            self.pilot,
            self.ship,
            self.flight_date,
            self.departure_time,
            self.departure_reference,
            self.destination_reference,
            self.fuel,
            self.upload,
            self.kml_text,
            self.import_text_button,
            self.kmz_document_candidates,
            self.candidates,
            self.shape_candidates,
            self.point_role,
            self.point_linked_section,
            self.quick_run_confirmation,
            self.manual_name,
            self.manual_lat,
            self.manual_lon,
            self.route,
            self.delete_node,
            self.move_up,
            self.move_down,
            self.phase,
            self.altitude,
            self.wind_direction,
            self.wind_speed,
            self.temperature,
            self.tas,
            self.all_leg_altitude,
            self.vrep_selector,
            self.arrival_mode,
            self.manual_vrep_altitude,
            self.manual_vrep_reason,
            self.destination_pattern_altitude,
            self.manual_qnh,
            self.manual_qnh_confirmation,
            self.tgl_count,
        )
        for control in editable_controls:
            control.disabled = not editable
        self.departure.disabled = not editable or self.reference_data_repository is not None
        self.destination.disabled = not editable or self.reference_data_repository is not None

        for control in (
            self.reference_revision_selector,
            self.reference_pack_path,
            self.reference_new_revision,
            self.reference_kind,
            self.reference_row_id,
            self.reference_row_json,
            self.reference_search,
            self.reference_search_results,
            self.master_reference_kind,
            self.master_reference_row,
            self.selected_reference_updates,
        ):
            control.disabled = not reference_enabled
        for control in (
            self.refresh_reference_button,
            self.rollback_reference_button,
            self.activate_reference_button,
            self.import_reference_button,
            self.export_reference_button,
            self.load_reference_row_button,
            self.save_reference_row_button,
            self.delete_reference_row_button,
        ):
            control.disabled = not reference_enabled

        is_route_shape = bool(
            isinstance(selected_shape, tuple)
            and len(selected_shape) == 2
            and selected_shape[0] in {"line", "polygon"}
        )
        self.create_button.disabled = not editable
        self.add_candidate.disabled = (
            not editable or not project_exists or self.candidates.value is None
        )
        self.add_shape_route.disabled = not editable or not project_exists or not is_route_shape
        self.add_manual.disabled = not editable or not project_exists
        self.apply_phase.disabled = not editable or not route_ready
        self.apply_all_leg_altitude_button.disabled = not editable or not route_ready
        self.apply_arrival_plan_button.disabled = not editable or not route_ready
        self.destination_pattern_altitude.disabled = not editable or not route_ready
        self.confirm_manual_qnh_button.disabled = not editable or not project_exists
        self.quick_run_confirmation.disabled = not editable or self.import_result is None
        self.select_kmz_document_button.disabled = not editable or self._pending_kmz is None
        self.add_master_reference_button.disabled = (
            not reference_enabled or not project_exists or self.master_reference_row.value is None
        )
        self.apply_selected_reference_updates_button.disabled = (
            not reference_enabled or not project_exists or not self.selected_reference_updates.value
        )
        can_align = bool(
            reference_enabled
            and project_exists
            and route_ready
            and self.reference_catalog is not None
        )
        self.align_departure_endpoint_button.disabled = (
            not can_align or self.departure_reference.value is None
        )
        self.align_destination_endpoint_button.disabled = (
            not can_align or self.destination_reference.value is None
        )

        section_controls = (
            self.route,
            self.delete_node,
            self.move_up,
            self.move_down,
            self.phase,
            self.altitude,
            self.wind_direction,
            self.leg_variation,
            self.wind_speed,
            self.temperature,
            self.tas,
            self.apply_phase,
            self.all_leg_altitude,
            self.apply_all_leg_altitude_button,
            self.vrep_selector,
            self.arrival_mode,
            self.manual_vrep_altitude,
            self.manual_vrep_reason,
            self.apply_arrival_plan_button,
        )
        for control in section_controls:
            control.layout.display = "" if route_ready else "none"
        self.run_label.layout.display = "" if outcome_exists else "none"
        self.clearcopy.layout.display = "" if outcome_exists else "none"

        reasons: list[str] = []
        if not editable:
            reasons.append(
                "Snapshot読取専用: 自動取得・再計算・編集・保存・出力はできません。"
                "編集する場合は保存Projectを開いてください。"
            )
        if not project_exists:
            reasons.append("保存: KML/KMZからProjectを作成すると下書き保存できます。")
        if line_selection_required:
            reasons.append("NAV LOGを作る: 複数のLineStringから飛行経路を1件選択してください。")
        if not route_ready:
            reasons.append("Leg入力: 2点以上のRouteを確定するとPhase・ALT欄を表示します。")
        if not outcome_exists:
            reasons.append("Snapshot: NAV LOGを一度計算すると作成できます。")
        if not allowed:
            issue_codes = (
                []
                if self.readiness_evaluation is None
                else [
                    item.ctx.code
                    for item in self.readiness_evaluation.effective_issues
                    if item.effective_severity == IssueSeverity.BLOCKER
                    or (
                        item.effective_acknowledgement_required
                        and self.project is not None
                        and item.ctx.ack_key not in self.project.acknowledged_warning_codes
                    )
                ]
            )
            detail = (
                " / ".join(dict.fromkeys(issue_codes))
                if issue_codes
                else "Project作成・計算・EDITABLE表示が必要"
            )
            reasons.append(
                "転記補助HTML: " + detail + "。該当欄を確認し、必要なら再計算してください。"
            )
            self.download_transfer_aid_status.value = reasons[-1]
        else:
            self.download_transfer_aid_status.value = (
                "転記補助HTMLを出力できます。原票・WX・性能・警告と照合してください。"
            )
        self.action_reasons.value = (
            "<ul class='action-reasons'>"
            + "".join(f"<li>{escape(reason)}</li>" for reason in reasons)
            + "</ul>"
        )

    def _load_snapshot(self, _: Any) -> None:
        try:
            if self.project_id.value is None:
                raise ValueError("Projectを選択してください。")
            project_id = UUID(str(self.project_id.value))
            snapshot_id = UUID(self.snapshot_id.value.strip())
            snapshot = self.project_service.load_snapshot(
                project_id,
                snapshot_id,
            )
            self.project = snapshot.input_data
            self.outcome = snapshot.calculation_results
            initial_time = snapshot.forecast_metadata.get("initial_time_utc")
            self._forecast_initial_time_utc = None if initial_time is None else str(initial_time)
            self.view_mode = ViewMode.SNAPSHOT_READONLY
            self._refresh_route()
            self._populate_project_inputs()
            self._refresh_readiness()
            self._notify("Snapshotを読取専用で開きました。")
        except (TypeError, ValueError, OSError) as error:
            self._notify(str(error), error=True)

    def render(self) -> widgets.Widget:
        self.quick_calculate_button.description = "NAV LOGを作る / 再計算"
        import_panel = widgets.VBox(
            [
                self.import_text_button,
                self.kmz_document_candidates,
                self.select_kmz_document_button,
                self.import_summary,
                self.map_view,
                self.shape_candidates,
                self.add_shape_route,
                self.candidates,
                self.point_role,
                self.point_linked_section,
                self.add_candidate,
                self.master_reference_kind,
                self.master_reference_row,
                self.add_master_reference_button,
                self.manual_name,
                self.manual_lat,
                self.manual_lon,
                self.add_manual,
                self.quick_run_confirmation,
            ]
        )
        primary = widgets.VBox(
            [
                widgets.HTML("<h2>AutoNavLog NAV2 地上準備</h2>"),
                self.message,
                self.reference_status,
                self.status_bar,
                self.storage_status,
                widgets.HTML(
                    "<p>KML/XMLを貼り付けるか、KML/KMZファイルを選択してください。"
                    "KML高度は航法高度として使用しません。</p>"
                ),
                self.upload,
                self.kml_text,
                self.flight_date,
                self.departure_time,
                self.quick_calculate_button,
                self.project_id,
                self.load_button,
                self.action_reasons,
                self.quick_run_status,
            ]
        )
        project_panel = widgets.VBox(
            [
                self.pilot,
                self.ship,
                self.reference_catalog_label,
                self.departure_reference,
                self.destination_reference,
                self.departure,
                self.destination,
                self.create_button,
                self.snapshot_id,
                widgets.HBox(
                    [
                        self.align_departure_endpoint_button,
                        self.align_destination_endpoint_button,
                    ]
                ),
                self.endpoint_alignment_status,
                self.load_snapshot_button,
                widgets.HBox(
                    [
                        self.refresh_reference_button,
                        self.rollback_reference_button,
                    ]
                ),
            ]
        )
        route_panel = widgets.VBox(
            [
                widgets.HTML(
                    "<p>LegごとにPhaseとALTを確認してください。風・気温はMSM値が"
                    "利用できない場合だけ手入力し、理由を記録します。</p>"
                ),
                self.route,
                widgets.HBox([self.delete_node, self.move_up, self.move_down]),
                self.phase,
                self.altitude,
                self.wind_direction,
                self.leg_variation,
                self.wind_speed,
                self.temperature,
                self.tas,
                self.apply_phase,
                self.all_leg_altitude,
                self.apply_all_leg_altitude_button,
                self.vrep_selector,
                self.arrival_mode,
                self.destination_pattern_altitude,
                self.destination_pattern_altitude_help,
                self.manual_vrep_altitude,
                self.manual_vrep_reason,
                self.apply_arrival_plan_button,
            ]
        )
        review_panel = widgets.VBox(
            [
                self.fuel,
                self.variation_rule_summary,
                self.manual_qnh,
                self.tgl_count,
                self.manual_qnh_confirmation,
                self.confirm_manual_qnh_button,
                self.issues,
            ]
        )
        output_panel = widgets.VBox(
            [
                widgets.HBox([self.name, self.edit_name_button]),
                self.run_label,
                self.clearcopy,
                self.download_transfer_aid_button,
                self.download_transfer_aid_status,
                widgets.HBox([self.save_button, self.snapshot_button]),
            ]
        )
        reference_panel = widgets.VBox(
            [
                widgets.HTML(
                    "<p>変更はactive版を上書きせず、新しい不変revisionとして保存します。"
                    "Projectの既存snapshotへは自動反映しません。</p>"
                ),
                self.reference_revision_selector,
                self.activate_reference_button,
                self.reference_pack_path,
                self.reference_search,
                self.reference_search_results,
                widgets.HTML(
                    "<p>active版は既存Projectへ自動反映しません。"
                    "差分確認後に選択した行だけを反映します。</p>"
                ),
                self.selected_reference_update_status,
                self.selected_reference_updates,
                self.apply_selected_reference_updates_button,
                widgets.HBox([self.import_reference_button, self.export_reference_button]),
                self.reference_new_revision,
                self.reference_kind,
                self.reference_row_id,
                self.reference_row_json,
                widgets.HBox(
                    [
                        self.load_reference_row_button,
                        self.save_reference_row_button,
                        self.delete_reference_row_button,
                    ]
                ),
                self.reference_diff,
            ]
        )
        details = widgets.Accordion(
            children=(
                project_panel,
                route_panel,
                review_panel,
                output_panel,
                reference_panel,
                import_panel,
            ),
            selected_index=None,
        )
        self._details_accordion = details
        for index, title in enumerate(
            (
                "Project・参照データ・Snapshot",
                "Route・Leg・VREPの編集",
                "飛行計画・転記前確認・警告",
                "結果・保存・転記補助HTML",
                "参照データpack・行管理",
                "KML/KMZ取込・形状・Point役割",
            )
        ):
            details.set_title(index, title)
        root = widgets.VBox([primary, details])
        root.add_class("autonavlog-app")
        display(  # type: ignore[no-untyped-call]
            widgets.HTML(
                """
                <style>
                .autonavlog-app {max-width:760px;margin:0 auto;gap:8px}
                .autonavlog-app .widget-label {min-width:92px}
                .autonavlog-app input,.autonavlog-app select,
                .autonavlog-app button {min-height:44px}
                .autonavlog-app .action-reasons {margin:4px 0;padding-left:20px}
                @media(max-width:390px){
                  .autonavlog-app .widget-label{min-width:0;width:100%}
                  .autonavlog-app .widget-hbox{flex-wrap:wrap}
                  .autonavlog-app {width:100%;padding:0 4px;box-sizing:border-box}
                }
                </style>
                """
            )
        )
        self._update_action_states()
        self._auto_open_blocking_panel()
        return root

    def _create_project(self, _: Any) -> None:
        try:
            self._create_project_from_inputs()
            self._notify("Projectを作成しました。")
        except Exception as error:
            self._notify(str(error), error=True)

    def _create_project_from_inputs(self) -> Project:
        departure_time = self._parse_departure_datetime()
        if not self.departure.value.strip() or not self.destination.value.strip():
            raise ValueError("FROMとTOを入力してください。")
        project_name = self._suggest_project_name()
        self.name.value = project_name
        self.project = self.project_service.create(
            name=project_name,
            pilot_name=self.pilot.value,
            ship_identifier=self.ship.value,
            flight_date=self.flight_date.value,
            planned_departure_time_jst=departure_time,
            departure_airport_id=self.departure.value.strip(),
            destination_airport_id=self.destination.value.strip(),
            total_usable_fuel_gal=self.fuel.value,
            default_variation_deg_east=self.variation.value,
        )
        self.project.metadata["project_name_auto"] = True
        self.project.metadata["project_name_generated"] = project_name
        self._refresh_project_list(selected=str(self.project.id))
        return self.project

    @staticmethod
    def _normalize_project_name(value: str) -> str:
        forbidden = set('<>:"/\\|?*')
        normalized = "".join(
            "_"
            if character in forbidden
            else ""
            if ord(character) < 32 or ord(character) == 127
            else character
            for character in value.strip()
        ).strip()
        return (normalized or "route")[:60]

    def _unique_initial_save_name(self, requested: str) -> str:
        if self.project is None or self.project.revision != 0:
            return requested
        existing = {summary.name for summary in self.project_service.list_projects()}
        if requested not in existing:
            return requested
        ordinal = 2
        while True:
            suffix = f"_{ordinal}"
            candidate = f"{requested[: 60 - len(suffix)]}{suffix}"
            if candidate not in existing:
                return candidate
            ordinal += 1

    def _suggest_project_name(self) -> str:
        if self.reference_data_repository is None and self.name.value.strip():
            return self._normalize_project_name(self.name.value)
        if self.project is not None and not self.project.metadata.get("project_name_auto", False):
            return self.project.name
        placemark: str | None = None
        selected = self.shape_candidates.value
        if self.import_result is not None:
            if isinstance(selected, tuple) and len(selected) == 2:
                kind, index = selected
                if kind == "line":
                    placemark = self.import_result.lines[index].name
                elif kind == "polygon":
                    placemark = self.import_result.polygons[index].name
            if placemark is None and len(self.import_result.lines) == 1:
                placemark = self.import_result.lines[0].name
            if placemark is None and len(self.import_result.polygons) == 1:
                placemark = self.import_result.polygons[0].name
            if placemark is None and len(self.import_result.points) == 1:
                placemark = self.import_result.points[0].name
        date_value = self.flight_date.value or datetime.now(JST).date()
        if placemark:
            suffix = placemark
        elif self.departure.value.strip() and self.destination.value.strip():
            suffix = f"{self.departure.value.strip()}-{self.destination.value.strip()}"
        else:
            suffix = "route"
        return self._normalize_project_name(f"{date_value.isoformat()}_{suffix}")

    def _refresh_project_list(self, *, selected: str | None = None) -> None:
        summaries = self.project_service.list_projects()
        options: list[tuple[str, str | None]] = [("未選択", None)]
        options.extend(
            (
                f"{summary.name}（{summary.updated_at.astimezone(JST):%Y-%m-%d %H:%M}・"
                f"{summary.status.value}）",
                str(summary.id),
            )
            for summary in summaries
        )
        current = selected if selected is not None else self.project_id.value
        if selected is not None and selected not in {value for _, value in options}:
            options.insert(1, (self.name.value or selected, selected))
        self.project_id.options = options
        if current in {value for _, value in options}:
            self.project_id.value = current

    def _parse_departure_datetime(self) -> datetime:
        if self.flight_date.value is None:
            raise ValueError("DATEを入力してください。")
        try:
            hour, minute = (int(value) for value in self.departure_time.value.split(":", 1))
            return datetime.combine(
                self.flight_date.value,
                datetime.min.time(),
                tzinfo=JST,
            ).replace(hour=hour, minute=minute)
        except (TypeError, ValueError) as error:
            raise ValueError("ETD JSTはHH:MM形式で入力してください。") from error

    def _readiness_input_changed(self, change: dict[str, Any]) -> None:
        if (
            self._suspend_readiness_sync
            or self.project is None
            or change.get("old") == change.get("new")
        ):
            return
        try:
            self._sync_project_inputs()
            self._refresh_readiness()
        except (TypeError, ValueError) as error:
            self.readiness_evaluation = None
            self.download_transfer_aid_button.disabled = True
            self.status_bar.value = (
                f"<strong style='color:#922b21'>入力未確定:</strong> {escape(str(error))}"
            )

    def _load_project(self, _: Any) -> None:
        try:
            if self.project_id.value is None:
                raise ValueError("Projectを選択してください。")
            self.project = self.project_service.load(UUID(str(self.project_id.value)))
            self.view_mode = ViewMode.EDITABLE
            self.outcome = None
            self._forecast_initial_time_utc = None
            self._refresh_route()
            self._populate_project_inputs()
            self._refresh_readiness()
            self._notify("Projectを読み込みました。")
        except Exception as error:
            self._notify(str(error), error=True)

    def _populate_project_inputs(self) -> None:
        if self.project is None:
            return
        self._suspend_readiness_sync = True
        try:
            project = self.project
            self.name.value = project.name
            self.pilot.value = project.pilot_name
            self.ship.value = project.ship_identifier
            self.flight_date.value = project.flight_date
            self.departure_time.value = project.planned_departure_time_jst.strftime("%H:%M")
            self.departure.value = project.departure_airport_id
            self.destination.value = project.destination_airport_id
            self.fuel.value = project.total_usable_fuel_gal
            self.variation.value = project.default_variation_deg_east
            self.manual_qnh.value = self._optional_text(project.manual_qnh_hpa)
            self.tgl_count.value = project.tgl_count
            state = self.readiness_service.ui_state(project)
            if state is not None and state.reference_data_snapshot is not None:
                snapshot = state.reference_data_snapshot
                departure_values = {value for _, value in self.departure_reference.options}
                destination_values = {value for _, value in self.destination_reference.options}
                if snapshot.departure_airport.id in departure_values:
                    self.departure_reference.value = snapshot.departure_airport.id
                if snapshot.destination_airport.id in destination_values:
                    self.destination_reference.value = snapshot.destination_airport.id
                self.destination_pattern_altitude.value = _pattern_altitude_input_value(
                    snapshot.destination_airport.pattern_altitude_ft_msl
                )
            if state is not None and state.arrival_plan is not None:
                arrival = state.arrival_plan
                vrep_value = str(arrival.visual_reporting_point_node_id)
                if vrep_value in {value for _, value in self.vrep_selector.options}:
                    self.vrep_selector.value = vrep_value
                self.arrival_mode.value = arrival.altitude_mode.value
                self.manual_vrep_altitude.value = (
                    ""
                    if arrival.manual_vrep_altitude_ft_msl is None
                    else str(arrival.manual_vrep_altitude_ft_msl)
                )
                self.manual_vrep_reason.value = arrival.manual_override_reason or ""
                selected_pattern = arrival.selected_pattern_altitude_ft_msl
                if selected_pattern is None and state.reference_data_snapshot is not None:
                    selected_pattern = _pattern_altitude_input_value(
                        state.reference_data_snapshot.destination_airport.pattern_altitude_ft_msl
                    )
                if selected_pattern is not None:
                    self.destination_pattern_altitude.value = selected_pattern
        finally:
            self._suspend_readiness_sync = False

    def _import_file(self, change: dict[str, Any]) -> None:
        content: bytes | None = None
        uploaded_name = "upload.kmz"
        try:
            if not change["new"]:
                return
            self.route_state = RouteState.PARSING
            value = change["new"]
            uploaded = value[0] if isinstance(value, tuple) else next(iter(value.values()))
            raw_content = uploaded["content"]
            content = (
                raw_content.tobytes() if hasattr(raw_content, "tobytes") else bytes(raw_content)
            )
            uploaded_name = uploaded.get("name") or uploaded.get("metadata", {}).get("name")
            self._accept_import_result(
                import_kml_or_kmz(
                    content,
                    filename=uploaded_name or "upload.kml",
                )
            )
            self._imported_kml_text = None
        except KmlDocumentSelectionRequired as selection:
            self._clear_import_result()
            if content is None:
                raise
            self._pending_kmz = (content, uploaded_name)
            self.kmz_document_candidates.options = selection.candidates
            self.select_kmz_document_button.disabled = False
            self.route_state = RouteState.NEEDS_SELECTION
            self._notify(
                "KMZに複数のKMLがあります。使用するKMLを選択してください。",
                error=True,
            )
        except Exception as error:
            self._clear_import_result()
            self.route_state = RouteState.INVALID
            self._notify(str(error), error=True)

    def _select_kmz_document(self, _: Any) -> None:
        if self._pending_kmz is None or self.kmz_document_candidates.value is None:
            self._notify("KMZ内のKMLを選択してください。", error=True)
            return
        content, filename = self._pending_kmz
        try:
            self._accept_import_result(
                import_kml_or_kmz(
                    content,
                    filename=filename,
                    kmz_kml_filename=str(self.kmz_document_candidates.value),
                )
            )
            self._pending_kmz = None
            self.kmz_document_candidates.options = ()
            self.select_kmz_document_button.disabled = True
        except Exception as error:
            self.route_state = RouteState.INVALID
            self._notify(str(error), error=True)

    def _import_text(self, _: Any) -> None:
        self.route_state = RouteState.PARSING
        try:
            if not self.kml_text.value.strip():
                raise ValueError("KML/XMLを貼り付けてください。")
            self._accept_import_result(import_kml_text(self.kml_text.value))
            self._imported_kml_text = self.kml_text.value
        except Exception as error:
            self._clear_import_result()
            self.route_state = RouteState.INVALID
            self._notify(str(error), error=True)

    def _accept_import_result(self, result: KmlImportResult) -> None:
        self.import_result = result
        self._kml_altitude_note = self._seed_altitude_from_single_polygon(result)
        self.candidates.options = [
            (
                f"{point.name} ({point.latitude_deg:.5f}, {point.longitude_deg:.5f})",
                index,
            )
            for index, point in enumerate(result.points)
        ]
        shape_options = [
            (
                f"LineString: {line.name} "
                f"({len(line.coordinates)}点 / {imported_line_length_nm(line):.1f} NM)",
                ("line", index),
            )
            for index, line in enumerate(result.lines)
        ] + [
            (
                f"Polygon境界: {polygon.name} ({max(0, len(polygon.outer_boundary) - 1)}点)",
                ("polygon", index),
            )
            for index, polygon in enumerate(result.polygons)
        ]
        self.shape_candidates.options = shape_options
        shape_count = len(result.lines) + len(result.polygons)
        if len(result.lines) == 1:
            self.shape_candidates.value = ("line", 0)
            self.shape_candidates.layout.display = "none" if shape_count == 1 else ""
        elif not result.lines and len(result.polygons) == 1:
            self.shape_candidates.value = ("polygon", 0)
            self.shape_candidates.layout.display = ""
        else:
            self.shape_candidates.value = None
            self.shape_candidates.layout.display = ""
        self._pending_kmz = None
        self.kmz_document_candidates.options = ()
        self.select_kmz_document_button.disabled = True
        self.route_state = RouteState.NEEDS_SELECTION
        self.polygon_route_confirmation.value = False
        self._shape_selected({"new": self.shape_candidates.value})
        summary = (
            f"<p><strong>取込結果:</strong> {len(result.points)}点、"
            f"{len(result.lines)} LineString、{len(result.polygons)} Polygon</p>"
        )
        if result.polygons:
            summary += (
                "<p style='color:#9a6700'><strong>注意:</strong> Polygonは空域・区域の"
                "境界を表す場合があり、自動的に飛行経路を意味しません。地図で確認し、"
                "Routeへ変換する場合だけ確認欄を選択してください。</p>"
            )
        if self._kml_altitude_note is not None:
            summary += (
                "<p style='color:#075985'><strong>KML高度から自動入力:</strong> "
                f"{escape(self._kml_altitude_note)}</p>"
            )
        if result.warnings:
            warning_items = "".join(f"<li>{escape(warning)}</li>" for warning in result.warnings)
            summary += f"<p><strong>取込警告:</strong></p><ul>{warning_items}</ul>"
        if not result.lines and result.polygons:
            summary += (
                "<p><strong>線（LineString）が見つかりません。</strong> "
                f"読み込んだのは面（Polygon）{len(result.polygons)}件です。</p>"
                "<ul><li>経路として使う場合: 面の外周を順にたどります。空域・区域の"
                "境界なら意図した経路になりません。〔面を経路として使う（確認）〕</li>"
                "<li>経路を作り直す場合: Google Earthの「パスを追加」で経路を作成し、"
                "そのKMLを読み込んでください。</li></ul>"
            )
        summary += (
            "<p><strong>高度:</strong> KML/KMZ内の高度値は航法計画高度に使用しません。"
            "ALT欄を確認してください。</p>"
        )
        self.import_summary.value = summary
        self._notify(
            f"{len(result.points)}点、{len(result.lines)} LineString、"
            f"{len(result.polygons)} Polygonを読み込みました。"
        )
        self._refresh_map()

    def _clear_import_result(self) -> None:
        self.import_result = None
        self._imported_kml_text = None
        self._kml_altitude_note = None
        self.candidates.options = []
        self.shape_candidates.options = []
        self.shape_candidates.layout.display = ""
        self.polygon_route_confirmation.value = False
        self.polygon_route_confirmation.disabled = True
        self.add_shape_route.disabled = True
        self.import_summary.value = ""
        self.route_state = RouteState.EMPTY
        self._refresh_map()
        self._update_action_states()

    def _shape_selected(self, change: dict[str, Any]) -> None:
        selected = change.get("new")
        is_shape = (
            isinstance(selected, tuple)
            and len(selected) == 2
            and selected[0] in {"line", "polygon"}
        )
        self.add_shape_route.disabled = not is_shape
        self.quick_run_confirmation.disabled = self.import_result is None
        if change.get("old") != selected:
            self.quick_run_confirmation.value = False
        self._refresh_map()
        self._update_action_states()

    def _invalidate_quick_run_confirmation(self, change: dict[str, Any]) -> None:
        self.route_state = RouteState.EMPTY
        if change.get("old") != change.get("new"):
            self.quick_run_confirmation.value = False

    def _all_leg_altitude_changed(self, change: dict[str, Any]) -> None:
        self._invalidate_quick_run_confirmation(change)
        if change.get("old") != change.get("new") and not self._setting_kml_altitude:
            self._all_leg_altitude_user_edited = True

        self._update_action_states()

    def _seed_altitude_from_single_polygon(
        self,
        result: KmlImportResult,
    ) -> str | None:
        if self._all_leg_altitude_user_edited:
            return None
        if self.project is not None and (self.project.route_nodes or self.project.sections):
            return None
        if result.points or result.lines or len(result.polygons) != 1:
            return None
        polygon = result.polygons[0]
        minimum_m = polygon.minimum_altitude_m
        maximum_m = polygon.maximum_altitude_m
        if (
            polygon.altitude_mode != "absolute"
            or minimum_m is None
            or maximum_m is None
            or not isfinite(minimum_m)
            or not isfinite(maximum_m)
            or maximum_m <= 0
            or not isclose(minimum_m, maximum_m, rel_tol=0, abs_tol=0.1)
        ):
            return None
        altitude_ft = int(round(maximum_m / FT_TO_M))
        if altitude_ft <= 0:
            return None
        self._setting_kml_altitude = True
        try:
            self.all_leg_altitude.value = float(altitude_ft)
        finally:
            self._setting_kml_altitude = False
        self.quick_run_confirmation.value = False
        return (
            f"absolute上限高度 {maximum_m:g} mを{altitude_ft:,} ftへ換算し、"
            "新規Legの全Leg ALTへ入力しました（計算前に確認が必要）"
        )

    def _add_imported_point(self, _: Any) -> None:
        if self.import_result is None or self.candidates.value is None:
            return
        point = self.import_result.points[self.candidates.value]
        if self.project is None:
            self._notify("先にProjectを作成してください。", error=True)
            return
        selected_value = self.point_role.value
        if selected_value is None:
            self._notify("Pointの役割を選択してください。", error=True)
            return
        selected_role = str(selected_value)
        if selected_role in {"CHECK_POINT", "REFERENCE_ONLY"}:
            linked_section_id: UUID | None = None
            if selected_role == "CHECK_POINT":
                if self.point_linked_section.value is None:
                    self._notify("CPを関連付けるLegを選択してください。", error=True)
                    return
                linked_section_id = UUID(str(self.point_linked_section.value))
            self.project.visual_references.append(
                VisualReference(
                    project_id=self.project.id,
                    name=point.name,
                    latitude_deg=point.latitude_deg,
                    longitude_deg=point.longitude_deg,
                    role=(
                        VisualReferenceRole.CHECK_POINT
                        if selected_role == "CHECK_POINT"
                        else VisualReferenceRole.OTHER
                    ),
                    linked_section_id=linked_section_id,
                    source="KML/KMZ Point",
                )
            )
            self._refresh_map()
            self._refresh_readiness()
            self._notify(
                f"{point.name}を"
                + ("経路外CP" if selected_role == "CHECK_POINT" else "参照点")
                + "として追加しました。"
            )
            return
        self._append_node(
            point.name,
            point.latitude_deg,
            point.longitude_deg,
            "KML/KMZ",
            role=RouteNodeRole(selected_role),
        )

    def _add_imported_shape(self, _: Any) -> None:
        if self.project is None:
            self._notify("先にProjectを作成してください。", error=True)
            return
        if self.import_result is None or self.shape_candidates.value is None:
            self._notify("Routeへ変換する形状を選択してください。", error=True)
            return
        kind, index = self.shape_candidates.value
        if kind == "line":
            line = select_imported_line(self.import_result, index)
            name = line.name
            coordinates = line.coordinates
            source = "KML/KMZ LineString"
        elif kind == "polygon":
            if not self.polygon_route_confirmation.value:
                self._notify(
                    "Polygonは空域・区域境界の可能性があります。"
                    "確認欄を選択してからRouteへ変換してください。",
                    error=True,
                )
                return
            polygon = self.import_result.polygons[index]
            name = polygon.name
            coordinates = select_imported_polygon_outer(self.import_result, index)
            source = "KML/KMZ Polygon (confirmed)"
        else:
            self._notify("未対応のKML形状です。", error=True)
            return
        route_coordinates: list[tuple[float, float]] = []
        for coordinate in coordinates:
            if not route_coordinates or coordinate != route_coordinates[-1]:
                route_coordinates.append(coordinate)
        if self.project.route_nodes and route_coordinates:
            last_node = self.project.ordered_nodes()[-1]
            if route_coordinates[0] == (
                last_node.latitude_deg,
                last_node.longitude_deg,
            ):
                route_coordinates.pop(0)
        if len(route_coordinates) < 2:
            self._notify("選択形状にはRoute化できる座標が不足しています。", error=True)
            return
        start_sequence = len(self.project.route_nodes)
        nodes = [
            RouteNode(
                project_id=self.project.id,
                sequence=start_sequence + offset,
                name=(
                    f"WP{start_sequence + offset + 1}"
                    if self.reference_data_repository is not None
                    else f"{name} {offset + 1:02d}"
                ),
                latitude_deg=latitude,
                longitude_deg=longitude,
                role=(
                    RouteNodeRole.AIRPORT
                    if start_sequence == 0 and offset == 0
                    else RouteNodeRole.ROUTE_POINT
                ),
                source=source,
            )
            for offset, (latitude, longitude) in enumerate(route_coordinates)
        ]
        self.project.route_nodes.extend(nodes)
        self._rebuild_sections()
        self._refresh_route()
        self._notify(f"{name}の{len(nodes)}点をRouteへ一括追加しました。")

    def _add_manual_point(self, _: Any) -> None:
        self._append_node(
            self.manual_name.value or "Route Point",
            self.manual_lat.value,
            self.manual_lon.value,
            "MANUAL",
        )

    def _append_node(
        self,
        name: str,
        latitude: float,
        longitude: float,
        source: str,
        *,
        role: RouteNodeRole | None = None,
    ) -> None:
        if self.project is None:
            self._notify("先にProjectを作成してください。", error=True)
            return
        adopted_role = role or (
            RouteNodeRole.AIRPORT if not self.project.route_nodes else RouteNodeRole.ROUTE_POINT
        )
        self.project.route_nodes.append(
            RouteNode(
                project_id=self.project.id,
                sequence=len(self.project.route_nodes),
                name=name,
                latitude_deg=latitude,
                longitude_deg=longitude,
                role=adopted_role,
                source=source,
            )
        )
        self._rebuild_sections()
        self._refresh_route()

    def _delete_node(self, _: Any) -> None:
        if self.project is None or self.route.value is None:
            return
        self.project.route_nodes.pop(self.route.value)
        self._normalize_nodes()

    def _move_node(self, direction: int) -> None:
        if self.project is None or self.route.value is None:
            return
        source = self.route.value
        target = source + direction
        if not 0 <= target < len(self.project.route_nodes):
            return
        self.project.route_nodes[source], self.project.route_nodes[target] = (
            self.project.route_nodes[target],
            self.project.route_nodes[source],
        )
        self._normalize_nodes()
        self.route.value = target

    def _normalize_nodes(self) -> None:
        if self.project is None:
            return
        for index, node in enumerate(self.project.route_nodes):
            node.sequence = index
        self._rebuild_sections()
        self._refresh_route()

    def _rebuild_sections(self) -> None:
        if self.project is None:
            return
        previous_sections = {
            (section.from_node_id, section.to_node_id): section for section in self.project.sections
        }
        rebuilt: list[NavSection] = []
        for index, (start, end) in enumerate(
            zip(self.project.route_nodes, self.project.route_nodes[1:], strict=False)
        ):
            previous = previous_sections.get((start.id, end.id))
            if previous is None:
                rebuilt.append(
                    NavSection(
                        project_id=self.project.id,
                        sequence=index,
                        from_node_id=start.id,
                        to_node_id=end.id,
                        phase=FlightPhase.CRUISE,
                        planned_altitude_ft_msl=self.all_leg_altitude.value,
                    )
                )
            else:
                rebuilt.append(
                    previous.model_copy(
                        update={
                            "project_id": self.project.id,
                            "sequence": index,
                        }
                    )
                )
        self.project.sections = rebuilt

    def _apply_phase(self, _: Any) -> None:
        if self.project is None or self.route.value is None:
            return
        index = self.route.value
        if index >= len(self.project.sections):
            self._notify("最終点には出発Legがありません。", error=True)
            return
        direction = self._optional_float(self.wind_direction.value)
        speed = self._optional_float(self.wind_speed.value)
        if (direction is None) != (speed is None):
            self._notify("手動風は風向・風速を両方入力してください。", error=True)
            return
        self.project.sections[index] = NavSection.model_validate(
            self.project.sections[index].model_dump()
            | {
                "phase": FlightPhase(self.phase.value),
                "planned_altitude_ft_msl": self.altitude.value,
                "manual_wind_direction_deg": direction,
                "manual_wind_speed_kt": speed,
                "manual_temperature_c": self._optional_float(self.temperature.value),
                "manual_tas_kt": self._optional_float(self.tas.value),
            },
        )
        self._refresh_route()

    def _apply_all_leg_altitude(self, _: Any) -> None:
        if self.project is None or not self.project.sections:
            self._notify("高度を適用するLegがありません。", error=True)
            return
        if self.all_leg_altitude.value <= 0:
            self._notify("全Leg ALT ftは0より大きい値を入力してください。", error=True)
            return
        count = len(self.project.sections)
        self.project.sections = [
            section.model_copy(
                update={
                    "planned_altitude_ft_msl": self.all_leg_altitude.value,
                }
            )
            for section in self.project.sections
        ]
        self._refresh_route()
        self._notify(
            f"明示操作により既存値を含む{count} Legの計画高度を"
            f"{self.all_leg_altitude.value:g} ftへ更新しました。"
        )

    def _refresh_route(self) -> None:
        if self.project is None:
            self.route.options = []
            self.route_state = RouteState.EMPTY
            return
        options: list[tuple[str, int]] = []
        for index, node in enumerate(self.project.route_nodes):
            if index >= len(self.project.sections):
                label = f"{index + 1}. {node.name}（終点）"
            else:
                section = self.project.sections[index]
                destination = self.project.route_nodes[index + 1]
                label = f"{index + 1}. {node.name} → {destination.name} / {section.phase.value}"
            options.append((label, index))
        self.route.options = options
        self.route_state = (
            RouteState.READY
            if len(self.project.route_nodes) >= 2 and bool(self.project.sections)
            else RouteState.EMPTY
        )
        self.point_linked_section.options = [
            (f"Leg {section.sequence + 1}", str(section.id))
            for section in self.project.ordered_sections()
        ]
        vrep_options = [(node.name, str(node.id)) for node in self.project.ordered_nodes()[:-1]]
        self.vrep_selector.options = vrep_options
        if len(self.project.route_nodes) >= 2:
            penultimate = self.project.ordered_nodes()[-2]
            self.vrep_selector.value = str(penultimate.id)
        self._refresh_map()
        if self.outcome is not None:
            self._refresh_readiness()

    def _route_selected(self, change: dict[str, Any]) -> None:
        if self.project is None or change["new"] is None:
            return
        index = change["new"]
        if index >= len(self.project.sections):
            return
        section = self.project.sections[index]
        self.phase.value = section.phase.value
        start = self.project.ordered_nodes()[index]
        variation = variation_for_departure_latitude(start.latitude_deg)
        self.leg_variation.value = (
            f"<strong>選択Leg VAR E:</strong> {variation.degrees_east:+g}°（出発緯度 "
            f"{start.latitude_deg:.4f}°）"
        )
        self.altitude.value = section.planned_altitude_ft_msl
        self.wind_direction.value = self._optional_text(section.manual_wind_direction_deg)
        self.wind_speed.value = self._optional_text(section.manual_wind_speed_kt)
        self.temperature.value = self._optional_text(section.manual_temperature_c)
        self.tas.value = self._optional_text(section.manual_tas_kt)

    def _refresh_map(self) -> None:
        coordinates: list[tuple[float, float]] = []
        if self.project is not None:
            coordinates.extend(
                (node.latitude_deg, node.longitude_deg) for node in self.project.ordered_nodes()
            )
            coordinates.extend(
                (reference.latitude_deg, reference.longitude_deg)
                for reference in self.project.visual_references
            )
        if self.import_result is not None:
            coordinates.extend(
                (point.latitude_deg, point.longitude_deg) for point in self.import_result.points
            )
            for line in self.import_result.lines:
                coordinates.extend(line.display_coordinates)
            for polygon in self.import_result.polygons:
                coordinates.extend(polygon.display_outer_boundary)
        if self.outcome is not None:
            coordinates.extend(
                (point.latitude_deg, point.longitude_deg) for point in self.outcome.derived_points
            )
            coordinates.extend(
                (projection.abeam_latitude_deg, projection.abeam_longitude_deg)
                for projection in self.outcome.check_point_projections
            )
        if not coordinates:
            self.map_view.value = ""
            return
        map_widget = folium.Map(
            location=coordinates[0],
            zoom_start=8,
            control_scale=True,
        )
        if self.import_result is not None:
            selected_shape = self.shape_candidates.value
            for index, line in enumerate(self.import_result.lines):
                selected = selected_shape == ("line", index)
                folium.PolyLine(  # type: ignore[no-untyped-call]
                    line.display_coordinates,
                    color="#2471a3" if selected else "#7f8c8d",
                    weight=4 if selected else 2,
                    dash_array=None if selected else "5 5",
                    tooltip=f"{escape(line.name)} (LineString)",
                ).add_to(map_widget)
            for index, polygon in enumerate(self.import_result.polygons):
                selected = selected_shape == ("polygon", index)
                folium.Polygon(
                    locations=polygon.display_outer_boundary,
                    color="#c0392b" if selected else "#d35400",
                    weight=4 if selected else 2,
                    fill=True,
                    fill_color="#e67e22",
                    fill_opacity=0.18 if selected else 0.08,
                    tooltip=f"{escape(polygon.name)} (Polygon / 空域・区域境界)",
                ).add_to(map_widget)
                for inner_boundary in polygon.inner_boundaries:
                    folium.PolyLine(  # type: ignore[no-untyped-call]
                        inner_boundary,
                        color="#d35400",
                        weight=2,
                        dash_array="4 4",
                        tooltip=f"{escape(polygon.name)} (内周)",
                    ).add_to(map_widget)
        if self.project is not None:
            route = self.project.ordered_nodes()
            for node in route:
                folium.Marker(
                    (node.latitude_deg, node.longitude_deg),
                    tooltip=escape(node.name),
                ).add_to(map_widget)
            if len(route) >= 2:
                folium.PolyLine(  # type: ignore[no-untyped-call]
                    [(node.latitude_deg, node.longitude_deg) for node in route],
                    color="#1f618d",
                    weight=4,
                ).add_to(map_widget)
            projections = {
                projection.checkpoint_id: projection
                for projection in (
                    [] if self.outcome is None else self.outcome.check_point_projections
                )
            }
            for reference in self.project.visual_references:
                is_cp = reference.role == VisualReferenceRole.CHECK_POINT
                color = "#8e44ad" if is_cp else "#566573"
                role_label = "CP" if is_cp else "参照点"
                folium.CircleMarker(
                    (reference.latitude_deg, reference.longitude_deg),
                    radius=6,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.85,
                    tooltip=f"{role_label}: {escape(reference.name)}",
                ).add_to(map_widget)
                projection = projections.get(reference.id)
                if projection is not None:
                    abeam = (
                        projection.abeam_latitude_deg,
                        projection.abeam_longitude_deg,
                    )
                    folium.PolyLine(  # type: ignore[no-untyped-call]
                        [(reference.latitude_deg, reference.longitude_deg), abeam],
                        color="#8e44ad",
                        weight=2,
                        dash_array="4 4",
                        tooltip=f"{escape(reference.name)} CP abeam",
                    ).add_to(map_widget)
                    folium.CircleMarker(
                        abeam,
                        radius=5,
                        color="#8e44ad",
                        fill=True,
                        fill_color="#ffffff",
                        fill_opacity=1.0,
                        tooltip=(
                            f"CP abeam: {escape(reference.name)} / "
                            f"XTK {projection.cross_track_distance_nm:.2f} NM"
                        ),
                    ).add_to(map_widget)
        if self.outcome is not None:
            for point in self.outcome.derived_points:
                color = "#148f77" if point.type == DerivedPointType.RCA else "#c0392b"
                folium.CircleMarker(
                    (point.latitude_deg, point.longitude_deg),
                    radius=7,
                    color=color,
                    fill=True,
                    fill_color=color,
                    fill_opacity=0.9,
                    tooltip=(f"{point.type.value} / {point.along_route_distance_nm:.2f} NM"),
                ).add_to(map_widget)
        if len(coordinates) >= 2:
            map_widget.fit_bounds(coordinates)
        self.map_view.value = map_widget._repr_html_()

    def _ensure_pasted_import(self) -> KmlImportResult:
        pasted = self.kml_text.value
        if pasted.strip() and (self.import_result is None or pasted != self._imported_kml_text):
            self._accept_import_result(import_kml_text(pasted))
            self._imported_kml_text = pasted
        if self.import_result is None:
            raise ValueError(
                "KML/XMLを貼り付けてください。ファイル取込済みの場合は通常の計算実行も"
                "利用できます。"
            )
        return self.import_result

    def _route_entries_from_import(
        self,
        result: KmlImportResult,
    ) -> tuple[list[RouteEntry], str]:
        selected = self.shape_candidates.value
        if len(result.lines) == 1:
            if selected != ("line", 0):
                confirmed = self.quick_run_confirmation.value
                self.shape_candidates.value = ("line", 0)
                self.quick_run_confirmation.value = confirmed
            line = select_imported_line(result, 0)
            return (
                [
                    (
                        f"{line.name} {index + 1:02d}",
                        latitude,
                        longitude,
                        "KML/KMZ LineString",
                    )
                    for index, (latitude, longitude) in enumerate(line.coordinates)
                ],
                f"単一LineString「{line.name}」をKML記載順でRoute候補へ適用",
            )
        if len(result.lines) > 1:
            if isinstance(selected, tuple) and len(selected) == 2 and selected[0] == "line":
                line_index = selected[1]
                line = select_imported_line(result, line_index)
                return (
                    [
                        (
                            f"{line.name} {index + 1:02d}",
                            latitude,
                            longitude,
                            "KML/KMZ LineString",
                        )
                        for index, (latitude, longitude) in enumerate(line.coordinates)
                    ],
                    f"選択済みLineString「{line.name}」をKML記載順でRoute候補へ適用",
                )
            raise ValueError(
                "LineStringが複数あります。地図で飛行経路にする形状を1件選択してから"
                "もう一度実行してください。"
            )
        if result.points:
            if self.reference_data_repository is not None:
                raise ValueError(
                    "Point Placemarkは自動で経路化しません。Pointを選び、"
                    "経路点・VREP・CP・参照のみの役割を明示してください。"
                )
            return (
                [
                    (
                        point.name,
                        point.latitude_deg,
                        point.longitude_deg,
                        "KML/KMZ Point",
                    )
                    for point in result.points
                ],
                f"{len(result.points)}件のPointをKML記載順でRoute候補へ適用",
            )
        if result.polygons:
            polygon_index: int | None = None
            if isinstance(selected, tuple) and len(selected) == 2 and selected[0] == "polygon":
                polygon_index = selected[1]
            elif len(result.polygons) == 1:
                polygon_index = 0
                if selected != ("polygon", 0):
                    confirmed = self.quick_run_confirmation.value
                    self.shape_candidates.value = ("polygon", 0)
                    self.quick_run_confirmation.value = confirmed
            if polygon_index is None:
                raise ValueError(
                    "Polygonが複数あります。開始点・進行方向は自動判定できません。"
                    "地図で形状を1件選択してください。"
                )
            if not self.polygon_route_confirmation.value:
                self._require_quick_run_confirmation(polygon=True)
            polygon = result.polygons[polygon_index]
            outer_boundary = select_imported_polygon_outer(result, polygon_index)
            return (
                [
                    (
                        f"{polygon.name} {index + 1:02d}",
                        latitude,
                        longitude,
                        "KML/KMZ Polygon (confirmed)",
                    )
                    for index, (latitude, longitude) in enumerate(outer_boundary)
                ],
                f"確認済みPolygon「{polygon.name}」をKML記載順でRoute候補へ適用",
            )
        raise ValueError("Route化できるPoint、LineString、PolygonがKMLにありません。")

    def _require_quick_run_confirmation(self, *, polygon: bool = False) -> None:
        if self.quick_run_confirmation.value:
            return
        details = (
            f"ETD JST={self.departure_time.value.strip() or '未入力'}、"
            f"新規Leg計画高度={self.all_leg_altitude.value:g} ft"
        )
        polygon_note = (
            "、およびPolygon境界の開始点・進行方向をKML記載順でRouteに使うこと" if polygon else ""
        )
        raise ValueError(
            f"{details}{polygon_note}は未確認です。地図と入力値を確認し、"
            "確認欄を選択してからもう一度実行してください。"
        )

    def _seed_active_reference_airports(
        self,
        route_coordinates: list[tuple[float, float]],
    ) -> tuple[Airport, Airport, str | None]:
        if self.reference_catalog is None:
            raise ValueError("AIRPORT_DATA_UNAVAILABLE: active参照データを読み込めません。")
        selections = list(self.reference_catalog.airports.values())
        if not selections:
            raise ValueError("AIRPORT_DATA_UNAVAILABLE: active packに空港行がありません。")
        by_id = {item.id: item for item in selections}
        by_icao = {item.icao.upper(): item for item in selections}

        def resolve(identifier: object, label: str) -> AirportSelection | None:
            value = "" if identifier is None else str(identifier).strip()
            if not value:
                return None
            selection = by_id.get(value) or by_icao.get(value.upper())
            if selection is None:
                raise ValueError(f"{label}={value!r}はactive参照データにありません。")
            if (
                label == "TO"
                and selection.pattern_altitude_validation_status
                != PatternAltitudeValidationStatus.VERIFIED
            ):
                raise ValueError(
                    "PATTERN_ALTITUDE_REQUIRED: 目的空港の実運用場周経路高度を"
                    "一次資料で検証してください。"
                )
            return selection

        departure = resolve(
            self.departure_reference.value or self.departure.value,
            "FROM",
        )
        destination = resolve(
            self.destination_reference.value or self.destination.value,
            "TO",
        )
        notes: list[str] = []

        def nearest(
            coordinate: tuple[float, float],
            label: str,
        ) -> AirportSelection:
            ranked = sorted(
                (
                    (
                        geodesic_leg(
                            coordinate[0],
                            coordinate[1],
                            item.latitude_deg,
                            item.longitude_deg,
                        ).distance_nm,
                        item,
                    )
                    for item in selections
                ),
                key=lambda candidate: (
                    candidate[0],
                    candidate[1].icao,
                    candidate[1].id,
                ),
            )
            distance_nm, candidate = ranked[0]
            if label == "TO" and (
                candidate.pattern_altitude_validation_status
                != PatternAltitudeValidationStatus.VERIFIED
            ):
                raise ValueError(
                    f"PATTERN_ALTITUDE_REQUIRED: 最寄り候補 {candidate.icao} "
                    f"({distance_nm:.2f} NM) の場周経路高度が未検証です。"
                )
            if distance_nm <= 1.0:
                notes.append(f"{label}={candidate.icao}を端点から{distance_nm:.2f} NMで自動確定")
                return candidate
            if distance_nm <= 5.0:
                raise ValueError(
                    f"{label}候補は{candidate.icao}（端点から{distance_nm:.2f} NM）です。"
                    "参照データDropdownで採用する空港を選択してください。"
                )
            raise ValueError(
                f"{label}端点の5 NM以内に空港がありません。active参照データから"
                "空港を選択するか、下書きの経路点として保存してください。"
            )

        if departure is None:
            if not route_coordinates:
                raise ValueError("FROMをactive参照データから選択してください。")
            departure = nearest(route_coordinates[0], "FROM")
        if destination is None:
            if not route_coordinates:
                raise ValueError("TOをactive参照データから選択してください。")
            destination = nearest(route_coordinates[-1], "TO")
        self.departure_reference.value = departure.id
        self.destination_reference.value = destination.id
        self.departure.value = departure.id
        self.destination.value = destination.id
        return (
            self._airport_from_selection(departure),
            self._airport_from_selection(destination),
            " / ".join(notes) or None,
        )

    def _seed_airport_inputs(
        self,
        route_coordinates: list[tuple[float, float]],
    ) -> tuple[Airport, Airport, str | None]:
        if self.reference_data_repository is not None:
            return self._seed_active_reference_airports(route_coordinates)
        airports = self.calculation_service.airports.all()
        if not airports:
            raise ValueError(
                "空港マスターが空のためFROM/TOを決定できません。"
                "airports.csvへ利用空港を登録してください。"
            )
        by_id = {airport.id: airport for airport in airports}
        by_icao = {airport.icao: airport for airport in airports}
        if self.project is not None:
            if not self.departure.value.strip():
                self.departure.value = self.project.departure_airport_id
            if not self.destination.value.strip():
                self.destination.value = self.project.destination_airport_id

        def entered_airport(value: str, label: str) -> Airport | None:
            identifier = value.strip()
            if not identifier:
                return None
            airport = by_id.get(identifier) or by_icao.get(identifier.upper())
            if airport is None:
                choices = ", ".join(airport.icao for airport in airports)
                raise ValueError(
                    f"{label}={identifier!r}は空港マスターにありません。候補: {choices}"
                )
            return airport

        departure = entered_airport(self.departure.value, "FROM")
        destination = entered_airport(self.destination.value, "TO")
        seeded = departure is None or destination is None

        def distance(
            coordinate: tuple[float, float],
            airport: Airport,
        ) -> float:
            return geodesic_leg(
                coordinate[0],
                coordinate[1],
                airport.latitude_deg,
                airport.longitude_deg,
            ).distance_nm

        if departure is None and destination is None and len(airports) == 2:
            first, second = airports
            if len(route_coordinates) >= 2:
                direct = distance(route_coordinates[0], first) + distance(
                    route_coordinates[-1], second
                )
                reverse = distance(route_coordinates[0], second) + distance(
                    route_coordinates[-1], first
                )
                departure, destination = (first, second) if direct <= reverse else (second, first)
            else:
                departure, destination = first, second
        elif len(airports) == 1:
            departure = departure or airports[0]
            destination = destination or airports[0]
        else:
            if departure is None and route_coordinates:
                departure = min(
                    airports,
                    key=lambda airport: distance(route_coordinates[0], airport),
                )
            if destination is None and route_coordinates:
                destination = min(
                    airports,
                    key=lambda airport: distance(route_coordinates[-1], airport),
                )
            if len(airports) == 2:
                if departure is None and destination is not None:
                    departure = next(
                        (airport for airport in airports if airport.id != destination.id),
                        destination,
                    )
                if destination is None and departure is not None:
                    destination = next(
                        (airport for airport in airports if airport.id != departure.id),
                        departure,
                    )
        if departure is None or destination is None:
            choices = ", ".join(f"{airport.icao} ({airport.name})" for airport in airports)
            raise ValueError(
                "FROM/TOを自動決定できません。人の判断が必要です。"
                f"FROMとTO欄へ入力してください。空港マスター候補: {choices}"
            )
        self.departure.value = departure.id
        self.destination.value = destination.id
        note = (
            f"空港マスターからFROM={departure.icao}、TO={destination.icao}を自動入力"
            if seeded
            else None
        )
        return departure, destination, note

    def _install_route_from_import(
        self,
        departure: Airport,
        destination: Airport,
        entries: list[RouteEntry],
    ) -> int:
        if self.project is None:
            raise ValueError("Projectがありません。")
        if self.project.route_nodes:
            return 0
        filtered: list[RouteEntry] = []
        for entry in entries:
            coordinate = (entry[1], entry[2])
            if not filtered or coordinate != (filtered[-1][1], filtered[-1][2]):
                filtered.append(entry)
        if self.reference_data_repository is not None:
            if len(filtered) < 2:
                raise ValueError("選択形状には2点以上の経路座標が必要です。")
            self.project.route_nodes = [
                RouteNode(
                    project_id=self.project.id,
                    sequence=index,
                    name=f"WP{index + 1}",
                    latitude_deg=latitude,
                    longitude_deg=longitude,
                    role=(
                        RouteNodeRole.AIRPORT
                        if index == 0
                        else (
                            RouteNodeRole.DESTINATION
                            if index == len(filtered) - 1
                            else RouteNodeRole.ROUTE_POINT
                        )
                    ),
                    source=source,
                )
                for index, (_name, latitude, longitude, source) in enumerate(filtered)
            ]
            self._rebuild_sections()
            self._initialize_imported_nav2_phases(destination)
            self._refresh_route()
            return len(self.project.route_nodes)
        departure_coordinate = (
            departure.latitude_deg,
            departure.longitude_deg,
        )
        destination_coordinate = (
            destination.latitude_deg,
            destination.longitude_deg,
        )
        if filtered and (filtered[0][1], filtered[0][2]) == departure_coordinate:
            filtered.pop(0)
        if filtered and (filtered[-1][1], filtered[-1][2]) == destination_coordinate:
            filtered.pop()
        route_specs: list[tuple[str, float, float, RouteNodeRole, str]] = [
            (
                departure.icao,
                departure.latitude_deg,
                departure.longitude_deg,
                RouteNodeRole.AIRPORT,
                f"AIRPORT_MASTER:{departure.source_revision}",
            ),
            *[
                (
                    name,
                    latitude,
                    longitude,
                    RouteNodeRole.ROUTE_POINT,
                    source,
                )
                for name, latitude, longitude, source in filtered
            ],
            (
                destination.icao,
                destination.latitude_deg,
                destination.longitude_deg,
                RouteNodeRole.DESTINATION,
                f"AIRPORT_MASTER:{destination.source_revision}",
            ),
        ]
        self.project.route_nodes = [
            RouteNode(
                project_id=self.project.id,
                sequence=index,
                name=name,
                latitude_deg=latitude,
                longitude_deg=longitude,
                role=role,
                source=source,
            )
            for index, (name, latitude, longitude, role, source) in enumerate(route_specs)
        ]
        self._rebuild_sections()
        self._initialize_imported_nav2_phases(destination)
        self._refresh_route()
        return len(self.project.route_nodes)

    def _initialize_imported_nav2_phases(self, destination: Airport) -> None:
        if self.project is None or not self.project.sections:
            return
        section_count = len(self.project.sections)
        phases = [FlightPhase.CRUISE] * section_count
        if section_count >= 4:
            phases[0] = FlightPhase.CLIMB
            phases[-2] = FlightPhase.DESCENT
            phases[-1] = FlightPhase.VISUAL_ARRIVAL
        elif section_count == 3:
            phases = [
                FlightPhase.CLIMB,
                FlightPhase.DESCENT,
                FlightPhase.VISUAL_ARRIVAL,
            ]
        elif section_count == 2:
            phases = [FlightPhase.CLIMB, FlightPhase.VISUAL_ARRIVAL]
        else:
            phases = [FlightPhase.VISUAL_ARRIVAL]
        visual_altitude = (
            destination.pattern_altitude_ft_msl
            if destination.pattern_altitude_ft_msl is not None
            else destination.elevation_ft_msl
        )
        self.project.sections = [
            section.model_copy(
                update={
                    "phase": phase,
                    "planned_altitude_ft_msl": (
                        visual_altitude
                        if phase == FlightPhase.VISUAL_ARRIVAL
                        else section.planned_altitude_ft_msl
                    ),
                }
            )
            for section, phase in zip(self.project.sections, phases, strict=True)
        ]
        self.project.metadata["auto_phase_assignment"] = {
            "method": "IMPORTED_NAV2_POSITIONAL_V1",
            "section_count": section_count,
            "phases": [phase.value for phase in phases],
            "visual_arrival_altitude_ft_msl": visual_altitude,
            "review_required": True,
        }

    def _sync_project_inputs(self) -> None:
        if self.project is None:
            raise ValueError("Projectがありません。")
        departure_time = self._parse_departure_datetime()
        self.project.pilot_name = self.pilot.value.strip()
        self.project.ship_identifier = self.ship.value.strip()
        self.project.flight_date = departure_time.date()
        self.project.planned_departure_time_jst = departure_time
        self.project.total_usable_fuel_gal = self.fuel.value
        self.project.default_variation_deg_east = self.variation.value
        self.project.manual_qnh_hpa = self._optional_float(self.manual_qnh.value)
        self.project.tgl_count = self.tgl_count.value

    def _confirm_quick_readiness_inputs(
        self,
        departure: Airport,
        destination: Airport,
    ) -> None:
        if self.project is None:
            raise ValueError("Projectがありません。")
        ordered = self.project.ordered_nodes()
        if len(ordered) < 3:
            raise ValueError(
                "目的空港直前のVREP候補がありません。経路点を1点以上追加してください。"
            )
        vrep = ordered[-2]
        for node in self.project.route_nodes:
            if node.role == RouteNodeRole.VISUAL_REPORTING_POINT:
                node.role = RouteNodeRole.ROUTE_POINT
        vrep.role = RouteNodeRole.VISUAL_REPORTING_POINT
        ordered[-1].role = RouteNodeRole.DESTINATION
        if self.project.sections:
            self.project.sections[-1].phase = FlightPhase.VISUAL_ARRIVAL
        if len(self.project.sections) >= 3:
            self.project.sections[-2].phase = FlightPhase.DESCENT
        self._store_reference_snapshot(departure.id, destination.id)
        state = self.readiness_service.ui_state(self.project) or PersistedUiState()
        self.project_service.set_ui_state(
            self.project,
            state.model_copy(
                update={
                    "arrival_plan": ArrivalPlan(
                        visual_reporting_point_node_id=vrep.id,
                        selected_pattern_altitude_ft_msl=int(
                            self.destination_pattern_altitude.value
                        ),
                        selected_pattern_altitude_source=_pattern_altitude_source(
                            int(self.destination_pattern_altitude.value),
                            destination.pattern_altitude_ft_msl,
                        ),
                    )
                }
            ),
            reconfirmed=True,
        )
        self._sync_project_inputs()
        if self.project.manual_qnh_hpa is not None:
            self.readiness_service.confirm_manual_qnh(self.project, self.outcome)
        self._refresh_route()

    def _calculate_from_pasted_kml(self, _: Any) -> None:
        steps: list[str] = []
        try:
            result = self._ensure_pasted_import()
            if self._kml_altitude_note is not None:
                steps.append(f"KML高度由来: {self._kml_altitude_note}")
            entries: list[RouteEntry] | None = None
            route_coordinates: list[tuple[float, float]]
            if self.project is None or not self.project.route_nodes:
                entries, route_decision = self._route_entries_from_import(result)
                route_coordinates = [(latitude, longitude) for _, latitude, longitude, _ in entries]
                steps.append(route_decision)
            else:
                route_coordinates = [
                    (node.latitude_deg, node.longitude_deg) for node in self.project.ordered_nodes()
                ]
                steps.append("既存RouteとLeg入力を保護し、貼付KMLでは自動置換していません")
            self._require_quick_run_confirmation()
            departure, destination, airport_note = self._seed_airport_inputs(route_coordinates)
            if airport_note:
                steps.append(airport_note)
            current_state = (
                None if self.project is None else self.readiness_service.ui_state(self.project)
            )
            if (
                current_state is None
                or current_state.arrival_plan is None
                or current_state.arrival_plan.selected_pattern_altitude_ft_msl is None
            ):
                self._set_destination_pattern_altitude(destination.pattern_altitude_ft_msl)
            if self.project is None:
                self._create_project_from_inputs()
                steps.append("入力欄からProjectを作成")
            if self.project is None:
                raise ValueError("Projectを作成できませんでした。")
            self.project.departure_airport_id = departure.id
            self.project.destination_airport_id = destination.id
            if not self.project.route_nodes:
                if entries is None:
                    raise ValueError("Route候補がありません。")
                if self.all_leg_altitude.value <= 0:
                    raise ValueError("全Leg ALT ftは0より大きい値を入力してください。")
                node_count = self._install_route_from_import(
                    departure,
                    destination,
                    entries,
                )
                steps.append(f"空港を含む{node_count}点と{len(self.project.sections)} Legを作成")
                steps.append(f"新規Legへ計画高度{self.all_leg_altitude.value:g} ftを適用")
                steps.append(
                    "Phase初期案を位置規則で設定: "
                    + " → ".join(section.phase.value for section in self.project.sections)
                    + "（飛行前に確認してください）"
                )
            self._confirm_quick_readiness_inputs(departure, destination)
            outcome = self._calculate_project()
            steps.append(f"NAV LOG計算完了: {outcome.status.value}")
            self._set_quick_run_status(steps)
            self._notify(f"計算状態: {outcome.status.value}")
        except Exception as error:
            steps.append(str(error))
            self._set_quick_run_status(steps, error=True)
            self._notify(str(error), error=True)

    def _set_quick_run_status(
        self,
        messages: list[str],
        *,
        error: bool = False,
    ) -> None:
        color = "#922b21" if error else "#1e8449"
        title = "確認・入力が必要" if error else "ワンクリック処理結果"
        items = "".join(f"<li>{escape(message)}</li>" for message in messages)
        self.quick_run_status.value = (
            f"<div style='color:{color}'><strong>{title}</strong><ol>{items}</ol></div>"
        )

    def _calculate(self, _: Any) -> None:
        try:
            outcome = self._calculate_project()
            self._notify(f"計算状態: {outcome.status.value}")
        except Exception as error:
            self._notify(str(error), error=True)

    def _calculate_project(self) -> CalculationOutcome:
        if self.view_mode != ViewMode.EDITABLE:
            raise ValueError("Snapshot読取専用では再計算できません。")
        if self.project is None:
            raise ValueError("Projectがありません。")
        self._sync_project_inputs()
        raw_outcome = self.calculation_service.calculate(
            self.project,
            self.weather_provider,
        )
        materialized = self.readiness_service.record_calculation(
            self.project,
            raw_outcome,
        )
        self.project = materialized.project
        self.outcome = materialized.outcome
        self.readiness_evaluation = materialized.evaluation
        if self.outcome is None:
            raise RuntimeError("計算結果を確定できませんでした。")
        initial_time = self.calculation_service.last_forecast_metadata.get("initial_time_utc")
        self._forecast_initial_time_utc = None if initial_time is None else str(initial_time)
        self._refresh_map()
        self._refresh_readiness()
        return self.outcome

    def _render_issues(self) -> None:
        if self.readiness_evaluation is None:
            self.issues.children = ()
            return
        children: list[widgets.Widget] = []
        for effective in self.readiness_evaluation.effective_issues:
            issue = effective.issue
            location = (
                "" if issue.segment_sequence is None else f" [Leg/Seg {issue.segment_sequence + 1}]"
            )
            code = effective.ctx.code
            action = ISSUE_ACTIONS.get(
                code,
                "入力・原資料・表示値を確認し、必要な修正後に再計算してください。",
            )
            row: list[widgets.Widget] = [
                widgets.HTML(
                    "<div class='issue-row'>"
                    f"<strong>{escape(effective.effective_severity.value)} "
                    f"{escape(code + location)}</strong>: "
                    f"{escape(issue.message)}"
                    f"<br><span class='issue-action'>次の操作: {escape(action)}</span>"
                    "</div>"
                )
            ]
            if effective.effective_acknowledgement_required:
                checkbox = widgets.Checkbox(
                    description="このWarningを確認済みにする",
                    value=effective.ctx.ack_key
                    in (self.project.acknowledged_warning_codes if self.project else set()),
                    indent=False,
                    disabled=self.view_mode != ViewMode.EDITABLE,
                )
                checkbox.observe(
                    lambda change, key=effective.ctx.ack_key: self._acknowledge(key, change["new"]),
                    names="value",
                )
                row.append(checkbox)
            if code == "FORECAST_UPDATE_AVAILABLE":
                latest = issue.metadata.get("latest_compatible_run_id")
                if latest:
                    button = widgets.Button(
                        description=f"最新Run {latest}へ切替",
                        icon="refresh",
                        disabled=self.view_mode != ViewMode.EDITABLE,
                    )
                    button.on_click(lambda _, run_id=str(latest): self._switch_forecast_run(run_id))
                    row.append(button)
            children.append(widgets.VBox(row))
        self.issues.children = tuple(children)

    def _switch_forecast_run(self, run_id: str) -> None:
        if self.project is None:
            self._notify("Projectを先に開いてください。", error=True)
            return
        if self.view_mode != ViewMode.EDITABLE:
            self._notify("Snapshot読取専用ではRunを変更できません。", error=True)
            return
        self.project.selected_forecast_run_id = run_id
        self._forecast_initial_time_utc = None
        self._refresh_readiness()
        self._notify(f"Forecast Runを{run_id}へ切り替えました。再計算してください。")

    def write_transfer_aid_html(self) -> Path:
        if self.view_mode != ViewMode.EDITABLE:
            raise ValueError("Snapshot読取専用では転記補助HTMLを出力できません。")
        if self.project is None or self.outcome is None:
            raise ValueError("先にNAV LOG計算を実行してください。")
        self._sync_project_inputs()
        self._refresh_readiness()
        if self.readiness_evaluation is None or not self.readiness_evaluation.transfer_aid_allowed:
            raise ValueError("転記補助HTMLはBlocker解消・必要警告承認・再計算後に出力できます。")
        output_directory = self.download_directory
        if output_directory is None:
            content_directory = Path("/content")
            output_directory = content_directory if content_directory.is_dir() else Path.cwd()
        output_directory.mkdir(parents=True, exist_ok=True)
        destination = output_directory / f"AutoNavLog_transfer_aid_{self.project.id}.html"
        destination.write_text(
            render_transfer_aid_document(
                self.project,
                self.outcome,
                effective_issues=self.readiness_evaluation.effective_issues,
                calculation_is_current=(self.readiness_evaluation.calculation_is_current),
                editable=True,
            ),
            encoding="utf-8",
        )
        return destination

    def _download_transfer_aid(self, _: Any) -> None:
        try:
            destination = self.write_transfer_aid_html()
            try:
                from google.colab import files  # type: ignore[import-not-found]
            except ImportError:
                self.download_transfer_aid_status.value = (
                    "<strong>A4印刷用HTMLを保存しました:</strong> " + escape(str(destination))
                )
            else:
                files.download(str(destination))
                self.download_transfer_aid_status.value = (
                    "<strong>ダウンロードを開始しました:</strong> " + escape(destination.name)
                )
        except Exception as error:
            self.download_transfer_aid_status.value = (
                f"<strong style='color:#922b21'>{escape(str(error))}</strong>"
            )

    def _acknowledge(self, ack_key: str, checked: bool) -> None:
        if self.project is None:
            return
        if self.view_mode != ViewMode.EDITABLE:
            self._notify(
                "Snapshot読取専用では警告承認を変更できません。",
                error=True,
            )
            return
        if checked:
            self.project.acknowledged_warning_codes.add(ack_key)
        else:
            self.project.acknowledged_warning_codes.discard(ack_key)
        self._refresh_readiness()

    def _save(self, _: Any) -> None:
        if self.project is None:
            return
        if self.view_mode != ViewMode.EDITABLE:
            self._notify("Snapshot読取専用では保存できません。", error=True)
            return
        try:
            self._set_storage_state(StorageState.WRITING)
            self.save_button.disabled = True
            self._sync_project_inputs()
            self._refresh_readiness()
            entered_name = self._normalize_project_name(self.name.value)
            generated_name = self.project.metadata.get("project_name_generated")
            if entered_name != generated_name:
                self.project.metadata["project_name_auto"] = False
            entered_name = self._unique_initial_save_name(entered_name)
            if self.project.metadata.get("project_name_auto", False):
                self.project.metadata["project_name_generated"] = entered_name
            self.name.value = entered_name
            self.project.name = entered_name
            saved = self.project_service.save(self.project)
            self.project = saved.project
            self._name_editing = False
            self._refresh_project_list(selected=str(self.project.id))
            self._set_storage_state(StorageState.DRIVE_READY)
            self._update_action_states()
            self._notify(f"保存しました: revision {self.project.revision}")
        except Exception as error:
            self._set_storage_state(StorageState.ERROR, str(error))
            self._update_action_states()
            self._notify(str(error), error=True)

    def _snapshot(self, _: Any) -> None:
        if self.project is None or self.outcome is None:
            self._notify("計算後にSnapshotを作成してください。", error=True)
            return
        if self.view_mode != ViewMode.EDITABLE:
            self._notify(
                "Snapshot読取専用ではSnapshotを再作成できません。",
                error=True,
            )
            return
        try:
            self._set_storage_state(StorageState.WRITING)
            self._sync_project_inputs()
            self._refresh_readiness()
            path = self.project_service.snapshot(
                self.project,
                self.outcome,
                self.calculation_service,
                msm_package_version=getattr(
                    self.weather_provider,
                    "package_version",
                    None,
                ),
                effective_issues=(
                    None
                    if self.readiness_evaluation is None
                    else self.readiness_evaluation.effective_issues
                ),
            )
            self._set_storage_state(StorageState.DRIVE_READY)
            self._notify(f"Snapshotを作成しました: {path.name}")
        except Exception as error:
            self._set_storage_state(StorageState.ERROR, str(error))
            self._notify(str(error), error=True)

    def _notify(self, message: str, *, error: bool = False) -> None:
        color = "#922b21" if error else "#1e8449"
        self.message.value = f"<p style='color:{color}'>{message}</p>"

    @staticmethod
    def _optional_float(value: str) -> float | None:
        stripped = value.strip()
        return None if not stripped else float(stripped)

    @staticmethod
    def _optional_text(value: float | None) -> str:
        return "" if value is None else f"{value:g}"
