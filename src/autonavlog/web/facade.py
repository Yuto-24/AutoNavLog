from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from secrets import token_urlsafe
from threading import RLock
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from autonavlog.application.arrival import standard_vrep_altitude_ft_msl
from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.project_service import ProjectService
from autonavlog.application.readiness import ReadinessEvaluation
from autonavlog.application.readiness_service import ReadinessService
from autonavlog.domain.calculation import CalculationOutcome, Issue
from autonavlog.domain.enums import (
    FlightPhase,
    IssueSeverity,
    RouteNodeRole,
)
from autonavlog.domain.planning import (
    ArrivalPlan,
    PersistedUiState,
    ReferenceDataSnapshot,
)
from autonavlog.domain.project import NavSection, Project, RouteNode
from autonavlog.importers.kml import (
    KmlImportResult,
    imported_line_length_nm,
    select_imported_line,
    select_imported_polygon_outer,
)
from autonavlog.nav.geodesy import geodesic_leg
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.presentation.transfer_aid import render_transfer_aid_document
from autonavlog.storage.airports import AirportRepository
from autonavlog.storage.reference_data import (
    ReferenceCatalog,
    ReferenceDataCatalogRepository,
)
from autonavlog.weather.provider import WeatherProvider

from .models import (
    ConfirmRouteRequest,
    SaveProjectRequest,
    UpdateProjectRequest,
)

JST = ZoneInfo("Asia/Tokyo")
RouteEntry = tuple[str, float, float, str]


ISSUE_ACTIONS: dict[str, str] = {
    "AIRPORT_DATA_UNAVAILABLE": "参照データとFROM/TOを確認してください。",
    "PATTERN_ALTITUDE_REQUIRED": (
        "一次資料で場周高度と出典を確認し、VERIFIED参照パックへ差し替えてください。"
    ),
    "PERFORMANCE_DATA_UNAVAILABLE": "検証済み性能データを読み込んでください。",
    "PERFORMANCE_DATA_UNVERIFIED": "性能データの版とSHA-256を確認してください。",
    "ROUTE_INCOMPLETE": "KML/KMZから2点以上の経路を確定してください。",
    "RECALCULATION_REQUIRED": "現在の入力でNAV LOGを再計算してください。",
    "DEFAULTS_NOT_REVIEWED": "ALT・Phase・FUEL・VAR・TGLを確認してください。",
    "MANUAL_QNH_RECONFIRM_REQUIRED": "DATE・ETD・FROMに対するQNHを再確認してください。",
    "VISUAL_REPORTING_POINT_REQUIRED": "目的空港直前のVREPを選択してください。",
    "VISUAL_REPORTING_POINT_ROUTE_INVALID": "VREPの位置と到着順序を確認してください。",
    "ARRIVAL_ALTITUDE_OVERRIDE_REASON_REQUIRED": "変則Entryの高度と理由を入力してください。",
    "FORECAST_PREPARE_FAILED": "通信とForecast Runを確認して再計算してください。",
    "FORECAST_RUN_OUT_OF_COVERAGE": "互換Forecast Runへ切り替えてください。",
    "WEATHER_QUERY_FAILED": "通信と気象providerを確認して再計算してください。",
    "WIND_UNAVAILABLE": "風を取得するか、風向・風速を手入力してください。",
    "TEMPERATURE_UNAVAILABLE": "気温を取得するか手入力してください。",
    "QNH_UNAVAILABLE": "観測QNHを取得するか、確認済みQNHを手入力してください。",
    "PILOT_REQUIRED": "PILOTを入力してください。",
    "SHIP_REQUIRED": "SHIPを入力してください。",
    "DEVELOPMENT_WEATHER_PROVIDER": "実気象providerで再計算してください。",
}


class WebApplicationError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass
class WebSession:
    token: str
    calculation_service: CalculationService
    weather_provider: WeatherProvider
    readiness_service: ReadinessService
    import_result: KmlImportResult | None = None
    import_filename: str | None = None
    project: Project | None = None
    outcome: CalculationOutcome | None = None
    readiness: ReadinessEvaluation | None = None


class AutoNavLogWebApplication:
    def __init__(
        self,
        *,
        project_service: ProjectService,
        airports: AirportRepository,
        performance: PerformanceRepository,
        reference_repository: ReferenceDataCatalogRepository,
        reference_catalog: ReferenceCatalog,
        weather_factory: Callable[[], WeatherProvider],
        weather_label: str,
        development_weather: bool,
        maximum_sessions: int = 128,
    ) -> None:
        if maximum_sessions < 1:
            raise ValueError("maximum_sessions must be positive")
        self.project_service = project_service
        self.airports = airports
        self.performance = performance
        self.reference_repository = reference_repository
        self.reference_catalog = reference_catalog
        self.weather_factory = weather_factory
        self.weather_label = weather_label
        self.development_weather = development_weather
        self.maximum_sessions = maximum_sessions
        self._sessions: dict[str, WebSession] = {}
        self._session_order: list[str] = []
        self._lock = RLock()

    def create_session(self) -> WebSession:
        with self._lock:
            while len(self._session_order) >= self.maximum_sessions:
                expired = self._session_order.pop(0)
                self._sessions.pop(expired, None)
            token = token_urlsafe(32)
            weather = self.weather_factory()
            calculation = CalculationService(self.airports, self.performance)
            session = WebSession(
                token=token,
                calculation_service=calculation,
                weather_provider=weather,
                readiness_service=ReadinessService(
                    calculation,
                    msm_package_version=getattr(weather, "package_version", None),
                ),
            )
            self._sessions[token] = session
            self._session_order.append(token)
            return session

    def session(self, token: str) -> WebSession:
        with self._lock:
            try:
                session = self._sessions[token]
            except KeyError as error:
                raise WebApplicationError(
                    "SESSION_NOT_FOUND",
                    "セッションの有効期限が切れました。画面を再読み込みしてください。",
                    status_code=401,
                ) from error
            self._session_order.remove(token)
            self._session_order.append(token)
            return session

    def accept_import(
        self,
        session: WebSession,
        *,
        result: KmlImportResult,
        filename: str,
    ) -> dict[str, Any]:
        with self._lock:
            session.import_result = result
            session.import_filename = filename
            session.project = None
            session.outcome = None
            session.readiness = None
            return self.present(session)

    def confirm_route(
        self,
        session: WebSession,
        request: ConfirmRouteRequest,
    ) -> dict[str, Any]:
        with self._lock:
            if not request.route_use_confirmed:
                raise WebApplicationError(
                    "ROUTE_CONFIRMATION_REQUIRED",
                    "地図とKML記載順を確認してから経路を確定してください。",
                )
            result = session.import_result
            if result is None:
                raise WebApplicationError("KML_REQUIRED", "先にKML/KMZを読み込んでください。")
            entries = self._entries_from_candidate(result, request)
            departure, destination = self._selected_airports(
                request.departure_airport_id,
                request.destination_airport_id,
            )
            entries = self._align_route_endpoints(entries, departure.id, destination.id)
            departure_time = self._departure_datetime(
                request.flight_date,
                request.departure_time_jst,
            )
            project = self.project_service.create(
                name=self._unique_project_name(
                    f"{request.flight_date.isoformat()}_{departure.icao}-{destination.icao}"
                ),
                pilot_name=request.pilot_name,
                ship_identifier=request.ship_identifier,
                flight_date=request.flight_date,
                planned_departure_time_jst=departure_time,
                departure_airport_id=departure.id,
                destination_airport_id=destination.id,
                total_usable_fuel_gal=request.total_usable_fuel_gal,
                default_variation_deg_east=request.default_variation_deg_east,
            )
            project.manual_qnh_hpa = request.manual_qnh_hpa
            project.tgl_count = request.tgl_count
            project.metadata.update(
                {
                    "project_name_auto": True,
                    "project_name_generated": project.name,
                    "web_import_filename": session.import_filename,
                }
            )
            self._install_route(
                project,
                entries,
                request.all_leg_altitude_ft_msl,
                destination.id,
                use_penultimate_as_vrep=request.use_penultimate_as_vrep,
            )
            state = PersistedUiState(
                reference_data_snapshot=ReferenceDataSnapshot(
                    departure_airport=departure,
                    destination_airport=destination,
                ),
                arrival_plan=(
                    ArrivalPlan(
                        visual_reporting_point_node_id=project.ordered_nodes()[-2].id,
                    )
                    if request.use_penultimate_as_vrep and len(project.route_nodes) >= 3
                    else None
                ),
            )
            self.project_service.set_ui_state(project, state, reconfirmed=True)
            if request.defaults_confirmed:
                session.readiness_service.confirm_defaults(project, None)
            if request.manual_qnh_hpa is not None and request.manual_qnh_confirmed:
                session.readiness_service.confirm_manual_qnh(project, None)
            materialized = session.readiness_service.evaluate(project, None)
            session.project = materialized.project
            session.outcome = materialized.outcome
            session.readiness = materialized.evaluation
            return self.present(session)

    def update_project(
        self,
        session: WebSession,
        request: UpdateProjectRequest,
    ) -> dict[str, Any]:
        with self._lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
            working = session.project.model_copy(deep=True)
            working.flight_date = request.flight_date
            working.planned_departure_time_jst = self._departure_datetime(
                request.flight_date,
                request.departure_time_jst,
            )
            working.pilot_name = request.pilot_name
            working.ship_identifier = request.ship_identifier
            working.total_usable_fuel_gal = request.total_usable_fuel_gal
            working.default_variation_deg_east = request.default_variation_deg_east
            working.manual_qnh_hpa = request.manual_qnh_hpa
            working.tgl_count = request.tgl_count
            sections = {section.id: section for section in working.sections}
            for update in request.sections:
                try:
                    section = sections[update.section_id]
                except KeyError as error:
                    raise WebApplicationError(
                        "SECTION_NOT_FOUND",
                        "更新対象のLegが現在の経路にありません。",
                    ) from error
                sections[update.section_id] = section.model_copy(
                    update={
                        "planned_altitude_ft_msl": update.planned_altitude_ft_msl,
                        "phase": update.phase,
                        "manual_wind_direction_deg": update.manual_wind_direction_deg,
                        "manual_wind_speed_kt": update.manual_wind_speed_kt,
                        "manual_temperature_c": update.manual_temperature_c,
                        "manual_tas_kt": update.manual_tas_kt,
                    }
                )
            working.sections = [sections[section.id] for section in working.ordered_sections()]
            self._apply_arrival_plan(working, request)
            if request.defaults_confirmed:
                session.readiness_service.confirm_defaults(working, session.outcome)
            if request.manual_qnh_hpa is not None and request.manual_qnh_confirmed:
                session.readiness_service.confirm_manual_qnh(working, session.outcome)
            materialized = session.readiness_service.evaluate(working, session.outcome)
            session.project = materialized.project
            session.outcome = materialized.outcome
            session.readiness = materialized.evaluation
            return self.present(session)

    def calculate(self, session: WebSession) -> dict[str, Any]:
        with self._lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
            outcome = session.calculation_service.calculate(
                session.project,
                session.weather_provider,
            )
            if self.development_weather:
                outcome = outcome.model_copy(
                    deep=True,
                    update={
                        "issues": [
                            *outcome.issues,
                            Issue(
                                code="DEVELOPMENT_WEATHER_PROVIDER",
                                severity=IssueSeverity.BLOCKER,
                                message=(
                                    "開発用固定気象で計算しています。公開用の転記補助HTMLは"
                                    "実気象providerで再計算するまで出力できません。"
                                ),
                            ),
                        ]
                    },
                )
            materialized = session.readiness_service.record_calculation(
                session.project,
                outcome,
            )
            session.project = materialized.project
            session.outcome = materialized.outcome
            session.readiness = materialized.evaluation
            return self.present(session)

    def acknowledge(self, session: WebSession, ack_key: str, checked: bool) -> dict[str, Any]:
        with self._lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "Projectがありません。")
            evaluation = self._evaluate(session)
            allowed = {
                item.ctx.ack_key
                for item in evaluation.effective_issues
                if item.effective_acknowledgement_required
            }
            if ack_key not in allowed:
                raise WebApplicationError(
                    "ACKNOWLEDGEMENT_NOT_FOUND",
                    "確認対象のWarningが現在の計算状態にありません。",
                    status_code=404,
                )
            if checked:
                session.project.acknowledged_warning_codes.add(ack_key)
            else:
                session.project.acknowledged_warning_codes.discard(ack_key)
            self._evaluate(session)
            return self.present(session)

    def save(self, session: WebSession, request: SaveProjectRequest) -> dict[str, Any]:
        with self._lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "保存するProjectがありません。")
            if request.name is not None:
                session.project.name = self._normalize_project_name(request.name)
                session.project.metadata["project_name_auto"] = False
            saved = self.project_service.save(session.project)
            session.project = saved.project
            self._evaluate(session)
            return self.present(session)

    def load(self, session: WebSession, project_id: UUID) -> dict[str, Any]:
        with self._lock:
            project = self.project_service.load(project_id)
            session.project = project
            session.outcome = None
            session.import_result = None
            session.import_filename = None
            self._evaluate(session)
            return self.present(session)

    def create_snapshot(self, session: WebSession) -> str:
        with self._lock:
            if session.project is None or session.outcome is None:
                raise WebApplicationError(
                    "CALCULATION_REQUIRED",
                    "計算後にSnapshotを作成してください。",
                )
            evaluation = self._evaluate(session)
            path = self.project_service.snapshot(
                session.project,
                session.outcome,
                session.calculation_service,
                msm_package_version=getattr(session.weather_provider, "package_version", None),
                effective_issues=evaluation.effective_issues,
            )
            return path.name

    def transfer_aid_html(self, session: WebSession) -> tuple[str, str]:
        with self._lock:
            if session.project is None or session.outcome is None:
                raise WebApplicationError(
                    "CALCULATION_REQUIRED",
                    "先にNAV LOGを計算してください。",
                )
            evaluation = self._evaluate(session)
            if not evaluation.transfer_aid_allowed:
                raise WebApplicationError(
                    "TRANSFER_AID_BLOCKED",
                    "Blocker解消・必要警告承認・再計算後に出力できます。",
                    status_code=409,
                )
            filename = f"AutoNavLog_transfer_aid_{session.project.id}.html"
            return filename, render_transfer_aid_document(
                session.project,
                session.outcome,
                effective_issues=evaluation.effective_issues,
                calculation_is_current=evaluation.calculation_is_current,
                editable=True,
            )

    def present(self, session: WebSession) -> dict[str, Any]:
        evaluation = None if session.project is None else self._evaluate(session)
        issues: list[dict[str, Any]] = []
        displayed_issue_keys: set[tuple[str, UUID | None, int | None]] = set()
        if evaluation is not None and session.project is not None:
            for item in evaluation.effective_issues:
                code = item.ctx.code
                display_key = (
                    code,
                    item.issue.section_id,
                    item.issue.segment_sequence,
                )
                if display_key in displayed_issue_keys:
                    continue
                displayed_issue_keys.add(display_key)
                issues.append(
                    {
                        "code": code,
                        "severity": item.effective_severity.value,
                        "message": item.issue.message,
                        "sectionId": (
                            None if item.issue.section_id is None else str(item.issue.section_id)
                        ),
                        "segmentSequence": item.issue.segment_sequence,
                        "acknowledgementRequired": (
                            item.effective_acknowledgement_required
                        ),
                        "ackKey": item.ctx.ack_key,
                        "acknowledged": (
                            item.ctx.ack_key in session.project.acknowledged_warning_codes
                        ),
                        "action": ISSUE_ACTIONS.get(
                            code,
                            "入力・原資料・表示値を確認してから再計算してください。",
                        ),
                    }
                )
        project_payload = (
            None
            if session.project is None
            else session.project.model_dump(mode="json")
        )
        outcome_payload = (
            None
            if session.outcome is None
            else session.outcome.model_dump(mode="json")
        )
        candidates = self._candidate_payload(session.import_result)
        summaries = self.project_service.list_projects()
        return {
            "runtime": {
                "weatherLabel": self.weather_label,
                "developmentWeather": self.development_weather,
                "referenceDatasetId": self.reference_catalog.manifest.dataset_id,
                "referenceRevision": self.reference_catalog.manifest.revision,
                "performanceRevision": self.performance.manifest.source_revision,
                "performanceValidationStatus": self.performance.manifest.validation_status,
            },
            "airports": [
                {
                    "id": airport.id,
                    "icao": airport.icao,
                    "name": airport.name,
                    "latitudeDeg": airport.latitude_deg,
                    "longitudeDeg": airport.longitude_deg,
                    "elevationFtMsl": airport.elevation_ft_msl,
                    "patternAltitudeFtMsl": airport.pattern_altitude_ft_msl,
                    "patternAltitudeValidationStatus": (
                        airport.pattern_altitude_validation_status.value
                    ),
                }
                for airport in sorted(
                    self.reference_catalog.airports.values(),
                    key=lambda item: item.icao,
                )
            ],
            "savedProjects": [
                {
                    "id": str(summary.id),
                    "name": summary.name,
                    "status": summary.status.value,
                    "revision": summary.revision,
                    "updatedAt": summary.updated_at.isoformat(),
                }
                for summary in summaries
            ],
            "import": {
                "filename": session.import_filename,
                **candidates,
            },
            "project": project_payload,
            "outcome": outcome_payload,
            "readiness": {
                "status": None if evaluation is None else evaluation.status.value,
                "calculationIsCurrent": (
                    False if evaluation is None else evaluation.calculation_is_current
                ),
                "transferAidAllowed": (
                    False if evaluation is None else evaluation.transfer_aid_allowed
                ),
                "workflowStep": self._workflow_step(session),
                "nextAction": self._next_action(session, issues),
                "issues": issues,
            },
        }

    def _evaluate(self, session: WebSession) -> ReadinessEvaluation:
        if session.project is None:
            raise WebApplicationError("PROJECT_REQUIRED", "Projectがありません。")
        materialized = session.readiness_service.evaluate(
            session.project,
            session.outcome,
        )
        session.project = materialized.project
        session.outcome = materialized.outcome
        session.readiness = materialized.evaluation
        return materialized.evaluation

    def _selected_airports(self, departure_id: str, destination_id: str) -> tuple[Any, Any]:
        try:
            departure = self.reference_catalog.airports[departure_id]
            destination = self.reference_catalog.airports[destination_id]
        except KeyError as error:
            raise WebApplicationError(
                "AIRPORT_NOT_FOUND",
                "選択空港がactive参照データにありません。",
            ) from error
        return departure, destination

    def _entries_from_candidate(
        self,
        result: KmlImportResult,
        request: ConfirmRouteRequest,
    ) -> list[RouteEntry]:
        if request.candidate_kind == "line":
            line = select_imported_line(result, request.candidate_index)
            return [
                (f"{line.name} {index + 1:02d}", lat, lon, "KML/KMZ LineString")
                for index, (lat, lon) in enumerate(line.coordinates)
            ]
        if request.candidate_kind == "polygon":
            if not request.polygon_route_confirmed:
                raise WebApplicationError(
                    "POLYGON_CONFIRMATION_REQUIRED",
                    "Polygon境界の開始点・進行方向をKML記載順で使うことを確認してください。",
                )
            outer = select_imported_polygon_outer(result, request.candidate_index)
            polygon = result.polygons[request.candidate_index]
            return [
                (f"{polygon.name} {index + 1:02d}", lat, lon, "KML/KMZ Polygon")
                for index, (lat, lon) in enumerate(outer)
            ]
        indices = request.point_indices or list(range(len(result.points)))
        if len(indices) < 2:
            raise WebApplicationError(
                "POINT_SELECTION_REQUIRED",
                "経路に使うPointをKML記載順で2件以上選択してください。",
            )
        try:
            points = [result.points[index] for index in indices]
        except IndexError as error:
            raise WebApplicationError(
                "POINT_NOT_FOUND",
                "選択したPointが現在のKMLにありません。",
            ) from error
        return [
            (point.name, point.latitude_deg, point.longitude_deg, "KML/KMZ Point")
            for point in points
        ]

    def _align_route_endpoints(
        self,
        entries: list[RouteEntry],
        departure_id: str,
        destination_id: str,
    ) -> list[RouteEntry]:
        if len(entries) < 2:
            raise WebApplicationError(
                "ROUTE_INCOMPLETE",
                "選択形状には2点以上の座標が必要です。",
            )
        departure = self.reference_catalog.airports[departure_id]
        destination = self.reference_catalog.airports[destination_id]
        deduplicated = [entries[0]]
        for entry in entries[1:]:
            if (entry[1], entry[2]) != (deduplicated[-1][1], deduplicated[-1][2]):
                deduplicated.append(entry)
        start = (deduplicated[0][1], deduplicated[0][2])
        end = (deduplicated[-1][1], deduplicated[-1][2])
        direct = (
            self._distance_to_airport(start, departure)
            + self._distance_to_airport(end, destination)
        )
        reverse = (
            self._distance_to_airport(start, destination)
            + self._distance_to_airport(end, departure)
        )
        if reverse + 0.1 < direct:
            raise WebApplicationError(
                "ROUTE_DIRECTION_MISMATCH",
                "KMLの開始・終了方向が選択したFROM/TOと逆です。",
            )
        departure_gap = self._distance_to_airport(start, departure)
        destination_gap = self._distance_to_airport(end, destination)
        if departure_gap > 5 or destination_gap > 5:
            raise WebApplicationError(
                "ROUTE_AIRPORT_ENDPOINT_MISMATCH",
                "KML端点は選択空港の5 NM以内にしてください。",
            )
        deduplicated[0] = (
            departure.icao,
            float(departure.latitude_deg),
            float(departure.longitude_deg),
            f"REFERENCE:{departure.source_revision}",
        )
        deduplicated[-1] = (
            destination.icao,
            float(destination.latitude_deg),
            float(destination.longitude_deg),
            f"REFERENCE:{destination.source_revision}",
        )
        return deduplicated

    @staticmethod
    def _distance_to_airport(coordinate: tuple[float, float], airport: Any) -> float:
        return geodesic_leg(
            coordinate[0],
            coordinate[1],
            float(airport.latitude_deg),
            float(airport.longitude_deg),
        ).distance_nm

    def _install_route(
        self,
        project: Project,
        entries: list[RouteEntry],
        altitude_ft_msl: float,
        destination_id: str,
        *,
        use_penultimate_as_vrep: bool,
    ) -> None:
        destination = self.reference_catalog.airports[destination_id]
        project.route_nodes = [
            RouteNode(
                project_id=project.id,
                sequence=index,
                name=(
                    entry[0]
                    if index in {0, len(entries) - 1}
                    else f"WP{index}"
                ),
                latitude_deg=entry[1],
                longitude_deg=entry[2],
                role=(
                    RouteNodeRole.AIRPORT
                    if index == 0
                    else (
                        RouteNodeRole.DESTINATION
                        if index == len(entries) - 1
                        else RouteNodeRole.ROUTE_POINT
                    )
                ),
                source=entry[3],
            )
            for index, entry in enumerate(entries)
        ]
        if use_penultimate_as_vrep and len(project.route_nodes) >= 3:
            project.route_nodes[-2].role = RouteNodeRole.VISUAL_REPORTING_POINT
        phases = self._initial_phases(len(project.route_nodes) - 1)
        visual_altitude = altitude_ft_msl
        if use_penultimate_as_vrep and len(project.route_nodes) >= 3:
            vrep = project.route_nodes[-2]
            vrep_distance_nm = geodesic_leg(
                vrep.latitude_deg,
                vrep.longitude_deg,
                float(destination.latitude_deg),
                float(destination.longitude_deg),
            ).distance_nm
            visual_altitude = standard_vrep_altitude_ft_msl(
                vrep_distance_nm,
                float(destination.elevation_ft_msl),
            )
        project.sections = [
            NavSection(
                project_id=project.id,
                sequence=index,
                from_node_id=start.id,
                to_node_id=end.id,
                phase=phases[index],
                planned_altitude_ft_msl=(
                    visual_altitude
                    if phases[index] == FlightPhase.VISUAL_ARRIVAL
                    else altitude_ft_msl
                ),
            )
            for index, (start, end) in enumerate(
                zip(project.route_nodes, project.route_nodes[1:], strict=False)
            )
        ]
        project.metadata["auto_phase_assignment"] = {
            "method": "IMPORTED_NAV2_POSITIONAL_V1",
            "phases": [phase.value for phase in phases],
            "review_required": True,
        }

    @staticmethod
    def _initial_phases(section_count: int) -> list[FlightPhase]:
        if section_count >= 4:
            return [
                FlightPhase.CLIMB,
                *([FlightPhase.CRUISE] * (section_count - 3)),
                FlightPhase.DESCENT,
                FlightPhase.VISUAL_ARRIVAL,
            ]
        if section_count == 3:
            return [FlightPhase.CLIMB, FlightPhase.DESCENT, FlightPhase.VISUAL_ARRIVAL]
        if section_count == 2:
            return [FlightPhase.CLIMB, FlightPhase.VISUAL_ARRIVAL]
        return [FlightPhase.VISUAL_ARRIVAL]

    def _apply_arrival_plan(
        self,
        project: Project,
        request: UpdateProjectRequest,
    ) -> None:
        state = project.metadata.get("ui_state")
        current = (
            PersistedUiState()
            if state is None
            else self.project_service.ui_state(project)
        )
        vrep_id = request.visual_reporting_point_node_id
        for index, node in enumerate(project.route_nodes):
            if node.role == RouteNodeRole.VISUAL_REPORTING_POINT:
                node.role = RouteNodeRole.ROUTE_POINT
            if index == 0:
                node.role = RouteNodeRole.AIRPORT
            elif index == len(project.route_nodes) - 1:
                node.role = RouteNodeRole.DESTINATION
        plan = None
        if vrep_id is not None:
            ordered = project.ordered_nodes()
            if len(ordered) < 2 or ordered[-2].id != vrep_id:
                raise WebApplicationError(
                    "VISUAL_REPORTING_POINT_ROUTE_INVALID",
                    "VREPは目的空港直前のRoute点にしてください。",
                )
            ordered[-2].role = RouteNodeRole.VISUAL_REPORTING_POINT
            if project.sections:
                project.sections[-1].phase = FlightPhase.VISUAL_ARRIVAL
            if len(project.sections) >= 2:
                project.sections[-2].phase = FlightPhase.DESCENT
            plan = ArrivalPlan(
                visual_reporting_point_node_id=vrep_id,
                altitude_mode=request.arrival_altitude_mode,
                manual_vrep_altitude_ft_msl=request.manual_vrep_altitude_ft_msl,
                manual_override_reason=request.manual_vrep_reason,
            )
        self.project_service.set_ui_state(
            project,
            current.model_copy(update={"arrival_plan": plan}),
            reconfirmed=True,
        )

    def _candidate_payload(self, result: KmlImportResult | None) -> dict[str, Any]:
        if result is None:
            return {"warnings": [], "sourceFiles": [], "candidates": []}
        candidates: list[dict[str, Any]] = []
        for index, line in enumerate(result.lines):
            candidates.append(
                {
                    "kind": "line",
                    "index": index,
                    "name": line.name,
                    "vertexCount": len(line.coordinates),
                    "distanceNm": round(imported_line_length_nm(line), 2),
                    "coordinates": [list(item) for item in line.display_coordinates],
                }
            )
        for index, polygon in enumerate(result.polygons):
            candidates.append(
                {
                    "kind": "polygon",
                    "index": index,
                    "name": polygon.name,
                    "vertexCount": len(polygon.outer_boundary),
                    "distanceNm": None,
                    "coordinates": [list(item) for item in polygon.display_outer_boundary],
                }
            )
        if len(result.points) >= 2:
            candidates.append(
                {
                    "kind": "points",
                    "index": 0,
                    "name": f"Point Placemark {len(result.points)}件（記載順）",
                    "vertexCount": len(result.points),
                    "distanceNm": None,
                    "coordinates": [
                        [point.latitude_deg, point.longitude_deg] for point in result.points
                    ],
                }
            )
        return {
            "warnings": list(result.warnings),
            "sourceFiles": list(result.source_files),
            "candidates": candidates,
        }

    def _workflow_step(self, session: WebSession) -> int:
        if session.project is None:
            return 1
        if session.outcome is None:
            return 2
        return 3

    @staticmethod
    def _next_action(session: WebSession, issues: list[dict[str, Any]]) -> str:
        if session.import_result is None and session.project is None:
            return "KML/KMZを読み込んでください"
        if session.project is None:
            return "経路と飛行計画を確認してください"
        blockers = [item for item in issues if item["severity"] == "BLOCKER"]
        if blockers:
            return str(blockers[0]["action"])
        pending = [
            item
            for item in issues
            if item["acknowledgementRequired"] and not item["acknowledged"]
        ]
        if pending:
            return "Warningを確認済みにしてください"
        if session.outcome is None:
            return "NAV LOGを計算してください"
        return "A4転記補助HTMLを出力できます"

    @staticmethod
    def _departure_datetime(flight_date: Any, hhmm: str) -> datetime:
        hour, minute = (int(value) for value in hhmm.split(":", 1))
        return datetime(
            flight_date.year,
            flight_date.month,
            flight_date.day,
            hour,
            minute,
            tzinfo=JST,
        )

    def _unique_project_name(self, requested: str) -> str:
        normalized = self._normalize_project_name(requested)
        existing = {summary.name for summary in self.project_service.list_projects()}
        if normalized not in existing:
            return normalized
        ordinal = 2
        while True:
            suffix = f"_{ordinal}"
            candidate = f"{normalized[: 60 - len(suffix)]}{suffix}"
            if candidate not in existing:
                return candidate
            ordinal += 1

    @staticmethod
    def _normalize_project_name(value: str) -> str:
        normalized = "".join(
            "_" if character in '<>:"/\\|?*' or ord(character) < 32 else character
            for character in value.strip()
        ).strip()
        return (normalized or "route")[:60]
