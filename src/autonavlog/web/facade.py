from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from secrets import compare_digest, token_urlsafe
from threading import RLock
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from autonavlog.application.arrival import calculate_arrival_altitude, standard_vrep_altitude_ft_msl
from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.checkpoints import project_check_points
from autonavlog.application.navlog_display import reproject_route_node_labels
from autonavlog.application.project_service import ProjectService
from autonavlog.application.readiness import ReadinessEvaluation
from autonavlog.application.readiness_service import ReadinessService
from autonavlog.application.rjfm_coordinate_matcher import (
    TRIGGER_TOLERANCE_NM,
    coordinate_distance_nm,
    coordinate_matches_reference,
)
from autonavlog.application.rjfm_departure_plan import (
    RJFM_INPUT_MODE_EDITABLE,
    TARGET_ALTITUDE_FT_MSL,
    rjfm_section_input_modes,
)
from autonavlog.application.rjfm_departure_service import (
    build_rjfm_departure_guidance,
    normalize_rjfm_departure_plan,
)
from autonavlog.application.rjfm_inbound_plan import (
    RJFM_INPUT_MODE_OMARU_TO_UMK_FIXED,
    RjfmInboundReferences,
    apply_rjfm_inbound_exception,
    rjfm_inbound_section_input_modes,
)
from autonavlog.application.rjfm_inbound_plan import (
    TARGET_ALTITUDE_FT_MSL as RJFM_INBOUND_TARGET_ALTITUDE_FT_MSL,
)
from autonavlog.application.rjfm_inbound_service import build_rjfm_inbound_guidance
from autonavlog.domain.calculation import CalculationOutcome, Issue
from autonavlog.domain.enums import (
    AdoptedSource,
    FlightPhase,
    IssueSeverity,
    RouteNodeNameSource,
    RouteNodeRole,
    VisualReferenceRole,
)
from autonavlog.domain.planning import (
    ArrivalAltitudeMode,
    ArrivalPlan,
    PersistedUiState,
    ReferenceDataSnapshot,
    RjfmCoordinate,
    load_persisted_ui_state,
)
from autonavlog.domain.project import NavSection, Project, RouteNode, VisualReference
from autonavlog.importers.kml import (
    KmlImportError,
    KmlImportResult,
    KmlRouteCoordinateLimitExceeded,
    connected_line_route_shape,
    imported_line_length_nm,
    select_imported_connected_line,
    select_imported_line,
    select_imported_polygon_outer,
    waypoint_name_slots_from_line,
)
from autonavlog.nav.geodesy import geodesic_leg
from autonavlog.nav.variation import variation_for_departure_latitude
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.storage.airports import AirportRepository
from autonavlog.storage.reference_data import (
    ReferenceCatalog,
    ReferenceDataCatalogRepository,
)
from autonavlog.storage.repository import ProjectSummary
from autonavlog.storage.rjfm_inbound_reference import RjfmInboundGuidanceReference
from autonavlog.storage.rjfm_reference import RjfmReferencePack
from autonavlog.version import __version__
from autonavlog.weather.destination_taf import (
    DestinationWindForecast,
    DestinationWindProvider,
    unavailable_destination_wind,
)
from autonavlog.weather.ftd_provider import FtdWeatherProvider
from autonavlog.weather.prewarm import WeatherPrewarmer
from autonavlog.weather.provider import WeatherProvider

from .cloudflare_access import AccessTokenVerifier
from .cruising_altitude import (
    LEGAL_THRESHOLD_NOTE_JA,
    TERRAIN_LIMITATION_NOTE_JA,
    magnetic_course_deg,
    matches_vfr_cruising_altitude,
    operational_magnetic_course_deg,
    vfr_cruising_altitude_candidates,
)
from .models import (
    ConfirmRouteRequest,
    ReplaceCheckPointsRequest,
    SaveProjectRequest,
    UpdateProjectRequest,
)

JST = ZoneInfo("Asia/Tokyo")
RouteEntry = tuple[str, float, float, str, RouteNodeNameSource]
ROUTE_EDITOR_VREP_REASON = "経路画面で指定したVREP計画高度"


ISSUE_ACTIONS: dict[str, str] = {
    "AIRPORT_DATA_UNAVAILABLE": "参照データとFROM/TOを確認してください。",
    "PATTERN_ALTITUDE_REQUIRED": (
        "目的空港のmaster値と今回採用する場周経路高度を確認してください。"
    ),
    "PERFORMANCE_DATA_UNAVAILABLE": "検証済み性能データを読み込んでください。",
    "PERFORMANCE_DATA_UNVERIFIED": "性能データの版とSHA-256を確認してください。",
    "ROUTE_INCOMPLETE": "KML/KMZから2点以上の経路を確定してください。",
    "RECALCULATION_REQUIRED": "現在の入力でNAV LOGを再計算してください。",
    "VISUAL_REPORTING_POINT_REQUIRED": "目的空港直前のVREPを選択してください。",
    "VISUAL_REPORTING_POINT_ROUTE_INVALID": "VREPの位置と到着順序を確認してください。",
    "ARRIVAL_ALTITUDE_OVERRIDE_REASON_REQUIRED": "変則Entryの高度と理由を入力してください。",
    "FORECAST_PREPARE_FAILED": "通信とForecast Runを確認して再計算してください。",
    "FORECAST_RUN_OUT_OF_COVERAGE": "互換Forecast Runへ切り替えてください。",
    "WEATHER_QUERY_FAILED": "通信と気象providerを確認して再計算してください。",
    "WIND_UNAVAILABLE": "風を取得するか、風向・風速を手入力してください。",
    "TEMPERATURE_UNAVAILABLE": "気温を取得するか手入力してください。",
    "CRUISE_POWER_TABLE_BOUNDARY_USED": (
        "65% PWRの線形外挿値とPOH原表を確認してください。"
    ),
    "PILOT_REQUIRED": "PILOTを入力してください。",
    "SHIP_REQUIRED": "SHIPを入力してください。",
    "DEVELOPMENT_WEATHER_PROVIDER": "実気象providerで再計算してください。",
    "CP_LINK_REQUIRED": "Check Pointの関連Legを選択してください。",
    "CP_NOT_ABEAM_LINKED_SECTION": "Check Pointの座標または関連Legを修正してください。",
}


def _owner_ids_match(left: str, right: str) -> bool:
    return compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class WebApplicationError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass
class WebSession:
    token: str
    owner_id: str
    calculation_service: CalculationService
    weather_provider: WeatherProvider
    readiness_service: ReadinessService
    lock: RLock = field(default_factory=RLock, repr=False)
    saved_projects_cache: tuple[ProjectSummary, ...] | None = None
    saved_projects_generation: int = -1
    import_result: KmlImportResult | None = None
    import_filename: str | None = None
    project: Project | None = None
    outcome: CalculationOutcome | None = None
    destination_wind: DestinationWindForecast | None = None
    readiness: ReadinessEvaluation | None = None


class AutoNavLogWebApplication:
    def __init__(
        self,
        *,
        project_service: ProjectService,
        airports: AirportRepository,
        performance: PerformanceRepository,
        rjfm_reference_pack: RjfmReferencePack,
        reference_repository: ReferenceDataCatalogRepository,
        reference_catalog: ReferenceCatalog,
        weather_factory: Callable[[], WeatherProvider],
        weather_label: str,
        development_weather: bool,
        maximum_sessions: int = 128,
        trusted_local_identity: str | None = None,
        access_verifier: AccessTokenVerifier | None = None,
        weather_prewarmer: WeatherPrewarmer | None = None,
        destination_wind_provider: DestinationWindProvider | None = None,
        rjfm_inbound_guidance_reference: RjfmInboundGuidanceReference | None = None,
    ) -> None:
        if maximum_sessions < 1:
            raise ValueError("maximum_sessions must be positive")
        self.project_service = project_service
        self.airports = airports
        self.performance = performance
        self.rjfm_reference_pack = rjfm_reference_pack
        self.rjfm_inbound_guidance_reference = rjfm_inbound_guidance_reference
        self.reference_repository = reference_repository
        self.reference_catalog = reference_catalog
        self.weather_factory = weather_factory
        self.weather_label = weather_label
        self.development_weather = development_weather
        self.maximum_sessions = maximum_sessions
        self.trusted_local_identity = trusted_local_identity
        self._sessions: dict[str, WebSession] = {}
        self.access_verifier = access_verifier
        self.weather_prewarmer = weather_prewarmer
        self.destination_wind_provider = destination_wind_provider
        self._session_order: list[str] = []
        self._projects_generation = 0
        self._lock = RLock()

    def create_session(self, owner_id: str) -> WebSession:
        with self._lock:
            while len(self._session_order) >= self.maximum_sessions:
                expired = self._session_order.pop(0)
                self._sessions.pop(expired, None)
            token = token_urlsafe(32)
            weather = self.weather_factory()
            calculation = CalculationService(
                self.airports,
                self.performance,
                expected_rjfm_reference_revision=self.rjfm_reference_pack.revision,
                expected_rjfm_reference_content_fingerprint=(
                    self.rjfm_reference_pack.content_fingerprint
                ),
            )
            session = WebSession(
                token=token,
                calculation_service=calculation,
                owner_id=owner_id,
                weather_provider=weather,
                readiness_service=ReadinessService(
                    calculation,
                    msm_package_version=getattr(weather, "package_version", None),
                    require_crew_identification=False,
                    require_defaults_review=False,
                ),
            )
            self._sessions[token] = session
            self._session_order.append(token)
            return session

    def session(self, token: str, owner_id: str) -> WebSession:
        with self._lock:
            try:
                session = self._sessions[token]
            except KeyError as error:
                raise WebApplicationError(
                    "SESSION_NOT_FOUND",
                    "セッションの有効期限が切れました。画面を再読み込みしてください。",
                    status_code=401,
                ) from error
            if not _owner_ids_match(session.owner_id, owner_id):
                raise WebApplicationError(
                    "SESSION_OWNER_MISMATCH",
                    "認証ユーザーとセッション所有者が一致しません。",
                    status_code=401,
                )
            self._session_order.remove(token)
            self._session_order.append(token)
            return session

    def invalidate_session(self, token: str, owner_id: str) -> None:
        with self._lock:
            session = self._sessions.get(token)
            if session is None or not _owner_ids_match(session.owner_id, owner_id):
                return
            self._sessions.pop(token, None)
            try:
                self._session_order.remove(token)
            except ValueError:
                pass

    def accept_import(
        self,
        session: WebSession,
        *,
        result: KmlImportResult,
        filename: str,
    ) -> dict[str, Any]:
        with session.lock:
            session.import_result = result
            session.import_filename = filename
            session.project = None
            session.outcome = None
            session.destination_wind = None
            session.readiness = None
            return self.present(session)

    def confirm_route(
        self,
        session: WebSession,
        request: ConfirmRouteRequest,
    ) -> dict[str, Any]:
        with session.lock:
            if not request.route_use_confirmed:
                raise WebApplicationError(
                    "ROUTE_CONFIRMATION_REQUIRED",
                    "地図とKML記載順を確認してから経路を確定してください。",
                )
            result = session.import_result
            if result is None:
                raise WebApplicationError("KML_REQUIRED", "先にKML/KMZを読み込んでください。")
            entries = self._entries_from_candidate(result, request)
            original_departure_coordinate = [entries[0][1], entries[0][2]]
            original_destination_coordinate = [entries[-1][1], entries[-1][2]]
            departure, destination = self._airports_for_route_endpoints(
                (entries[0][1], entries[0][2]),
                (entries[-1][1], entries[-1][2]),
            )
            entries = self._align_route_endpoints(entries, departure.id, destination.id)
            entries = self._reserve_rjfm_northbound_name_slots(entries, departure.id)
            departure_time = self._departure_datetime(
                request.flight_date,
                request.departure_time_jst,
            )
            project = self.project_service.create(
                name=self._unique_project_name(
                    f"{request.flight_date.isoformat()}_{departure.icao}-{destination.icao}",
                    session,
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
            project = project.model_copy(
                update={
                    "weather_mode": request.weather_mode,
                    "ftd_weather": request.ftd_weather,
                    "run_up_included": request.run_up_included,
                    "nose_fairing_enabled": request.nose_fairing_enabled,
                    "air_conditioning_enabled": request.air_conditioning_enabled,
                    "descent_rate_fpm": request.descent_rate_fpm,
                }
            )
            project.tgl_count = request.tgl_count
            project.metadata.update(
                {
                    "project_name_auto": True,
                    "project_name_generated": project.name,
                    "web_import_filename": session.import_filename,
                    "web_owner_id": session.owner_id,
                    "web_original_departure_coordinate": list(original_departure_coordinate),
                    "web_original_destination_coordinate": list(original_destination_coordinate),
                }
            )
            if request.candidate_kind == "connected_lines":
                connected = result.connected_lines[request.candidate_index]
                project.metadata.update(
                    {
                        "web_import_container_path": list(connected.container_path),
                        "web_import_segment_names": list(connected.segment_names),
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
                    self._automatic_arrival_plan(
                        project.ordered_nodes()[-2].id,
                        destination,
                    )
                    if request.use_penultimate_as_vrep and len(project.route_nodes) >= 3
                    else None
                ),
            )
            self.project_service.set_ui_state(project, state, reconfirmed=True)
            self._normalize_rjfm_departure(project)
            if request.defaults_confirmed:
                session.readiness_service.confirm_defaults(project, None)
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
        with session.lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
            working = self._updated_project(session, request)
            materialized = session.readiness_service.evaluate(working, session.outcome)
            session.project = materialized.project
            session.outcome = materialized.outcome
            if materialized.outcome is None:
                session.destination_wind = None
            session.readiness = materialized.evaluation
            return self.present(session)

    def replace_check_points(
        self,
        session: WebSession,
        request: ReplaceCheckPointsRequest,
    ) -> dict[str, Any]:
        with session.lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
            working = session.project.model_copy(deep=True)
            existing = {
                reference.id: reference
                for reference in working.visual_references
                if reference.role == VisualReferenceRole.CHECK_POINT
            }
            supplied_ids = [item.id for item in request.check_points if item.id is not None]
            if len(supplied_ids) != len(set(supplied_ids)):
                raise WebApplicationError(
                    "CHECK_POINT_ID_DUPLICATED",
                    "同じCheck Point IDが複数回指定されています。",
                )
            replacements: list[VisualReference] = []
            for item in request.check_points:
                previous = None if item.id is None else existing.get(item.id)
                if item.id is not None and previous is None:
                    raise WebApplicationError(
                        "CHECK_POINT_NOT_FOUND",
                        "更新対象のCheck Pointが現在のProjectにありません。",
                        status_code=404,
                    )
                replacements.append(
                    VisualReference(
                        id=item.id if item.id is not None else None,
                        name=item.name,
                        latitude_deg=item.latitude_deg,
                        longitude_deg=item.longitude_deg,
                        role=VisualReferenceRole.CHECK_POINT,
                        linked_section_id=item.linked_section_id,
                        source=(previous.source if previous is not None else "WEB_MANUAL"),
                    )
                    if item.id is not None
                    else VisualReference(
                        name=item.name,
                        latitude_deg=item.latitude_deg,
                        longitude_deg=item.longitude_deg,
                        role=VisualReferenceRole.CHECK_POINT,
                        linked_section_id=item.linked_section_id,
                        source="WEB_MANUAL",
                    )
                )
            preserved = [
                reference
                for reference in working.visual_references
                if reference.role != VisualReferenceRole.CHECK_POINT
            ]
            working = working.model_copy(update={"visual_references": [*preserved, *replacements]})
            materialized = session.readiness_service.evaluate(working, session.outcome)
            session.project = materialized.project
            session.outcome = materialized.outcome
            session.readiness = materialized.evaluation
            return self.present(session)

    def rename_route_node(
        self,
        session: WebSession,
        node_id: UUID,
        name: str,
    ) -> dict[str, Any]:
        with session.lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
            node = next(
                (item for item in session.project.route_nodes if item.id == node_id),
                None,
            )
            if node is None:
                raise WebApplicationError(
                    "ROUTE_NODE_NOT_FOUND",
                    "更新対象の経路点が現在の経路にありません。",
                    status_code=404,
                )
            if node.role in {RouteNodeRole.AIRPORT, RouteNodeRole.DESTINATION}:
                raise WebApplicationError(
                    "ROUTE_NODE_RENAME_FORBIDDEN",
                    "出発・到着空港の表示名は変更できません。",
                    status_code=409,
                )
            previous_name = node.name
            node.name = name
            node.name_source = RouteNodeNameSource.USER
            if session.outcome is not None:
                session.outcome = reproject_route_node_labels(
                    session.project,
                    session.outcome,
                    node_id=node.id,
                    previous_name=previous_name,
                )
            return self.present(session)

    def update_and_calculate(
        self,
        session: WebSession,
        request: UpdateProjectRequest,
    ) -> dict[str, Any]:
        """Atomically apply editable inputs and replace the last-good calculation."""
        with session.lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
            working = self._updated_project(session, request)
            outcome, destination_wind = self._calculate_outcome(session, working)
            materialized = session.readiness_service.record_calculation(working, outcome)
            session.project = materialized.project
            session.outcome = materialized.outcome
            session.destination_wind = destination_wind
            session.readiness = materialized.evaluation
            return self.present(session)

    def calculate(
        self,
        session: WebSession,
        progress: Callable[[int, str], None] | None = None,
    ) -> dict[str, Any]:
        with session.lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
            report = progress or (lambda _percent, _message: None)
            report(10, "経路と計算条件を確認しています。")
            outcome, destination_wind = self._calculate_outcome(
                session,
                session.project,
                progress=report,
            )
            report(90, "計算結果と準備状況を反映しています。")
            materialized = session.readiness_service.record_calculation(
                session.project,
                outcome,
            )
            session.project = materialized.project
            session.outcome = materialized.outcome
            session.destination_wind = destination_wind
            session.readiness = materialized.evaluation
            report(98, "画面表示を準備しています。")
            return self.present(session)

    def _destination_wind(
        self,
        project: Project,
        outcome: CalculationOutcome | None,
    ) -> DestinationWindForecast | None:
        if outcome is None:
            return None
        destination_icao = self.airports.get(project.destination_airport_id).icao
        if not outcome.sections:
            return unavailable_destination_wind(
                destination_icao,
                None,
                "DESTINATION_ETA_UNAVAILABLE",
            )
        cumulative_seconds = outcome.sections[-1].cumulative_ete_seconds.adopted()
        if cumulative_seconds is None:
            return unavailable_destination_wind(
                destination_icao,
                None,
                "DESTINATION_ETA_UNAVAILABLE",
            )
        eta_utc = project.planned_departure_time_jst + timedelta(seconds=cumulative_seconds)
        provider = self.destination_wind_provider
        if provider is None:
            return unavailable_destination_wind(
                destination_icao,
                eta_utc,
                "TAF_PROVIDER_DISABLED",
            )
        try:
            forecast = provider.forecast(destination_icao, eta_utc)
            if forecast.airport_icao.strip().upper() != destination_icao:
                return unavailable_destination_wind(
                    destination_icao,
                    eta_utc,
                    "DESTINATION_TAF_AIRPORT_MISMATCH",
                )
            return forecast
        except Exception:
            return unavailable_destination_wind(
                destination_icao,
                eta_utc,
                "TAF_FETCH_FAILED",
            )

    def _updated_project(
        self,
        session: WebSession,
        request: UpdateProjectRequest,
    ) -> Project:
        if session.project is None:
            raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
        working = session.project.model_copy(deep=True)
        working.flight_date = request.flight_date
        working.planned_departure_time_jst = self._departure_datetime(
            request.flight_date,
            request.departure_time_jst,
        )
        if request.pilot_name is not None:
            working.pilot_name = request.pilot_name
        if request.ship_identifier is not None:
            working.ship_identifier = request.ship_identifier
        working.total_usable_fuel_gal = request.total_usable_fuel_gal
        working.default_variation_deg_east = request.default_variation_deg_east
        working.run_up_included = request.run_up_included
        working.nose_fairing_enabled = request.nose_fairing_enabled
        working.air_conditioning_enabled = request.air_conditioning_enabled
        working.descent_rate_fpm = request.descent_rate_fpm
        weather_changed = (
            working.weather_mode != request.weather_mode
            or working.ftd_weather != request.ftd_weather
        )
        working = working.model_copy(
            update={
                "weather_mode": request.weather_mode,
                "ftd_weather": request.ftd_weather,
                "selected_forecast_run_id": (
                    None if weather_changed else working.selected_forecast_run_id
                ),
            }
        )
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
            section_update: dict[str, object] = {
                "planned_altitude_ft_msl": update.planned_altitude_ft_msl,
                "phase": update.phase,
                "manual_wind_direction_deg": update.manual_wind_direction_deg,
                "manual_wind_speed_kt": update.manual_wind_speed_kt,
                "manual_temperature_c": update.manual_temperature_c,
                "manual_tas_kt": update.manual_tas_kt,
            }
            if update.manual_temperature_c_by_phase is not None:
                section_update["manual_temperature_c_by_phase"] = (
                    update.manual_temperature_c_by_phase
                )
            if update.manual_wind_by_phase is not None:
                section_update["manual_wind_by_phase"] = update.manual_wind_by_phase
            sections[update.section_id] = section.model_copy(update=section_update)
        working.sections = [sections[section.id] for section in working.ordered_sections()]
        self._apply_arrival_plan(working, request)
        self._normalize_rjfm_departure(working)
        if request.defaults_confirmed:
            session.readiness_service.confirm_defaults(working, session.outcome)
        return working

    def _calculate_outcome(
        self,
        session: WebSession,
        project: Project,
        progress: Callable[[int, str], None] | None = None,
    ) -> tuple[CalculationOutcome, DestinationWindForecast | None]:
        self._normalize_rjfm_departure(project)
        state = self.project_service.ui_state(project)
        plan = state.arrival_plan
        if (
            plan is None
            or plan.selected_pattern_altitude_ft_msl is None
            or plan.selected_pattern_altitude_source is None
        ):
            raise WebApplicationError(
                "PATTERN_ALTITUDE_REQUIRED",
                "目的空港のmaster値と今回採用する場周経路高度を確認してください。",
                status_code=409,
            )
        if project.weather_mode == "FTD":
            if project.ftd_weather is None:
                raise WebApplicationError(
                    "FTD_WEATHER_REQUIRED",
                    "FTDモードの地上風と5,000 ft風を入力してください。",
                )
            outcome = session.calculation_service.calculate(
                project,
                FtdWeatherProvider(project.ftd_weather),
                progress=progress,
            )
            destination_icao = self.airports.get(project.destination_airport_id).icao
            ftd_destination_wind = unavailable_destination_wind(
                destination_icao,
                None,
                "FTD_MODE_NO_TAF",
            ).model_copy(update={"source_label": "FTD固定気象"})
            return self._with_rjfm_guidance(session, project, outcome), ftd_destination_wind

        destination_wind: DestinationWindForecast | None = None
        outcome = session.calculation_service.calculate(
            project,
            session.weather_provider,
            progress=progress,
        )
        for _ in range(3):
            forecast = self._destination_wind(project, outcome)
            current_signature = self._destination_wind_signature(destination_wind)
            forecast_signature = self._destination_wind_signature(forecast)
            destination_wind = forecast
            if forecast_signature == current_signature:
                break
            outcome = session.calculation_service.calculate(
                project,
                session.weather_provider,
                forecast,
                progress=progress,
            )
        if not self.development_weather:
            return self._with_rjfm_guidance(session, project, outcome), destination_wind
        decorated = outcome.model_copy(
            deep=True,
            update={
                "issues": [
                    *outcome.issues,
                    Issue(
                        code="DEVELOPMENT_WEATHER_PROVIDER",
                        severity=IssueSeverity.BLOCKER,
                        message=(
                            "開発用固定気象で計算しています。実気象providerで再計算するまで"
                            "運用用の計算結果として扱えません。"
                        ),
                    ),
                ]
            },
        )
        return self._with_rjfm_guidance(session, project, decorated), destination_wind

    def _normalize_rjfm_departure(self, project: Project) -> None:
        normalize_rjfm_departure_plan(project, self.rjfm_reference_pack)
        state = self.project_service.ui_state(project)
        arrival = calculate_arrival_altitude(project, state).result
        points = self.rjfm_reference_pack.points
        apply_rjfm_inbound_exception(
            project,
            RjfmInboundReferences(
                revision=self.rjfm_reference_pack.revision,
                content_fingerprint=self.rjfm_reference_pack.content_fingerprint,
                umk=RjfmCoordinate(
                    latitude_deg=points["UMK"].position.latitude_deg,
                    longitude_deg=points["UMK"].position.longitude_deg,
                    source=f"RJFM_REFERENCE:{self.rjfm_reference_pack.revision}:UMK",
                    estimated_error_nm=0.35,
                ),
                omaru=RjfmCoordinate(
                    latitude_deg=points["OMARU"].position.latitude_deg,
                    longitude_deg=points["OMARU"].position.longitude_deg,
                    source=f"RJFM_REFERENCE:{self.rjfm_reference_pack.revision}:OMARU",
                    estimated_error_nm=0.35,
                ),
            ),
            adopted_vrep_altitude_ft_msl=(
                None if arrival is None else arrival.adopted_altitude_ft_msl
            ),
        )

    def _with_rjfm_guidance(
        self,
        session: WebSession,
        project: Project,
        outcome: CalculationOutcome,
    ) -> CalculationOutcome:
        state = self.project_service.ui_state(project)
        fingerprint_project = project.model_copy(
            deep=True,
            update={"selected_forecast_run_id": outcome.selected_forecast_run_id},
        )
        fingerprint = session.readiness_service.fingerprints(
            fingerprint_project,
            outcome,
            state,
        ).calculation_input
        guidance = build_rjfm_departure_guidance(
            project,
            outcome,
            state,
            self.performance,
            self.rjfm_reference_pack,
            generated_against_fingerprint=fingerprint,
        )
        inbound_guidance = build_rjfm_inbound_guidance(
            project,
            outcome,
            state,
            self.rjfm_inbound_guidance_reference,
            generated_against_fingerprint=fingerprint,
        )
        # Inbound guidance is intentionally transient. Saving solver diagnostics
        # could resurrect a turn point after route, wind, altitude, or reference
        # inputs change. Departure guidance has different persisted semantics.
        updated_state = state.model_copy(update={"rjfm_departure_guidance": guidance})
        self.project_service.set_ui_state(project, updated_state)
        return outcome.model_copy(
            update={
                "rjfm_departure_guidance": guidance,
                "rjfm_inbound_guidance": inbound_guidance,
            }
        )

    @staticmethod
    def _destination_wind_signature(
        forecast: DestinationWindForecast | None,
    ) -> tuple[object, ...] | None:
        if forecast is None:
            return None
        usable = (
            forecast.availability.value == "AVAILABLE"
            and forecast.wind_speed_kt is not None
            and not forecast.variable_direction
            and (forecast.wind_speed_kt == 0 or forecast.wind_direction_deg_from is not None)
        )
        if not usable:
            return ("CALM_FALLBACK",)
        return (
            forecast.wind_direction_deg_from,
            forecast.wind_speed_kt,
        )

    def acknowledge(self, session: WebSession, ack_key: str, checked: bool) -> dict[str, Any]:
        with session.lock:
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
                    "確認対象の確認事項が現在の計算状態にありません。",
                    status_code=404,
                )
            if checked:
                session.project.acknowledged_warning_codes.add(ack_key)
            else:
                session.project.acknowledged_warning_codes.discard(ack_key)
            self._evaluate(session)
            return self.present(session)

    def save(self, session: WebSession, request: SaveProjectRequest) -> dict[str, Any]:
        with session.lock:
            if session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "保存するProjectがありません。")
            self._assert_project_owner(session.project, session.owner_id)
            project_to_save = session.project.model_copy(deep=True)
            if request.name is not None:
                project_to_save.name = self._normalize_project_name(request.name)
                project_to_save.metadata["project_name_auto"] = False
            saved = self.project_service.save(project_to_save)
            session.project = saved.project
            self._projects_changed(session)
            self._evaluate(session)
            return self.present(session)

    def load(self, session: WebSession, project_id: UUID) -> dict[str, Any]:
        with session.lock:
            try:
                project = self.project_service.load(project_id)
            except ValueError as error:
                raise WebApplicationError(
                    "PROJECT_NOT_FOUND",
                    "指定されたProjectは見つかりません。",
                    status_code=404,
                ) from error
            self._assert_project_owner(project, session.owner_id)
            self._normalize_rjfm_departure(project)
            session.project = project
            session.saved_projects_cache = None
            session.outcome = None
            session.destination_wind = None
            session.import_result = None
            session.import_filename = None
            self._evaluate(session)
            return self.present(session)

    def delete(self, session: WebSession, project_id: UUID) -> dict[str, Any]:
        with session.lock:
            try:
                project = self.project_service.load(project_id)
            except (FileNotFoundError, ValueError) as error:
                raise WebApplicationError(
                    "PROJECT_NOT_FOUND",
                    "指定されたProjectは見つかりません。",
                    status_code=404,
                ) from error
            self._assert_project_owner(project, session.owner_id)
            self.project_service.delete(project_id)
            if session.project is not None and session.project.id == project_id:
                session.project = None
                session.outcome = None
                session.destination_wind = None
                session.readiness = None
            self._projects_changed(session)
            return self.present(session)

    def present(self, session: WebSession) -> dict[str, Any]:
        with session.lock:
            return self._present_unlocked(session)

    def _present_unlocked(self, session: WebSession) -> dict[str, Any]:
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
                        "acknowledgementRequired": (item.effective_acknowledgement_required),
                        "ackKey": item.ctx.ack_key,
                        "acknowledged": (
                            item.ctx.ack_key in session.project.acknowledged_warning_codes
                        ),
                        "action": ISSUE_ACTIONS.get(
                            code,
                            "入力・原資料・表示値を確認してから再計算してください。",
                        ),
                        **self._boundary_issue_details(session.outcome, item.issue),
                    }
                )
        project_payload = (
            None if session.project is None else session.project.model_dump(mode="json")
        )
        outcome_payload = (
            None if session.outcome is None else session.outcome.model_dump(mode="json")
        )
        destination_wind_payload = (
            None
            if session.destination_wind is None
            else session.destination_wind.model_dump(mode="json")
        )
        candidates = self._candidate_payload(session.import_result)
        summaries = self._owned_project_summaries(session)
        check_point_planning = self._check_point_planning(session.project)
        return {
            "runtime": {
                "appVersion": __version__,
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
                    "patternAltitudeSource": airport.pattern_altitude_source,
                    "patternAltitudeSourceRevision": (airport.pattern_altitude_source_revision),
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
            "altitudeGuidance": {
                "legalThresholdNote": LEGAL_THRESHOLD_NOTE_JA,
                "terrainLimitationNote": TERRAIN_LIMITATION_NOTE_JA,
                "sections": self._section_guidance(session.project),
            },
            "rjfmMapReference": self._rjfm_map_reference(
                session.project,
                self.rjfm_reference_pack,
            ),
            "checkPointPlanning": check_point_planning,
            "project": project_payload,
            "outcome": outcome_payload,
            "destinationWind": destination_wind_payload,
            "readiness": {
                "status": None if evaluation is None else evaluation.status.value,
                "calculationIsCurrent": (
                    False if evaluation is None else evaluation.calculation_is_current
                ),
                "workflowStep": self._workflow_step(session),
                "nextAction": self._next_action(session, issues),
                "issues": issues,
            },
        }

    @staticmethod
    def _boundary_issue_details(
        outcome: CalculationOutcome | None,
        issue: Issue,
    ) -> dict[str, Any]:
        """Expose cruise-boundary evidence with human-readable route context.

        The stored Issue keeps only stable calculation identifiers.  Labels and
        zone order are resolved from the current result at presentation time so
        the API never needs to expose a UUID as a user-facing fallback.
        """

        raw_provenance = issue.metadata.get("boundary_provenance")
        if not isinstance(raw_provenance, list):
            return {}
        provenance: list[dict[str, Any]] = []
        for item in raw_provenance:
            if not isinstance(item, dict):
                continue
            provenance.append(
                {
                    "axis": item.get("axis"),
                    "requestedValue": item.get("requested_value"),
                    "availableMin": item.get("available_min"),
                    "availableMax": item.get("available_max"),
                    "adoptedValue": item.get("adopted_value"),
                    "pressureAltitudeFt": item.get("pressure_altitude_ft"),
                    "isaDeviationC": item.get("isa_deviation_c"),
                    "sourcePages": item.get("source_pages", []),
                    "supportingLowerValue": item.get("supporting_lower_value"),
                    "supportingUpperValue": item.get("supporting_upper_value"),
                    "supportingFraction": item.get("supporting_fraction"),
                    "extrapolated": item.get("extrapolated", False),
                }
            )
        if not provenance:
            return {}
        details: dict[str, Any] = {"boundaryProvenance": provenance}
        raw_calculation_condition = issue.metadata.get("calculation_condition")
        if isinstance(raw_calculation_condition, dict):
            details["calculationCondition"] = {
                "pressureAltitudeFt": raw_calculation_condition.get("pressure_altitude_ft"),
                "isaDeviationC": raw_calculation_condition.get("isa_deviation_c"),
            }
        raw_selected_condition = issue.metadata.get("selected_condition")
        if isinstance(raw_selected_condition, dict):
            power_percent_by_corner = raw_selected_condition.get("power_percent_by_corner")
            details["selectedCondition"] = {
                "pressureAltitudeFt": raw_selected_condition.get("pressure_altitude_ft"),
                "isaDeviationC": raw_selected_condition.get("isa_deviation_c"),
                "powerPercentByCorner": (
                    [
                        {
                            "pressureAltitudeFt": item.get("pressure_altitude_ft"),
                            "isaDeviationC": item.get("isa_deviation_c"),
                            "powerPercent": item.get("power_percent"),
                        }
                        for item in power_percent_by_corner
                        if isinstance(item, dict)
                    ]
                    if isinstance(power_percent_by_corner, list)
                    else []
                ),
            }
        location = AutoNavLogWebApplication._boundary_issue_location(outcome, issue)
        if location is not None:
            details["location"] = location
        return details

    @staticmethod
    def _boundary_issue_location(
        outcome: CalculationOutcome | None,
        issue: Issue,
    ) -> dict[str, Any] | None:
        if (
            outcome is None
            or issue.section_id is None
            or issue.segment_sequence is None
        ):
            return None
        zones = sorted(
            (section for section in outcome.sections if section.section_id == issue.section_id),
            key=lambda section: section.sequence,
        )
        zone_index = next(
            (
                index
                for index, zone in enumerate(zones)
                if zone.sequence == issue.segment_sequence
            ),
            None,
        )
        if zone_index is None:
            return None
        zone = zones[zone_index]
        parent = next(
            (
                row
                for row in outcome.display_rows
                if row.row_type == "PHYSICAL_LEG_SUMMARY" and row.section_id == issue.section_id
            ),
            None,
        )
        return {
            "fromName": zone.from_name if parent is None else parent.from_name,
            "toName": zone.to_name if parent is None else parent.to_name,
            "phase": zone.phase.value,
            "zoneOrdinal": zone_index + 1,
            "zoneCount": len(zones),
            "zoneFromName": zone.from_name,
            "zoneToName": zone.to_name,
        }

    @staticmethod
    def _check_point_planning(project: Project | None) -> dict[str, Any]:
        if project is None:
            return {"projections": [], "issues": []}
        computation = project_check_points(project)
        return {
            "projections": [
                projection.model_dump(mode="json") for projection in computation.projections
            ],
            "issues": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "sectionId": (None if issue.section_id is None else str(issue.section_id)),
                    "checkPointId": issue.metadata.get("checkpoint_id"),
                }
                for issue in computation.issues
            ],
        }

    @staticmethod
    def _rjfm_map_reference(
        project: Project | None,
        reference_pack: RjfmReferencePack,
    ) -> dict[str, Any] | None:
        if (
            project is None
            or (
                project.departure_airport_id.strip().upper() != "RJFM"
                and project.destination_airport_id.strip().upper() != "RJFM"
            )
        ):
            return None
        pca = reference_pack.pca
        civil_airspace = reference_pack.civil_training_test_airspace
        return {
            "revision": reference_pack.revision,
            "contentFingerprint": reference_pack.content_fingerprint,
            "pca": {
                "name": pca.name,
                "polygonVertices": [
                    {
                        "latitudeDeg": float(point.latitude_deg),
                        "longitudeDeg": float(point.longitude_deg),
                    }
                    for point in pca.polygon_vertices
                ],
                "exclusionCenter": {
                    "latitudeDeg": float(pca.exclusion_center.latitude_deg),
                    "longitudeDeg": float(pca.exclusion_center.longitude_deg),
                },
                "exclusionRadiusKm": float(pca.exclusion_radius_km),
                "sourceAltitudeLowerM": float(pca.source_altitude_lower_m),
                "sourceAltitudeUpperM": float(pca.source_altitude_upper_m),
                "operationalAltitudeLowerFtMsl": float(
                    pca.operational_altitude_lower_ft_msl
                ),
                "operationalAltitudeUpperFtMsl": float(
                    pca.operational_altitude_upper_ft_msl
                ),
                "altitudeBoundsInclusive": pca.altitude_bounds_inclusive,
                "operationalAltitudePolicyStatus": (
                    pca.operational_altitude_policy_status
                ),
                "sourceIds": list(pca.source_ids),
            },
            "civilTrainingTestAirspace": {
                "availability": "REMOTE_GSI_GEOJSON",
                "dataUse": civil_airspace.data_use,
                "contentFingerprintScope": (
                    civil_airspace.content_fingerprint_scope
                ),
                "sourcePageUrl": civil_airspace.source_page_url,
                "layerMetadataUrl": civil_airspace.layer_metadata_url,
                "tileUrlTemplate": civil_airspace.tile_url_template,
                "tileUrls": [
                    civil_airspace.tile_url_template.format(
                        z=tile.zoom,
                        x=tile.x,
                        y=tile.y,
                    )
                    for tile in civil_airspace.tiles
                ],
                "tiles": [
                    {
                        "url": civil_airspace.tile_url_template.format(
                            z=tile.zoom,
                            x=tile.x,
                            y=tile.y,
                        ),
                        "expectedPolygonNames": list(tile.expected_polygon_names),
                    }
                    for tile in civil_airspace.tiles
                ],
                "featureNamePrefix": civil_airspace.feature_name_prefix,
                "checkedAtUtc": civil_airspace.checked_at_utc.isoformat().replace(
                    "+00:00", "Z"
                ),
                "caution": civil_airspace.caution_jp,
                "sourceIds": list(civil_airspace.source_ids),
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

    @staticmethod
    def _assert_project_owner(project: Project, owner_id: str) -> None:
        stored_owner = project.metadata.get("web_owner_id")
        if not isinstance(stored_owner, str) or not _owner_ids_match(stored_owner, owner_id):
            raise WebApplicationError(
                "PROJECT_NOT_FOUND",
                "指定されたProjectは見つかりません。",
                status_code=404,
            )

    def _projects_changed(self, session: WebSession) -> None:
        with self._lock:
            self._projects_generation += 1
        session.saved_projects_cache = None
        session.saved_projects_generation = -1

    def _owned_project_summaries(
        self,
        session: WebSession,
    ) -> tuple[ProjectSummary, ...]:
        with self._lock:
            generation = self._projects_generation
        owned = [
            summary
            for summary in self.project_service.list_projects()
            if isinstance(summary.web_owner_id, str)
            and _owner_ids_match(summary.web_owner_id, session.owner_id)
        ]
        session.saved_projects_cache = tuple(owned)
        session.saved_projects_generation = generation
        return session.saved_projects_cache

    @staticmethod
    def _section_guidance(project: Project | None) -> list[dict[str, Any]]:
        if project is None:
            return []
        nodes = {node.id: node for node in project.route_nodes}
        input_modes: dict[str, str] = {}
        raw_ui_state = project.metadata.get("ui_state")
        if isinstance(raw_ui_state, dict):
            try:
                ui_state = load_persisted_ui_state(raw_ui_state)
            except (TypeError, ValueError):
                ui_state = None
            if ui_state is not None and ui_state.rjfm_departure_plan is not None:
                input_modes = rjfm_section_input_modes(
                    project,
                    ui_state.rjfm_departure_plan,
                )
            if ui_state is not None and ui_state.rjfm_inbound_plan is not None:
                input_modes.update(rjfm_inbound_section_input_modes(
                    project, ui_state.rjfm_inbound_plan
                ))
        guidance: list[dict[str, Any]] = []
        for section in project.ordered_sections():
            start = nodes.get(section.from_node_id)
            end = nodes.get(section.to_node_id)
            if start is None or end is None:
                continue
            true_course = geodesic_leg(
                start.latitude_deg,
                start.longitude_deg,
                end.latitude_deg,
                end.longitude_deg,
            ).initial_true_course_deg
            magnetic_course = magnetic_course_deg(
                true_course,
                variation_for_departure_latitude(start.latitude_deg).degrees_east,
            )
            vfr_cruising_altitude_magnetic_course = operational_magnetic_course_deg(
                magnetic_course
            )
            matches = matches_vfr_cruising_altitude(
                section.planned_altitude_ft_msl,
                magnetic_course,
            )
            applies_to_cruising_altitude_input = section.phase in {
                FlightPhase.CLIMB,
                FlightPhase.CRUISE,
                FlightPhase.DESCENT,
            }
            input_mode = input_modes.get(str(section.id), RJFM_INPUT_MODE_EDITABLE)
            fixed_by_rjfm = input_mode != RJFM_INPUT_MODE_EDITABLE
            guidance.append(
                {
                    "sectionId": str(section.id),
                    "magneticCourseDeg": magnetic_course,
                    "vfrCruisingAltitudeMagneticCourseDeg": (
                        vfr_cruising_altitude_magnetic_course
                    ),
                    "variationDegEast": variation_for_departure_latitude(
                        start.latitude_deg
                    ).degrees_east,
                    "candidateAltitudesFtMsl": list(
                        vfr_cruising_altitude_candidates(
                            vfr_cruising_altitude_magnetic_course
                        )
                    ),
                    "appliesToCruise": section.phase == FlightPhase.CRUISE,
                    "appliesToCruisingAltitudeInput": (
                        applies_to_cruising_altitude_input and not fixed_by_rjfm
                    ),
                    "requiresReview": (
                        applies_to_cruising_altitude_input
                        and not fixed_by_rjfm
                        and not matches
                    ),
                    "inputMode": input_mode,
                    "fixedAltitudeFtMsl": (
                        RJFM_INBOUND_TARGET_ALTITUDE_FT_MSL
                        if input_mode == RJFM_INPUT_MODE_OMARU_TO_UMK_FIXED
                        else TARGET_ALTITUDE_FT_MSL if fixed_by_rjfm else None
                    ),
                }
            )
        return guidance

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

    def _nearest_airport_within_5_nm(
        self,
        coordinate: tuple[float, float],
    ) -> tuple[Any, float] | None:
        ranked = sorted(
            (
                (
                    self._distance_to_airport(coordinate, airport),
                    airport.icao,
                    airport.id,
                    airport,
                )
                for airport in self.reference_catalog.airports.values()
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )
        if not ranked or ranked[0][0] > 5.0:
            return None
        distance, _, _, airport = ranked[0]
        return airport, distance

    def _airports_for_route_endpoints(
        self,
        departure_coordinate: tuple[float, float],
        destination_coordinate: tuple[float, float],
    ) -> tuple[Any, Any]:
        departure_match = self._nearest_airport_within_5_nm(departure_coordinate)
        destination_match = self._nearest_airport_within_5_nm(destination_coordinate)
        if departure_match is None or destination_match is None:
            missing = []
            if departure_match is None:
                missing.append("始点")
            if destination_match is None:
                missing.append("終点")
            raise WebApplicationError(
                "ROUTE_AIRPORT_ENDPOINT_NOT_FOUND",
                f"KMLの{'・'.join(missing)}から5 NM以内に空港が見つかりません。",
            )
        return departure_match[0], destination_match[0]

    def _entries_from_candidate(
        self,
        result: KmlImportResult,
        request: ConfirmRouteRequest,
    ) -> list[RouteEntry]:
        if request.candidate_kind == "connected_lines":
            try:
                connected_line = select_imported_connected_line(
                    result, request.candidate_index
                )
            except KmlRouteCoordinateLimitExceeded as error:
                raise WebApplicationError(
                    "ROUTE_COORDINATE_LIMIT_EXCEEDED",
                    f"選択した経路は座標数の上限（{error.maximum}点）を超えています。"
                    f"{error.maximum}点以下の経路を選択してください。",
                ) from error
            except KmlImportError as error:
                raise WebApplicationError(
                    "ROUTE_CANDIDATE_NOT_FOUND",
                    "選択した連結LineStringが現在のKMLにありません。",
                ) from error
            return [
                (
                    name or f"WP{index + 1}",
                    lat,
                    lon,
                    (
                        "KML/KMZ Point"
                        if source == "point"
                        else (
                            "KML/KMZ LineString name"
                            if source == "line"
                            else "KML/KMZ LineString"
                        )
                    ),
                    (
                        RouteNodeNameSource.IMPORTED
                        if name
                        else RouteNodeNameSource.GENERATED
                    ),
                )
                for index, ((lat, lon), name, source) in enumerate(
                    zip(
                        connected_line.coordinates,
                        connected_line.waypoint_names,
                        connected_line.waypoint_sources,
                        strict=True,
                    )
                )
            ]
        if request.candidate_kind == "line":
            try:
                line = select_imported_line(result, request.candidate_index)
            except KmlRouteCoordinateLimitExceeded as error:
                raise WebApplicationError(
                    "ROUTE_COORDINATE_LIMIT_EXCEEDED",
                    f"選択した経路は座標数の上限（{error.maximum}点）を超えています。"
                    f"{error.maximum}点以下の経路を選択してください。",
                ) from error
            except KmlImportError as error:
                raise WebApplicationError(
                    "ROUTE_CANDIDATE_NOT_FOUND",
                    "選択したLineStringが現在のKMLにありません。",
                ) from error
            names_by_index = waypoint_name_slots_from_line(line)
            entries: list[RouteEntry] = []
            for index, (lat, lon) in enumerate(line.coordinates):
                line_name = names_by_index[index]
                point_name = self._nearest_point_name(result, lat, lon)
                entries.append(
                    (
                        line_name or point_name or f"WP{index + 1}",
                        lat,
                        lon,
                        (
                            "KML/KMZ LineString name"
                            if line_name
                            else "KML/KMZ LineString"
                        ),
                        (
                            RouteNodeNameSource.IMPORTED
                            if line_name or point_name
                            else RouteNodeNameSource.GENERATED
                        ),
                    )
                )
            return entries
        if request.candidate_kind == "polygon":
            if not request.polygon_route_confirmed:
                raise WebApplicationError(
                    "POLYGON_CONFIRMATION_REQUIRED",
                    "Polygon境界の開始点・進行方向をKML記載順で使うことを確認してください。",
                )
            try:
                outer = select_imported_polygon_outer(result, request.candidate_index)
                polygon = result.polygons[request.candidate_index]
            except (IndexError, KmlImportError) as error:
                raise WebApplicationError(
                    "ROUTE_CANDIDATE_NOT_FOUND",
                    "選択したPolygonが現在のKMLにありません。",
                ) from error
            return [
                (
                    f"{polygon.name} {index + 1:02d}",
                    lat,
                    lon,
                    "KML/KMZ Polygon",
                    RouteNodeNameSource.GENERATED,
                )
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
            (
                point.name,
                point.latitude_deg,
                point.longitude_deg,
                "KML/KMZ Point",
                RouteNodeNameSource.IMPORTED,
            )
            for point in points
        ]

    @staticmethod
    def _nearest_point_name(
        result: KmlImportResult,
        latitude_deg: float,
        longitude_deg: float,
    ) -> str | None:
        nearest: tuple[float, str] | None = None
        for point in result.points:
            distance = geodesic_leg(
                latitude_deg,
                longitude_deg,
                point.latitude_deg,
                point.longitude_deg,
            ).distance_nm
            if nearest is None or distance < nearest[0]:
                nearest = (distance, point.name)
        if nearest is None or nearest[0] > 0.05:
            return None
        return nearest[1].strip() or None

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
        direct = self._distance_to_airport(start, departure) + self._distance_to_airport(
            end, destination
        )
        reverse = self._distance_to_airport(start, destination) + self._distance_to_airport(
            end, departure
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
            RouteNodeNameSource.GENERATED,
        )
        deduplicated[-1] = (
            destination.icao,
            float(destination.latitude_deg),
            float(destination.longitude_deg),
            f"REFERENCE:{destination.source_revision}",
            RouteNodeNameSource.GENERATED,
        )
        return deduplicated

    def _reserve_rjfm_northbound_name_slots(
        self,
        entries: list[RouteEntry],
        departure_id: str,
    ) -> list[RouteEntry]:
        """Reserve physical UMK/OMARU names before route-node installation.

        KML import stays generic: it supplies the ordered coordinates and its
        ordinary labels unchanged.  Only the RJFM northbound installation
        boundary knows that a coordinate matching the reference UMK or OMARU
        is a named physical route slot.  This deliberately does not create a
        virtual UMK or a synthetic OMARU; those remain the departure-plan
        normalizer's graph responsibility.
        """

        if departure_id.strip().upper() != "RJFM" or len(entries) < 2:
            return entries

        reserved = list(entries)
        umk = self.rjfm_reference_pack.points["UMK"].position
        omaru = self.rjfm_reference_pack.points["OMARU"].position
        reserved_indices: set[int] = set()

        def distance(index: int, point: Any) -> float:
            entry = reserved[index]
            return coordinate_distance_nm(
                entry[1],
                entry[2],
                float(point.latitude_deg),
                float(point.longitude_deg),
            )

        def reserve(index: int, name: str) -> None:
            current = reserved[index]
            explicit = current[0].strip().upper()
            # A coordinate-consistent KML UMK/OMARU label is already exact.
            # A contradictory label is positional data and must not mask the
            # physical reference slot it happens to occupy.
            if explicit != name:
                reserved[index] = (
                    name,
                    current[1],
                    current[2],
                    current[3],
                    RouteNodeNameSource.GENERATED,
                )
            reserved_indices.add(index)

        first_umk_gap = distance(1, umk)
        first_omaru_gap = distance(1, omaru)
        if min(first_umk_gap, first_omaru_gap) > TRIGGER_TOLERANCE_NM + 1e-9:
            return entries

        if first_umk_gap <= first_omaru_gap:
            reserve(1, "UMK")
            for index in range(2, len(reserved)):
                entry = reserved[index]
                if coordinate_matches_reference(
                    entry[1],
                    entry[2],
                    float(omaru.latitude_deg),
                    float(omaru.longitude_deg),
                ):
                    reserve(index, "OMARU")
                    break
        else:
            # OMARU-first routes retain a virtual UMK in the departure plan;
            # no UMK entry is inserted into this imported physical route.
            reserve(1, "OMARU")

        # A LineString's segmented title is positional metadata, not an
        # identity for a coordinate.  Once physical reference slots have been
        # reserved, continue those ordinary labels over later ordinary route
        # coordinates in their original order.  KML Point labels remain bound
        # to their explicit coordinate and therefore are neither moved nor
        # overwritten.  The coordinate sequence itself is never changed.
        ordinary_line_entries = [
            entry
            for index, entry in enumerate(entries)
            if (
                0 < index < len(entries) - 1
                and entry[3] == "KML/KMZ LineString name"
                and not (
                    entry[0].strip().upper() == "UMK"
                    and coordinate_matches_reference(
                        entry[1],
                        entry[2],
                        float(umk.latitude_deg),
                        float(umk.longitude_deg),
                    )
                )
                and not (
                    entry[0].strip().upper() == "OMARU"
                    and coordinate_matches_reference(
                        entry[1],
                        entry[2],
                        float(omaru.latitude_deg),
                        float(omaru.longitude_deg),
                    )
                )
            )
        ]
        ordinary_targets = [
            index
            for index, entry in enumerate(reserved)
            if (
                0 < index < len(reserved) - 1
                and index not in reserved_indices
                and entry[3] != "KML/KMZ Point"
            )
        ]
        for index, source_entry in zip(ordinary_targets, ordinary_line_entries, strict=False):
            current = reserved[index]
            reserved[index] = (
                source_entry[0],
                current[1],
                current[2],
                source_entry[3],
                source_entry[4],
            )
        return reserved

    @staticmethod
    def _distance_to_airport(coordinate: tuple[float, float], airport: Any) -> float:
        return geodesic_leg(
            coordinate[0],
            coordinate[1],
            float(airport.latitude_deg),
            float(airport.longitude_deg),
        ).distance_nm


    def _automatic_arrival_plan(
        self,
        vrep_id: UUID,
        destination: Any,
    ) -> ArrivalPlan:
        master = float(destination.pattern_altitude_ft_msl)
        selected_pattern = (
            int(master)
            if master.is_integer() and 100 <= master <= 25_000 and int(master) % 100 == 0
            else None
        )
        return ArrivalPlan(
            visual_reporting_point_node_id=vrep_id,
            selected_pattern_altitude_ft_msl=selected_pattern,
            selected_pattern_altitude_source=(
                AdoptedSource.AUTOMATIC if selected_pattern is not None else None
            ),
        )

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
                sequence=index,
                name=entry[0] or f"WP{index}",
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
                name_source=entry[4],
            )
            for index, entry in enumerate(entries)
        ]
        if use_penultimate_as_vrep and len(project.route_nodes) >= 3:
            project.route_nodes[-2].role = RouteNodeRole.VISUAL_REPORTING_POINT
        phases = self._initial_phases(len(project.route_nodes) - 1)
        visual_altitude = altitude_ft_msl
        if use_penultimate_as_vrep and len(project.route_nodes) >= 3:
            vrep = project.route_nodes[-2]
            plan = self._automatic_arrival_plan(vrep.id, destination)
            selected_pattern = plan.selected_pattern_altitude_ft_msl
            if selected_pattern is not None:
                vrep_distance_nm = geodesic_leg(
                    vrep.latitude_deg,
                    vrep.longitude_deg,
                    float(destination.latitude_deg),
                    float(destination.longitude_deg),
                ).distance_nm
                visual_altitude = standard_vrep_altitude_ft_msl(
                    vrep_distance_nm,
                    selected_pattern,
                )
        project.sections = [
            NavSection(
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
        current = PersistedUiState() if state is None else self.project_service.ui_state(project)
        vrep_id = (
            request.visual_reporting_point_node_id
            if request.visual_reporting_point_node_id is not None
            else (
                None
                if current.arrival_plan is None
                else current.arrival_plan.visual_reporting_point_node_id
            )
        )
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

            snapshot = current.reference_data_snapshot
            current_plan = current.arrival_plan
            selected_pattern = request.selected_pattern_altitude_ft_msl
            destination = None if snapshot is None else snapshot.destination_airport
            if selected_pattern is None and current_plan is not None:
                selected_pattern = current_plan.selected_pattern_altitude_ft_msl
            if selected_pattern is None and destination is not None:
                selected_pattern = self._automatic_arrival_plan(
                    vrep_id,
                    destination,
                ).selected_pattern_altitude_ft_msl
            if destination is None:
                selected_pattern = None
                selected_source = None
            else:
                if (
                    selected_pattern is not None
                    and selected_pattern <= destination.elevation_ft_msl
                ):
                    raise WebApplicationError(
                        "PATTERN_ALTITUDE_REQUIRED",
                        "採用場周経路高度は目的空港標高より高くしてください。",
                    )
                selected_source = (
                    None
                    if selected_pattern is None
                    else (
                        AdoptedSource.AUTOMATIC
                        if selected_pattern == destination.pattern_altitude_ft_msl
                        else AdoptedSource.MANUAL
                    )
                )

            altitude_mode = request.arrival_altitude_mode
            manual_vrep_altitude = request.manual_vrep_altitude_ft_msl
            manual_vrep_reason = request.manual_vrep_reason
            visual_section = project.ordered_sections()[-1] if project.sections else None
            if (
                destination is not None
                and selected_pattern is not None
                and visual_section is not None
            ):
                distance_nm = geodesic_leg(
                    ordered[-2].latitude_deg,
                    ordered[-2].longitude_deg,
                    destination.latitude_deg,
                    destination.longitude_deg,
                ).distance_nm
                automatic_altitude = standard_vrep_altitude_ft_msl(
                    distance_nm,
                    selected_pattern,
                )
                if altitude_mode == ArrivalAltitudeMode.MANUAL_NON_STANDARD_ENTRY:
                    if manual_vrep_altitude is not None:
                        visual_section.planned_altitude_ft_msl = manual_vrep_altitude
                elif request.selected_pattern_altitude_ft_msl is not None:
                    visual_section.planned_altitude_ft_msl = automatic_altitude
                else:
                        route_altitude = float(visual_section.planned_altitude_ft_msl)
                        if abs(route_altitude - automatic_altitude) > 1e-9:
                            rounded_route_altitude = round(route_altitude)
                            if (
                                abs(route_altitude - rounded_route_altitude) > 1e-9
                                or rounded_route_altitude % 100 != 0
                            ):
                                raise WebApplicationError(
                                    "VREP_ALTITUDE_INVALID",
                                    "VREP高度は100 ft単位で入力してください。",
                                )
                            altitude_mode = ArrivalAltitudeMode.MANUAL_NON_STANDARD_ENTRY
                            manual_vrep_altitude = rounded_route_altitude
                            manual_vrep_reason = ROUTE_EDITOR_VREP_REASON
            plan = ArrivalPlan(
                visual_reporting_point_node_id=vrep_id,
                selected_pattern_altitude_ft_msl=selected_pattern,
                selected_pattern_altitude_source=selected_source,
                altitude_mode=altitude_mode,
                manual_vrep_altitude_ft_msl=manual_vrep_altitude,
                manual_override_reason=manual_vrep_reason,
            )
        self.project_service.set_ui_state(
            project,
            current.model_copy(update={"arrival_plan": plan}),
            reconfirmed=True,
        )

    def _candidate_payload(self, result: KmlImportResult | None) -> dict[str, Any]:
        if result is None:
            return {"warnings": [], "sourceFiles": [], "candidates": []}
        route_candidates: list[tuple[int, int, dict[str, Any]]] = []
        suppressed_line_indices = {
            line_index
            for connected in result.connected_lines
            for line_index in connected.segment_indices
        }
        ordinal = 0
        for index, connected in enumerate(result.connected_lines):
            route_shape = connected_line_route_shape(connected)
            route_candidates.append(
                (
                    connected.document_order,
                    ordinal,
                    {
                        "kind": "connected_lines",
                        "index": index,
                        "name": connected.name,
                        "containerPath": list(connected.container_path),
                        "segmentNames": list(connected.segment_names),
                        "segmentCount": len(connected.segment_names),
                        "legCount": len(route_shape.coordinates) - 1,
                        "vertexCount": len(route_shape.coordinates),
                        "distanceNm": round(connected.distance_nm, 2),
                        "maxJoinGapNm": round(connected.max_join_gap_nm, 5),
                        "coordinates": [
                            list(item) for item in route_shape.display_coordinates
                        ],
                    },
                )
            )
            ordinal += 1
        for index, line in enumerate(result.lines):
            if index in suppressed_line_indices:
                continue
            route_candidates.append(
                (
                    line.document_order,
                    ordinal,
                    {
                        "kind": "line",
                        "index": index,
                        "name": line.name,
                        "vertexCount": len(line.coordinates),
                        "distanceNm": round(imported_line_length_nm(line), 2),
                        "coordinates": [list(item) for item in line.display_coordinates],
                    },
                )
            )
            ordinal += 1
        candidates = [
            item[2] for item in sorted(route_candidates, key=lambda item: item[:2])
        ]
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
        if len(result.points) >= 2 and not result.lines and not result.connected_lines:
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
        for candidate in candidates:
            coordinates = candidate.get("coordinates")
            departure_match = None
            destination_match = None
            if isinstance(coordinates, list) and coordinates:
                departure_match = self._nearest_airport_within_5_nm(
                    (float(coordinates[0][0]), float(coordinates[0][1]))
                )
                destination_match = self._nearest_airport_within_5_nm(
                    (float(coordinates[-1][0]), float(coordinates[-1][1]))
                )
            candidate.update(
                {
                    "departureAirportId": (
                        None if departure_match is None else departure_match[0].id
                    ),
                    "departureDistanceNm": (
                        None if departure_match is None else round(departure_match[1], 3)
                    ),
                    "destinationAirportId": (
                        None if destination_match is None else destination_match[0].id
                    ),
                    "destinationDistanceNm": (
                        None if destination_match is None else round(destination_match[1], 3)
                    ),
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
            item for item in issues if item["acknowledgementRequired"] and not item["acknowledged"]
        ]
        if pending:
            return "確認事項を確認済みにしてください"
        if session.outcome is None:
            return "NAV LOGを計算してください"
        return "NAV LOGの計算結果を確認してください"

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

    def _unique_project_name(self, requested: str, session: WebSession) -> str:
        normalized = self._normalize_project_name(requested)
        existing = {summary.name for summary in self._owned_project_summaries(session)}
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
        normalized = normalized[:60].rstrip(" .")
        return normalized or "route"
