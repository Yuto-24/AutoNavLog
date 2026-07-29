from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import folium
import ipywidgets as widgets
from IPython.display import display

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.project_service import ProjectService
from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.enums import FlightPhase, RouteNodeRole
from autonavlog.domain.project import NavSection, Project, RouteNode
from autonavlog.importers.kml import KmlImportResult, import_kml_or_kmz
from autonavlog.presentation.clearcopy import render_clearcopy_html
from autonavlog.weather.provider import WeatherProvider

JST = ZoneInfo("Asia/Tokyo")


class AutoNavLogApp:
    """Thin, one-column Colab UI. All business decisions stay in services."""

    def __init__(
        self,
        project_service: ProjectService,
        calculation_service: CalculationService,
        weather_provider: WeatherProvider,
    ):
        self.project_service = project_service
        self.calculation_service = calculation_service
        self.weather_provider = weather_provider
        self.project: Project | None = None
        self.outcome: CalculationOutcome | None = None
        self.import_result: KmlImportResult | None = None
        self.message = widgets.HTML()
        self.name = widgets.Text(description="Project")
        self.pilot = widgets.Text(description="PILOT")
        self.ship = widgets.Text(description="SHIP")
        self.flight_date = widgets.DatePicker(description="DATE", value=date.today())
        self.departure_time = widgets.Text(description="ETD JST", value="09:00")
        self.departure = widgets.Text(description="FROM")
        self.destination = widgets.Text(description="TO")
        self.fuel = widgets.FloatText(description="FUEL gal", value=81.0)
        self.variation = widgets.FloatText(description="VAR E", value=8.0)
        self.create_button = widgets.Button(description="新規Project", icon="plus")
        self.create_button.on_click(self._create_project)
        self.project_id = widgets.Text(description="Project ID")
        self.load_button = widgets.Button(description="読込", icon="folder-open")
        self.load_button.on_click(self._load_project)
        self.upload = widgets.FileUpload(accept=".kml,.kmz", multiple=False)
        self.upload.observe(self._import_file, names="value")
        self.candidates = widgets.Select(description="候補", rows=6)
        self.map_view = widgets.HTML()
        self.add_candidate = widgets.Button(description="Routeへ追加", icon="plus")
        self.add_candidate.on_click(self._add_imported_point)
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
        self.safe_altitude = widgets.Text(description="SEA ft")
        self.wind_direction = widgets.Text(description="WIND°")
        self.wind_speed = widgets.Text(description="WIND kt")
        self.temperature = widgets.Text(description="TEMP °C")
        self.tas = widgets.Text(description="TAS kt")
        self.loss_time = widgets.FloatText(description="LOSS sec", value=0)
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
        self.issues = widgets.VBox()
        self.clearcopy = widgets.HTML()
        self.save_button = widgets.Button(description="保存", icon="save")
        self.snapshot_button = widgets.Button(description="Snapshot", icon="camera")
        self.save_button.on_click(self._save)
        self.snapshot_button.on_click(self._snapshot)

    def render(self) -> widgets.Widget:
        sections = [
            ("1. 開始・環境確認", widgets.VBox([self.message])),
            (
                "2. Projectの作成・読込",
                widgets.VBox(
                    [
                        self.name,
                        self.pilot,
                        self.ship,
                        self.flight_date,
                        self.departure_time,
                        self.departure,
                        self.destination,
                        self.create_button,
                        self.project_id,
                        self.load_button,
                    ]
                ),
            ),
            (
                "3. コース取込・編集",
                widgets.VBox(
                    [
                        self.upload,
                        self.map_view,
                        self.candidates,
                        self.add_candidate,
                        self.manual_name,
                        self.manual_lat,
                        self.manual_lon,
                        self.add_manual,
                        self.route,
                        widgets.HBox([self.delete_node, self.move_up, self.move_down]),
                        self.phase,
                        self.altitude,
                        self.safe_altitude,
                        self.wind_direction,
                        self.wind_speed,
                        self.temperature,
                        self.tas,
                        self.loss_time,
                        self.apply_phase,
                    ]
                ),
            ),
            (
                "4. 飛行計画入力",
                widgets.VBox(
                    [
                        self.fuel,
                        self.variation,
                        self.manual_qnh,
                        self.tgl_count,
                    ]
                ),
            ),
            ("5. Forecast Run選択", widgets.VBox([self.run_label])),
            ("6. 計算実行", widgets.VBox([self.calculate_button])),
            ("7. 警告・未確定項目の解消", self.issues),
            ("8. 清書ビュー", widgets.VBox([self.clearcopy])),
            ("9. 保存・Snapshot作成", widgets.HBox([self.save_button, self.snapshot_button])),
        ]
        children: list[Any] = []
        for heading, body in sections:
            children.extend((widgets.HTML(f"<h3>{heading}</h3>"), body))
        root = widgets.VBox(children)
        root.add_class("autonavlog-app")
        display(  # type: ignore[no-untyped-call]
            widgets.HTML(
                """
                <style>
                .autonavlog-app {max-width:760px;margin:0 auto;gap:8px}
                .autonavlog-app .widget-label {min-width:92px}
                .autonavlog-app input,.autonavlog-app select {min-height:40px}
                </style>
                """
            )
        )
        return root

    def _create_project(self, _: Any) -> None:
        try:
            if self.flight_date.value is None:
                raise ValueError("DATEを入力してください")
            hour, minute = (int(value) for value in self.departure_time.value.split(":", 1))
            departure_time = datetime.combine(
                self.flight_date.value,
                datetime.min.time(),
                tzinfo=JST,
            ).replace(hour=hour, minute=minute)
            self.project = self.project_service.create(
                name=self.name.value or "NAV2",
                pilot_name=self.pilot.value,
                ship_identifier=self.ship.value,
                flight_date=self.flight_date.value,
                planned_departure_time_jst=departure_time,
                departure_airport_id=self.departure.value,
                destination_airport_id=self.destination.value,
                total_usable_fuel_gal=self.fuel.value,
                default_variation_deg_east=self.variation.value,
            )
            self.project_id.value = str(self.project.id)
            self._notify("Projectを作成しました。")
        except Exception as error:
            self._notify(str(error), error=True)

    def _load_project(self, _: Any) -> None:
        try:
            self.project = self.project_service.repository.load(UUID(self.project_id.value))
            self._refresh_route()
            self._notify("Projectを読み込みました。")
        except Exception as error:
            self._notify(str(error), error=True)

    def _import_file(self, change: dict[str, Any]) -> None:
        try:
            if not change["new"]:
                return
            value = change["new"]
            uploaded = value[0] if isinstance(value, tuple) else next(iter(value.values()))
            raw_content = uploaded["content"]
            content = (
                raw_content.tobytes()
                if hasattr(raw_content, "tobytes")
                else bytes(raw_content)
            )
            uploaded_name = uploaded.get("name") or uploaded.get("metadata", {}).get("name")
            self.import_result = import_kml_or_kmz(
                content,
                filename=uploaded_name or "upload.kml",
            )
            self.candidates.options = [
                (f"{point.name} ({point.latitude_deg:.5f}, {point.longitude_deg:.5f})", index)
                for index, point in enumerate(self.import_result.points)
            ]
            self._notify(
                f"{len(self.import_result.points)}点、"
                f"{len(self.import_result.lines)}参考LineStringを読み込みました。"
            )
            self._refresh_map()
        except Exception as error:
            self._notify(str(error), error=True)

    def _add_imported_point(self, _: Any) -> None:
        if self.import_result is None or self.candidates.value is None:
            return
        point = self.import_result.points[self.candidates.value]
        self._append_node(point.name, point.latitude_deg, point.longitude_deg, "KML/KMZ")

    def _add_manual_point(self, _: Any) -> None:
        self._append_node(
            self.manual_name.value or "Route Point",
            self.manual_lat.value,
            self.manual_lon.value,
            "MANUAL",
        )

    def _append_node(self, name: str, latitude: float, longitude: float, source: str) -> None:
        if self.project is None:
            self._notify("先にProjectを作成してください。", error=True)
            return
        role = RouteNodeRole.AIRPORT if not self.project.route_nodes else RouteNodeRole.ROUTE_POINT
        self.project.route_nodes.append(
            RouteNode(
                project_id=self.project.id,
                sequence=len(self.project.route_nodes),
                name=name,
                latitude_deg=latitude,
                longitude_deg=longitude,
                role=role,
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
        previous_phases = {
            (section.from_node_id, section.to_node_id): section.phase
            for section in self.project.sections
        }
        self.project.sections = [
            NavSection(
                project_id=self.project.id,
                sequence=index,
                from_node_id=start.id,
                to_node_id=end.id,
                phase=previous_phases.get((start.id, end.id), FlightPhase.CRUISE),
                planned_altitude_ft_msl=5000,
            )
            for index, (start, end) in enumerate(
                zip(self.project.route_nodes, self.project.route_nodes[1:], strict=False)
            )
        ]

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
                "safe_enroute_altitude_ft_msl": self._optional_float(
                    self.safe_altitude.value
                ),
                "manual_wind_direction_deg": direction,
                "manual_wind_speed_kt": speed,
                "manual_temperature_c": self._optional_float(self.temperature.value),
                "manual_tas_kt": self._optional_float(self.tas.value),
                "loss_time_seconds": self.loss_time.value,
            },
        )
        self._refresh_route()

    def _refresh_route(self) -> None:
        if self.project is None:
            self.route.options = []
            return
        phases = [section.phase.value for section in self.project.sections]
        self.route.options = [
            (
                f"{index + 1}. {node.name}"
                + (f" → {phases[index]}" if index < len(phases) else ""),
                index,
            )
            for index, node in enumerate(self.project.route_nodes)
        ]
        self._refresh_map()

    def _route_selected(self, change: dict[str, Any]) -> None:
        if self.project is None or change["new"] is None:
            return
        index = change["new"]
        if index >= len(self.project.sections):
            return
        section = self.project.sections[index]
        self.phase.value = section.phase.value
        self.altitude.value = section.planned_altitude_ft_msl
        self.safe_altitude.value = self._optional_text(
            section.safe_enroute_altitude_ft_msl
        )
        self.wind_direction.value = self._optional_text(
            section.manual_wind_direction_deg
        )
        self.wind_speed.value = self._optional_text(section.manual_wind_speed_kt)
        self.temperature.value = self._optional_text(section.manual_temperature_c)
        self.tas.value = self._optional_text(section.manual_tas_kt)
        self.loss_time.value = section.loss_time_seconds

    def _refresh_map(self) -> None:
        coordinates = []
        if self.project is not None:
            coordinates = [
                (node.latitude_deg, node.longitude_deg)
                for node in self.project.ordered_nodes()
            ]
        if not coordinates and self.import_result is not None:
            coordinates = [
                (point.latitude_deg, point.longitude_deg)
                for point in self.import_result.points
            ]
        if not coordinates:
            self.map_view.value = ""
            return
        map_widget = folium.Map(
            location=coordinates[0],
            zoom_start=8,
            control_scale=True,
        )
        if self.import_result is not None:
            for line in self.import_result.lines:
                folium.PolyLine(  # type: ignore[no-untyped-call]
                    line.coordinates,
                    color="#7f8c8d",
                    weight=2,
                    dash_array="5 5",
                    tooltip=line.name,
                ).add_to(map_widget)
        if self.project is not None:
            route = self.project.ordered_nodes()
            for node in route:
                folium.Marker(
                    (node.latitude_deg, node.longitude_deg),
                    tooltip=node.name,
                ).add_to(map_widget)
            if len(route) >= 2:
                folium.PolyLine(  # type: ignore[no-untyped-call]
                    [(node.latitude_deg, node.longitude_deg) for node in route],
                    color="#1f618d",
                    weight=4,
                ).add_to(map_widget)
        self.map_view.value = map_widget._repr_html_()

    def _calculate(self, _: Any) -> None:
        if self.project is None:
            self._notify("Projectがありません。", error=True)
            return
        try:
            self.project.total_usable_fuel_gal = self.fuel.value
            self.project.default_variation_deg_east = self.variation.value
            self.project.manual_qnh_hpa = self._optional_float(self.manual_qnh.value)
            self.project.tgl_count = self.tgl_count.value
            outcome = self.calculation_service.calculate(
                self.project,
                self.weather_provider,
            )
            self.project = self.project_service.apply_calculation_outcome(
                self.project,
                outcome,
            )
            self.outcome = outcome
            self.run_label.value = (
                f"<strong>Forecast Run:</strong> "
                f"{outcome.selected_forecast_run_id or '未確定'}"
            )
            self._render_issues()
            self.clearcopy.value = render_clearcopy_html(self.project, outcome)
            self._notify(f"計算状態: {outcome.status.value}")
        except Exception as error:
            self._notify(str(error), error=True)

    def _render_issues(self) -> None:
        if self.outcome is None:
            self.issues.children = ()
            return
        children: list[widgets.Widget] = []
        for issue in self.outcome.issues:
            if issue.acknowledgement_required:
                checkbox = widgets.Checkbox(
                    description=f"{issue.code}: {issue.message}",
                    value=issue.code
                    in (
                        self.project.acknowledged_warning_codes
                        if self.project
                        else set()
                    ),
                    indent=False,
                )
                checkbox.observe(
                    lambda change, code=issue.code: self._acknowledge(code, change["new"]),
                    names="value",
                )
                children.append(checkbox)
            else:
                children.append(
                    widgets.HTML(
                        f"<strong>{issue.severity.value} {issue.code}</strong>: {issue.message}"
                    )
                )
        self.issues.children = tuple(children)

    def _acknowledge(self, code: str, checked: bool) -> None:
        if self.project is None:
            return
        if checked:
            self.project.acknowledged_warning_codes.add(code)
        else:
            self.project.acknowledged_warning_codes.discard(code)

    def _save(self, _: Any) -> None:
        if self.project is None:
            return
        try:
            saved = self.project_service.save(self.project)
            self.project = saved.project
            self._notify(f"保存しました: revision {self.project.revision}")
        except Exception as error:
            self._notify(str(error), error=True)

    def _snapshot(self, _: Any) -> None:
        if self.project is None or self.outcome is None:
            self._notify("計算後にSnapshotを作成してください。", error=True)
            return
        try:
            path = self.project_service.snapshot(
                self.project,
                self.outcome,
                self.calculation_service,
                msm_package_version=getattr(self.weather_provider, "package_version", None),
            )
            self._notify(f"Snapshotを作成しました: {path.name}")
        except Exception as error:
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
