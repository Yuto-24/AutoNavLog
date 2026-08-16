from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any
from uuid import UUID

from autonavlog.domain.calculation import (
    CalculationOutcome,
    DerivedRoutePoint,
    FuelPlan,
    Issue,
    IterationRecord,
    NavLogDisplayRow,
    SectionResult,
)
from autonavlog.domain.enums import (
    AdoptedSource,
    Availability,
    DerivedPointType,
    FlightPhase,
    IssueSeverity,
    Pa500Policy,
    ProjectStatus,
    ValueState,
    WeatherRequestKind,
)
from autonavlog.domain.planning import (
    AirportSelection,
    ArrivalAltitudeResult,
    CheckPointProjection,
    PersistedUiState,
    RjfmDeparturePlan,
    RjfmMainRouteMode,
    load_persisted_ui_state,
)
from autonavlog.domain.project import Airport, NavSection, Project, RouteNode
from autonavlog.domain.values import AdoptedValue
from autonavlog.domain.weather import ForecastRequirement, WeatherRequest, WeatherResult
from autonavlog.nav.airspeed import (
    cas_from_tas,
    isa_temperature_c,
    tas_from_cas,
)
from autonavlog.nav.fuel import build_fuel_plan, fuel_for_section, remaining_fuel
from autonavlog.nav.geodesy import (
    GeodesicLeg,
    geodesic_leg,
)
from autonavlog.nav.variation import variation_for_departure_latitude
from autonavlog.nav.wind_triangle import WindTriangleError, solve_wind_triangle
from autonavlog.performance.climb import ClimbCalculator, ClimbPerformanceError
from autonavlog.performance.cruise import (
    CruisePerformanceError,
    CruisePerformanceSelectionPolicy,
)
from autonavlog.performance.repository import PerformanceDataError, PerformanceRepository
from autonavlog.storage.airports import AirportRepository
from autonavlog.weather.destination_taf import DestinationWindForecast
from autonavlog.weather.provider import WeatherProvider

from .arrival import calculate_arrival_altitude
from .checkpoints import project_check_points
from .forecast_service import ForecastService
from .navlog_display import NavLogPhysicalLeg, build_navlog_display_rows
from .phase_segments import (
    PhaseSegmentation,
    PhaseSegmentationError,
    PhysicalRouteLeg,
    RouteBoundary,
    split_route_into_phase_segments,
)
from .rjfm_departure_plan import rjfm_plan_matches_project


@dataclass(frozen=True)
class CalculationPolicies:
    version: str = "nav2-v7-rjfm-umk-guidance"
    # Kept for serialized policy compatibility. NAV2-v5 uses MSL directly and
    # does not round or QNH-correct a separate planning pressure altitude.
    pa_500_policy: Pa500Policy = Pa500Policy.CEILING
    max_iterations: int = 5
    convergence_seconds: float = 30.0
    phase_boundary_distance_tolerance_nm: float = 0.25


@dataclass(frozen=True)
class _Geometry:
    section: NavSection
    start: RouteNode
    end: RouteNode
    geodesic: GeodesicLeg
    true_course_deg: float
    distance_nm: float


@dataclass(frozen=True)
class _PhaseEnvironment:
    weather: WeatherResult | None
    wind_direction_deg_from: float | None
    wind_speed_kt: float | None
    temperature_c: float | None
    weather_metadata: dict[str, Any]
    weather_warnings: tuple[str, ...]
    altitude_ft_msl: float
    pressure_altitude_exact_ft: float
    pressure_altitude_planning_ft: float


@dataclass(frozen=True)
class _LegEnvironment:
    geometry: _Geometry
    phase_environments: dict[FlightPhase, _PhaseEnvironment]
    pressure_altitude_exact_ft: float
    pressure_altitude_planning_ft: float

    def for_phase(self, phase: FlightPhase) -> _PhaseEnvironment:
        return self.phase_environments.get(
            phase,
            self.phase_environments[self.geometry.section.phase],
        )


@dataclass(frozen=True)
class _ClimbPlan:
    source_section_id: UUID
    duration_seconds: float
    fuel_gal: float
    tas_kt: float
    route_end_distance_nm: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _DescentPlan:
    source_section_id: UUID
    duration_seconds: float
    route_start_distance_nm: float
    route_end_distance_nm: float
    cruise_cas_kt: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _RouteTraversal:
    distance_nm: float | None
    route_exhausted: bool = False


@dataclass
class _IterationResult:
    sections: list[SectionResult]
    display_rows: list[NavLogDisplayRow]
    issues: list[Issue]
    representative_times: dict[str, datetime]
    phases: list[FlightPhase]
    section_fuels: list[float | None]
    derived_points: list[DerivedRoutePoint]


def _automatic(
    value: Any,
    state: ValueState = ValueState.AUTO,
    metadata: dict[str, Any] | None = None,
    warnings: tuple[str, ...] = (),
) -> AdoptedValue[Any]:
    return AdoptedValue(
        automatic_value=value,
        automatic_status=state,
        automatic_metadata=metadata or {},
        adopted_source=AdoptedSource.AUTOMATIC if value is not None else None,
        warnings=warnings,
    )


def _manual_or_automatic(
    automatic: Any,
    manual: Any,
    *,
    state: ValueState = ValueState.AUTO,
    metadata: dict[str, Any] | None = None,
    warnings: tuple[str, ...] = (),
) -> AdoptedValue[Any]:
    return AdoptedValue(
        automatic_value=automatic,
        automatic_status=state if automatic is not None else ValueState.UNAVAILABLE,
        automatic_metadata=metadata or {},
        manual_override=manual,
        adopted_source=(
            AdoptedSource.MANUAL
            if manual is not None
            else AdoptedSource.AUTOMATIC
            if automatic is not None
            else None
        ),
        warnings=warnings,
    )


def _unavailable(reason_code: str = "AUTOMATIC_VALUE_UNAVAILABLE") -> AdoptedValue[Any]:
    return AdoptedValue(
        automatic_status=ValueState.UNAVAILABLE,
        automatic_metadata={"reason_code": reason_code},
    )


class CalculationService:
    def __init__(
        self,
        airports: AirportRepository,
        performance: PerformanceRepository,
        policies: CalculationPolicies | None = None,
        forecast_service: ForecastService | None = None,
        *,
        expected_rjfm_reference_revision: str | None = None,
        expected_rjfm_reference_content_fingerprint: str | None = None,
    ):
        self.airports = airports
        self.performance = performance
        self.policies = policies or CalculationPolicies()
        self.forecast_service = forecast_service or ForecastService()
        self.expected_rjfm_reference_revision = expected_rjfm_reference_revision
        self.expected_rjfm_reference_content_fingerprint = (
            expected_rjfm_reference_content_fingerprint
        )
        self.last_weather_requests: list[WeatherRequest] = []
        self.last_weather_results: list[WeatherResult] = []
        self.last_forecast_metadata: dict[str, Any] = {}

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

    def _load_planning_state(
        self,
        project: Project,
        issues: list[Issue],
    ) -> tuple[PersistedUiState | None, ArrivalAltitudeResult | None]:
        if "snapshot_effective_issues" in project.metadata:
            issues.append(
                self._blocker(
                    "PROJECT_STATE_INVALID",
                    "編集可能ProjectにSnapshot専用状態が含まれています。",
                )
            )
        raw_state = project.metadata.get("ui_state")
        if raw_state is None:
            return None, None
        try:
            ui_state = load_persisted_ui_state(raw_state)
        except (TypeError, ValueError) as error:
            issues.append(
                Issue(
                    code="PROJECT_STATE_INVALID",
                    severity=IssueSeverity.BLOCKER,
                    message="保存済みUI状態を安全に読み込めません。",
                    metadata={"reason": str(error)},
                )
            )
            return None, None
        arrival = calculate_arrival_altitude(project, ui_state)
        issues.extend(arrival.issues)
        return ui_state, arrival.result

    def _selected_airports(
        self,
        project: Project,
        ui_state: PersistedUiState | None,
    ) -> tuple[Airport, Airport]:
        snapshot = None if ui_state is None else ui_state.reference_data_snapshot
        if snapshot is None:
            return (
                self.airports.get(project.departure_airport_id),
                self.airports.get(project.destination_airport_id),
            )
        if (
            snapshot.departure_airport.id != project.departure_airport_id
            or snapshot.destination_airport.id != project.destination_airport_id
        ):
            raise KeyError("Project airport IDs do not match the saved snapshot")
        return (
            self._airport_from_selection(snapshot.departure_airport),
            self._airport_from_selection(snapshot.destination_airport),
        )

    def calculate(
        self,
        project: Project,
        provider: WeatherProvider,
        destination_wind: DestinationWindForecast | None = None,
        progress: Callable[[int, str], None] | None = None,
    ) -> CalculationOutcome:
        report = progress or (lambda _percent, _message: None)
        report(15, "経路データを準備しています。")
        working = project.model_copy(deep=True)
        issues: list[Issue] = []
        ui_state, arrival_altitude = self._load_planning_state(working, issues)
        rjfm_departure_plan = None if ui_state is None else ui_state.rjfm_departure_plan
        rjfm_plan_rejection_reason: str | None = None
        if rjfm_departure_plan is not None:
            if (
                self.expected_rjfm_reference_revision is None
                or self.expected_rjfm_reference_content_fingerprint is None
            ):
                rjfm_plan_rejection_reason = "CURRENT_REFERENCE_IDENTITY_UNAVAILABLE"
            elif (
                rjfm_departure_plan.reference_revision
                != self.expected_rjfm_reference_revision
                or rjfm_departure_plan.reference_content_fingerprint
                != self.expected_rjfm_reference_content_fingerprint
            ):
                rjfm_plan_rejection_reason = "REFERENCE_IDENTITY_MISMATCH"
            elif not rjfm_plan_matches_project(working, rjfm_departure_plan):
                rjfm_plan_rejection_reason = "ROUTE_INPUT_MISMATCH"
        if rjfm_departure_plan is not None and rjfm_plan_rejection_reason is not None:
            issues.append(
                Issue(
                    code="RJFM_DEPARTURE_PLAN_STALE",
                    severity=IssueSeverity.BLOCKER,
                    message=(
                        "保存済みRJFM出発例外を現在の経路・参照パックと照合できません。"
                        "現在の参照データで経路を再正規化してから再計算してください。"
                    ),
                    metadata={
                        "reason": rjfm_plan_rejection_reason,
                        "reference_revision": rjfm_departure_plan.reference_revision,
                        "reference_content_fingerprint": (
                            rjfm_departure_plan.reference_content_fingerprint
                        ),
                        "expected_reference_revision": (
                            self.expected_rjfm_reference_revision
                        ),
                        "expected_reference_content_fingerprint": (
                            self.expected_rjfm_reference_content_fingerprint
                        ),
                    },
                )
            )
            rjfm_departure_plan = None
        arrival_altitude_ft_msl = (
            None if arrival_altitude is None else float(arrival_altitude.adopted_altitude_ft_msl)
        )
        check_point_computation = project_check_points(working)
        issues.extend(check_point_computation.issues)
        check_point_projections = list(check_point_computation.projections)
        check_point_names = {
            reference.id: reference.name for reference in working.visual_references
        }
        check_point_boundaries = tuple(
            RouteBoundary(
                distance_nm=projection.cumulative_distance_nm,
                label=f"CP:{check_point_names[projection.checkpoint_id]}",
            )
            for projection in check_point_projections
        )
        self.last_weather_requests = []
        self.last_weather_results = []
        self.last_forecast_metadata = {}
        geometries = self._build_geometry(working, issues)
        try:
            departure, destination = self._selected_airports(working, ui_state)
        except KeyError as error:
            issues.append(self._blocker("AIRPORT_DATA_UNAVAILABLE", str(error)))
            return self._empty_outcome(working, issues, check_point_projections, arrival_altitude)
        if not geometries:
            return self._empty_outcome(working, issues, check_point_projections, arrival_altitude)
        performance_validation_reason = self.performance.validation_issue_reason()
        if performance_validation_reason == "HASH_MISMATCH":
            issues.append(
                Issue(
                    code="PERFORMANCE_DATA_UNVERIFIED",
                    severity=IssueSeverity.BLOCKER,
                    message="性能manifestと実CSVのSHA-256が一致しません。",
                    metadata={
                        "reason": "HASH_MISMATCH",
                        "source_revision": self.performance.manifest.source_revision,
                        "validation_status": (self.performance.manifest.validation_status),
                    },
                )
            )
            return self._empty_outcome(
                working,
                issues,
                check_point_projections,
                arrival_altitude,
            )
        performance_problems = self.performance.readiness_problems(working.aircraft_profile_id)
        if performance_problems:
            issues.append(
                Issue(
                    code="PERFORMANCE_DATA_UNAVAILABLE",
                    severity=IssueSeverity.BLOCKER,
                    message=(
                        "選択機体に必要な性能データを解決できません: "
                        + " / ".join(performance_problems)
                    ),
                    metadata={
                        "reason": "RUNTIME_READINESS",
                        "aircraft_profile_id": working.aircraft_profile_id,
                        "details": list(performance_problems),
                    },
                )
            )
            return self._empty_outcome(
                working,
                issues,
                check_point_projections,
                arrival_altitude,
            )
        departure_gap_nm = geodesic_leg(
            departure.latitude_deg,
            departure.longitude_deg,
            geometries[0].start.latitude_deg,
            geometries[0].start.longitude_deg,
        ).distance_nm
        destination_gap_nm = geodesic_leg(
            geometries[-1].end.latitude_deg,
            geometries[-1].end.longitude_deg,
            destination.latitude_deg,
            destination.longitude_deg,
        ).distance_nm
        if departure_gap_nm > 0.5 or destination_gap_nm > 0.5:
            issues.append(
                Issue(
                    code="ROUTE_AIRPORT_ENDPOINT_MISMATCH",
                    severity=IssueSeverity.BLOCKER,
                    message=("経路の先頭・末尾が選択した出発地・目的地と一致しません。"),
                    metadata={
                        "departure_gap_nm": departure_gap_nm,
                        "destination_gap_nm": destination_gap_nm,
                        "tolerance_nm": 0.5,
                    },
                )
            )
            return self._empty_outcome(working, issues, check_point_projections, arrival_altitude)

        report(25, "気象データを準備しています。")
        initial_requirement = self.forecast_service.build_initial_requirement(working)
        selected_run_id = self._select_and_prepare_run(
            working,
            provider,
            initial_requirement,
            issues,
        )
        if selected_run_id is None:
            return self._empty_outcome(working, issues, check_point_projections, arrival_altitude)

        previous_times: dict[str, datetime] = {}
        iteration_records: list[IterationRecord] = []
        final: _IterationResult | None = None
        qnh_value: AdoptedValue[float] = _manual_or_automatic(
            None,
            working.manual_qnh_hpa,
        )
        converged = False
        for iteration in range(1, self.policies.max_iterations + 1):
            report(
                30 + round((iteration - 1) * 45 / self.policies.max_iterations),
                f"気象を反映して経路を計算しています（{iteration}/{self.policies.max_iterations}）。",
            )
            requests = self._weather_requests(
                working,
                departure,
                destination,
                geometries,
                previous_times,
                arrival_altitude_ft_msl,
            )
            results = self._query_weather(provider, selected_run_id, requests, issues)
            qnh_value = self._adopt_qnh(working, results)
            iteration_result = self._calculate_iteration(
                working,
                departure,
                destination,
                geometries,
                results,
                check_point_boundaries,
                arrival_altitude,
                destination_wind,
                rjfm_departure_plan,
            )
            final = iteration_result
            delta = self._maximum_time_delta(previous_times, iteration_result.representative_times)
            iteration_records.append(
                IterationRecord(
                    iteration=iteration,
                    max_time_delta_seconds=delta,
                    representative_times_utc=iteration_result.representative_times,
                )
            )
            if previous_times and delta is not None and delta < self.policies.convergence_seconds:
                converged = True
                break
            previous_times = iteration_result.representative_times

        if final is None:
            outcome = self._empty_outcome(
                working,
                self._deduplicate_issues(issues),
                check_point_projections,
                arrival_altitude,
            )
            return outcome.model_copy(
                update={
                    "selected_forecast_run_id": selected_run_id,
                    "qnh_hpa": qnh_value,
                    "iterations": iteration_records,
                }
            )
        report(80, "計算結果を検証しています。")
        issues.extend(final.issues)
        if not converged:
            issues.append(
                Issue(
                    code="ITERATION_NOT_CONVERGED",
                    severity=IssueSeverity.WARNING,
                    message="5回以内に代表時刻が30秒未満へ収束しませんでした。",
                    acknowledgement_required=True,
                )
            )
        derived_points = final.derived_points
        issues.extend(self._flight_phase_sequence_issues(geometries))
        issues.extend(self._calculation_output_completeness_issues(final.sections))
        issues.extend(
            self._missing_derived_phase_point_issues(
                geometries,
                derived_points,
            )
        )

        if selected_run_id is not None:
            final_requirement = self.forecast_service.build_final_requirement(
                working,
                tuple(final.representative_times.values()),
            )
            run_status = provider.inspect_run_status(selected_run_id, final_requirement)
            if not run_status.selected_run_covers_requirement:
                issues.append(
                    self._blocker(
                        "FORECAST_RUN_OUT_OF_COVERAGE",
                        "最終経路時刻を選択済みForecast Runが覆いません。再選択が必要です。",
                    )
                )

        fuel_plan = build_fuel_plan(
            working.total_usable_fuel_gal,
            final.phases,
            final.section_fuels,
            working.tgl_count,
        )
        if fuel_plan.extra_gal is not None and fuel_plan.extra_gal < 0:
            issues.append(
                self._blocker("INSUFFICIENT_FUEL", "搭載燃料が最小必要燃料を下回ります。")
            )
        issues = self._deduplicate_issues(issues)
        project_status = self._status(working, issues)
        report(88, "燃料計画とNAV LOGを仕上げています。")
        return CalculationOutcome(
            project_id=working.id,
            selected_forecast_run_id=selected_run_id,
            qnh_hpa=qnh_value,
            sections=final.sections,
            display_rows=final.display_rows,
            derived_points=derived_points,
            check_point_projections=check_point_projections,
            arrival_altitude=arrival_altitude,
            fuel_plan=fuel_plan,
            issues=issues,
            iterations=iteration_records,
            converged=converged,
            status=project_status,
            policy_version=self.policies.version,
            performance_table_version=self.performance.manifest.source_revision,
        )

    def _build_geometry(self, project: Project, issues: list[Issue]) -> list[_Geometry]:
        if len(project.route_nodes) < 2 or not project.sections:
            issues.append(self._blocker("ROUTE_INCOMPLETE", "経路とSectionを確定してください。"))
            return []
        ordered_nodes = project.ordered_nodes()
        ordered_sections = project.ordered_sections()
        if (
            len(ordered_sections) != len(ordered_nodes) - 1
            or len({node.sequence for node in ordered_nodes}) != len(ordered_nodes)
            or len({section.sequence for section in ordered_sections}) != len(ordered_sections)
            or any(
                section.from_node_id != start.id or section.to_node_id != end.id
                for section, (start, end) in zip(
                    ordered_sections,
                    zip(ordered_nodes, ordered_nodes[1:], strict=False),
                    strict=False,
                )
            )
        ):
            issues.append(
                self._blocker(
                    "ROUTE_INCOMPLETE",
                    "Route NodeとSectionが先頭から末尾まで一続きになっていません。",
                )
            )
            return []
        nodes = {node.id: node for node in ordered_nodes}
        geometries: list[_Geometry] = []
        for section in ordered_sections:
            try:
                start, end = nodes[section.from_node_id], nodes[section.to_node_id]
            except KeyError:
                issues.append(
                    self._blocker("ROUTE_INCOMPLETE", "Section端点が存在しません.", section.id)
                )
                continue
            leg = geodesic_leg(
                start.latitude_deg,
                start.longitude_deg,
                end.latitude_deg,
                end.longitude_deg,
            )
            adopted_distance = (
                start.manual_distance_nm
                if start.manual_distance_nm is not None
                else leg.distance_nm
            )
            if (
                not isfinite(leg.distance_nm)
                or leg.distance_nm <= 1e-9
                or not isfinite(adopted_distance)
                or adopted_distance <= 0
            ):
                issues.append(
                    self._blocker(
                        "ROUTE_INCOMPLETE",
                        "同一座標または無効な距離のRoute Legは計算できません。",
                        section.id,
                    )
                )
                return []
            geometries.append(
                _Geometry(
                    section,
                    start,
                    end,
                    leg,
                    start.manual_true_course_deg
                    if start.manual_true_course_deg is not None
                    else leg.initial_true_course_deg,
                    start.manual_distance_nm
                    if start.manual_distance_nm is not None
                    else leg.distance_nm,
                )
            )
        return geometries

    def _select_and_prepare_run(
        self,
        project: Project,
        provider: WeatherProvider,
        requirement: ForecastRequirement,
        issues: list[Issue],
    ) -> str | None:
        try:
            initial_time_utc: datetime | None = None
            if project.selected_forecast_run_id is None:
                run = provider.resolve_run(requirement)
                selected_run_id = run.id
                initial_time_utc = run.initial_time_utc.astimezone(timezone.utc)
            else:
                status = provider.inspect_run_status(
                    project.selected_forecast_run_id,
                    requirement,
                )
                selected_run_id = project.selected_forecast_run_id
                try:
                    initial_time_utc = datetime.strptime(selected_run_id, "%Y%m%d%H%M%S").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    initial_time_utc = None
                if not status.selected_run_covers_requirement:
                    issues.append(
                        self._blocker(
                            "FORECAST_RUN_OUT_OF_COVERAGE",
                            "保存済みForecast Runが必要時間帯を覆いません。",
                        )
                    )
                    return None
                if status.update_available:
                    issues.append(
                        Issue(
                            code="FORECAST_UPDATE_AVAILABLE",
                            severity=IssueSeverity.WARNING,
                            message=(
                                "新しい互換Forecast Runがあります。保存済みRunは変更していません。"
                            ),
                            metadata={
                                "selected_run_id": selected_run_id,
                                "latest_compatible_run_id": (status.latest_compatible_run_id),
                            },
                        )
                    )
            prepared = provider.prepare_run(selected_run_id, requirement)
            metadata = dict(prepared.metadata)
            metadata["forecast_run_id"] = selected_run_id
            if initial_time_utc is not None:
                metadata["initial_time_utc"] = initial_time_utc.isoformat().replace("+00:00", "Z")
            self.last_forecast_metadata = metadata
            return selected_run_id
        except Exception as error:
            issues.append(self._blocker("FORECAST_PREPARE_FAILED", str(error)))
            return None

    @staticmethod
    def _weather_phases(section: NavSection) -> tuple[FlightPhase, ...]:
        if section.phase == FlightPhase.VISUAL_ARRIVAL:
            return (section.phase,)
        return (
            section.phase,
            *(
                phase
                for phase in (
                    FlightPhase.CLIMB,
                    FlightPhase.CRUISE,
                    FlightPhase.DESCENT,
                )
                if phase != section.phase
            ),
        )

    @staticmethod
    def _weather_request_id(section: NavSection, phase: FlightPhase) -> str:
        suffix = "aloft" if phase == section.phase else phase.value.lower()
        return f"section:{section.id}:{suffix}"

    def _weather_requests(
        self,
        project: Project,
        departure: Airport,
        destination: Airport,
        geometries: list[_Geometry],
        previous_times: dict[str, datetime],
        arrival_altitude_ft_msl: float | None = None,
    ) -> list[WeatherRequest]:
        requests: list[WeatherRequest] = []
        elapsed = 0.0
        for index, geometry in enumerate(geometries):
            default_seconds = (
                geometry.distance_nm
                / (geometry.section.manual_tas_kt or ForecastService.estimate_speed_kt)
                * 3600.0
            )
            valid_time = previous_times.get(
                str(geometry.section.id),
                project.planned_departure_time_jst
                + timedelta(seconds=elapsed + default_seconds / 2),
            )
            elapsed += default_seconds
            for phase in self._weather_phases(geometry.section):
                altitude_ft_msl, altitude_metadata = self._weather_request_representative_altitude(
                    index,
                    geometries,
                    departure,
                    destination,
                    arrival_altitude_ft_msl,
                    phase,
                )
                requests.append(
                    WeatherRequest(
                        request_id=self._weather_request_id(geometry.section, phase),
                        kind=WeatherRequestKind.ALOFT,
                        latitude_deg=geometry.geodesic.midpoint_latitude_deg,
                        longitude_deg=geometry.geodesic.midpoint_longitude_deg,
                        valid_time_utc=valid_time,
                        altitude_ft_msl=altitude_ft_msl,
                        metadata=altitude_metadata,
                    )
                )
        last_geometry = geometries[-1]
        last_default_seconds = (
            last_geometry.distance_nm
            / (last_geometry.section.manual_tas_kt or ForecastService.estimate_speed_kt)
            * 3600.0
        )
        last_midpoint_time = previous_times.get(str(last_geometry.section.id))
        arrival_time = (
            project.planned_departure_time_jst + timedelta(seconds=elapsed)
            if last_midpoint_time is None
            else last_midpoint_time + timedelta(seconds=last_default_seconds / 2.0)
        )
        requests.append(
            WeatherRequest(
                request_id="departure:surface",
                kind=WeatherRequestKind.ALOFT,
                latitude_deg=departure.latitude_deg,
                longitude_deg=departure.longitude_deg,
                valid_time_utc=project.planned_departure_time_jst,
                altitude_ft_msl=float(departure.elevation_ft_msl),
                metadata={
                    "source_rule": "NAV2_V6_DEPARTURE_PARENT_ROW",
                    "phase": "DEPARTURE",
                    "representative_altitude_policy": "AIRPORT_ELEVATION_MSL",
                    "representative_altitude_ft_msl": float(departure.elevation_ft_msl),
                    "airport_id": departure.id,
                },
            )
        )
        requests.append(
            WeatherRequest(
                request_id="destination:surface",
                kind=WeatherRequestKind.ALOFT,
                latitude_deg=destination.latitude_deg,
                longitude_deg=destination.longitude_deg,
                valid_time_utc=arrival_time,
                altitude_ft_msl=float(destination.elevation_ft_msl),
                metadata={
                    "source_rule": "NAV2_V5_DESTINATION_FINAL_ROW",
                    "phase": "DESTINATION",
                    "representative_altitude_policy": "AIRPORT_ELEVATION_MSL",
                    "representative_altitude_ft_msl": float(destination.elevation_ft_msl),
                    "airport_id": destination.id,
                },
            )
        )
        return requests

    @classmethod
    def _weather_request_representative_altitude(
        cls,
        index: int,
        geometries: list[_Geometry],
        departure: Airport,
        destination: Airport,
        arrival_altitude_ft_msl: float | None,
        phase: FlightPhase | None = None,
    ) -> tuple[float, dict[str, Any]]:
        section = geometries[index].section
        phase = section.phase if phase is None else phase
        source_rule = "CAC_REV19_8-(3)_5_AND_6"
        if phase == FlightPhase.CRUISE:
            altitude = float(section.planned_altitude_ft_msl)
            return altitude, {
                "source_rule": source_rule,
                "phase": phase.value,
                "representative_altitude_policy": ("SECTION_PLANNED_CRUISE_ALTITUDE_MSL"),
                "representative_altitude_ft_msl": altitude,
                "altitude_basis": "CRUISE_ALTITUDE",
            }

        if phase == FlightPhase.CLIMB:
            climb_index = next(
                (
                    position
                    for position, geometry in enumerate(geometries)
                    if geometry.section.phase == FlightPhase.CLIMB
                ),
                index,
            )
            lower_altitude = float(departure.elevation_ft_msl)
            upper_altitude = float(geometries[climb_index].section.planned_altitude_ft_msl)
            altitude_basis = "DEPARTURE_AIRPORT_ELEVATION_AND_CRUISE_ALTITUDE"
        elif phase == FlightPhase.DESCENT:
            lower_altitude = float(
                cls._descent_target_altitude(
                    index,
                    geometries,
                    destination,
                    arrival_altitude_ft_msl,
                )
            )
            descent_index = next(
                (
                    position
                    for position, geometry in enumerate(geometries)
                    if geometry.section.phase == FlightPhase.DESCENT
                ),
                index,
            )
            cruise_index = max(0, descent_index - 1)
            upper_altitude = float(
                geometries[cruise_index].section.planned_altitude_ft_msl
            )
            altitude_basis = "CRUISE_ALTITUDE_AND_VISUAL_REPORTING_POINT_ALTITUDE"
        else:
            lower_altitude = float(destination.elevation_ft_msl)
            upper_altitude = float(
                section.planned_altitude_ft_msl
                if arrival_altitude_ft_msl is None
                else arrival_altitude_ft_msl
            )
            altitude_basis = "VISUAL_REPORTING_POINT_ALTITUDE_AND_DESTINATION_ELEVATION"
        representative_altitude = (lower_altitude + upper_altitude) / 2.0
        return representative_altitude, {
            "source_rule": source_rule,
            "phase": phase.value,
            "representative_altitude_policy": ("ARITHMETIC_MEAN_OF_ENDPOINT_ALTITUDES_MSL"),
            "lower_altitude_ft_msl": lower_altitude,
            "upper_altitude_ft_msl": upper_altitude,
            "representative_altitude_ft_msl": representative_altitude,
            "altitude_basis": altitude_basis,
        }

    def _query_weather(
        self,
        provider: WeatherProvider,
        selected_run_id: str | None,
        requests: list[WeatherRequest],
        issues: list[Issue],
    ) -> list[WeatherResult]:
        self.last_weather_requests.extend(requests)
        if selected_run_id is None:
            return []
        try:
            results = list(provider.query_batch(selected_run_id, requests))
        except Exception as error:
            issues.append(self._blocker("WEATHER_QUERY_FAILED", str(error)))
            return []
        if {item.request_id for item in results} != {item.request_id for item in requests}:
            issues.append(
                self._blocker(
                    "WEATHER_BATCH_MISMATCH",
                    "WeatherProviderのrequest ID対応が一致しません。",
                )
            )
            return []
        requests_by_id = {request.request_id: request for request in requests}
        results = [
            result.model_copy(
                update={
                    "metadata": result.metadata
                    | {"request_metadata": requests_by_id[result.request_id].metadata}
                }
            )
            for result in results
        ]
        self.last_weather_results.extend(results)
        return results

    @staticmethod
    def _adopt_qnh(project: Project, results: list[WeatherResult]) -> AdoptedValue[float]:
        result = next((item for item in results if item.request_id == "project:qnh"), None)
        automatic = None
        metadata: dict[str, Any] = {}
        warnings: tuple[str, ...] = ()
        if result is not None:
            metadata = result.metadata | {
                "values": result.values,
                "availability": result.availability.value,
                "reason_code": result.reason_code,
            }
            values_label = result.values.get("label")
            metadata_label = result.metadata.get("label")
            label = (
                values_label
                if isinstance(values_label, str) and values_label.strip()
                else metadata_label
            )
            if isinstance(label, str) and label.strip():
                metadata["label"] = label.strip()
            warnings = result.warnings
            if result.availability == Availability.AVAILABLE:
                value = result.values.get("qnh_hpa")
                automatic = float(value) if isinstance(value, (int, float)) else None
        return _manual_or_automatic(
            automatic,
            project.manual_qnh_hpa,
            metadata=metadata,
            warnings=warnings,
        )

    def _build_leg_environments(
        self,
        geometries: list[_Geometry],
        weather_by_id: dict[str, WeatherResult],
        issues: list[Issue],
        departure: Airport,
        destination: Airport,
        arrival_altitude_ft_msl: float | None,
    ) -> list[_LegEnvironment]:
        environments: list[_LegEnvironment] = []
        for index, geometry in enumerate(geometries):
            section = geometry.section
            phase_environments: dict[FlightPhase, _PhaseEnvironment] = {}
            for phase in self._weather_phases(section):
                weather = weather_by_id.get(self._weather_request_id(section, phase))
                wind_direction, wind_speed, temperature, metadata, warnings = self._section_weather(
                    section, weather, phase
                )
                altitude_ft_msl, _ = self._weather_request_representative_altitude(
                    index,
                    geometries,
                    departure,
                    destination,
                    arrival_altitude_ft_msl,
                    phase,
                )
                # Project policy PA=MSL: representative MSL altitude is used
                # directly for airspeed/performance calculations.
                exact_phase_pa = altitude_ft_msl
                phase_environments[phase] = _PhaseEnvironment(
                    weather=weather,
                    wind_direction_deg_from=wind_direction,
                    wind_speed_kt=wind_speed,
                    temperature_c=temperature,
                    weather_metadata=metadata,
                    weather_warnings=warnings,
                    altitude_ft_msl=altitude_ft_msl,
                    pressure_altitude_exact_ft=exact_phase_pa,
                    pressure_altitude_planning_ft=exact_phase_pa,
                )
                if temperature is None:
                    issues.append(
                        self._blocker(
                            "TEMPERATURE_UNAVAILABLE",
                            "気温を取得できません。手動入力が必要です。",
                            section.id,
                            metadata={"phase": phase.value},
                        )
                    )
                if phase != FlightPhase.VISUAL_ARRIVAL and (
                    wind_speed is None or (wind_speed > 0 and wind_direction is None)
                ):
                    issues.append(
                        self._blocker(
                            "WIND_UNAVAILABLE",
                            "風を取得できません。手動入力が必要です。",
                            section.id,
                            metadata={"phase": phase.value},
                        )
                    )
            target_altitude_ft_msl = (
                arrival_altitude_ft_msl
                if (
                    section.phase == FlightPhase.VISUAL_ARRIVAL
                    and arrival_altitude_ft_msl is not None
                )
                else section.planned_altitude_ft_msl
            )
            exact_pa = float(target_altitude_ft_msl)
            environments.append(
                _LegEnvironment(
                    geometry=geometry,
                    phase_environments=phase_environments,
                    pressure_altitude_exact_ft=exact_pa,
                    pressure_altitude_planning_ft=exact_pa,
                )
            )
        return environments

    @staticmethod
    def _route_leg_offsets(
        geometries: list[_Geometry],
    ) -> list[tuple[float, float]]:
        offsets: list[tuple[float, float]] = []
        cumulative = 0.0
        for geometry in geometries:
            start = cumulative
            cumulative += geometry.distance_nm
            offsets.append((start, cumulative))
        return offsets

    def _distance_after_duration(
        self,
        environments: list[_LegEnvironment],
        duration_seconds: float,
        tas_kt: float,
        issues: list[Issue],
        section_id: UUID,
    ) -> _RouteTraversal:
        remaining_seconds = duration_seconds
        route_distance = 0.0
        for environment in environments:
            phase_environment = environment.for_phase(FlightPhase.CLIMB)
            wind_speed = phase_environment.wind_speed_kt
            if wind_speed is None:
                return _RouteTraversal(None)
            try:
                solution = solve_wind_triangle(
                    environment.geometry.true_course_deg,
                    tas_kt,
                    phase_environment.wind_direction_deg_from,
                    wind_speed,
                )
            except WindTriangleError as error:
                issues.append(
                    self._blocker(
                        "WIND_TRIANGLE_FAILED",
                        str(error),
                        section_id,
                    )
                )
                return _RouteTraversal(None)
            full_leg_seconds = environment.geometry.distance_nm / solution.ground_speed_kt * 3600.0
            if remaining_seconds <= full_leg_seconds + 1e-9:
                return _RouteTraversal(
                    route_distance + solution.ground_speed_kt * remaining_seconds / 3600.0
                )
            remaining_seconds -= full_leg_seconds
            route_distance += environment.geometry.distance_nm
        return _RouteTraversal(None, route_exhausted=True)

    def _build_climb_plan(
        self,
        departure: Airport,
        environments: list[_LegEnvironment],
        climb_calculator: ClimbCalculator,
        unique_weights: list[float],
        performance_usable: bool,
        issues: list[Issue],
    ) -> _ClimbPlan | None:
        climb_environment = next(
            (
                environment
                for environment in environments
                if environment.geometry.section.phase == FlightPhase.CLIMB
            ),
            None,
        )
        if climb_environment is None or not performance_usable:
            return None
        section = climb_environment.geometry.section
        climb_phase_environment = climb_environment.for_phase(FlightPhase.CLIMB)
        temperature = climb_phase_environment.temperature_c
        if temperature is None:
            return None
        if len(unique_weights) != 1:
            issues.append(
                self._blocker(
                    "CLIMB_WEIGHT_UNRESOLVED",
                    "上昇表に複数重量があり、採用重量を確定できません。",
                    section.id,
                )
            )
            return None
        try:
            departure_pa = float(departure.elevation_ft_msl)
            climb = climb_calculator.calculate(
                departure_pa,
                climb_environment.pressure_altitude_exact_ft,
                temperature,
                unique_weights[0],
            )
        except ClimbPerformanceError as error:
            issues.append(
                self._blocker(
                    "CLIMB_PERFORMANCE_UNAVAILABLE",
                    str(error),
                    section.id,
                )
            )
            return None
        representative_pressure_altitude_ft = (
            departure_pa + climb_environment.pressure_altitude_exact_ft
        ) / 2.0
        if section.manual_tas_kt is None:
            tas_kt = tas_from_cas(
                111.0,
                representative_pressure_altitude_ft,
                temperature,
            )
            tas_method = "CAC_REV19_8-(3)_5_(1)_CAS_111_AT_REPRESENTATIVE_PRESSURE_ALTITUDE"
            cas_kt: float | None = 111.0
        else:
            tas_kt = section.manual_tas_kt
            tas_method = "MANUAL_OVERRIDE"
            cas_kt = None
        duration_seconds = climb.time_min * 60.0
        traversal = self._distance_after_duration(
            environments,
            duration_seconds,
            tas_kt,
            issues,
            section.id,
        )
        route_end_distance = traversal.distance_nm
        if route_end_distance is None:
            if traversal.route_exhausted:
                issues.append(
                    self._blocker(
                        "RCA_OUTSIDE_ROUTE",
                        "上昇完了までの飛行距離が計画経路端を越えます。",
                        section.id,
                    )
                )
            return None
        for warning in climb.warnings:
            issues.append(
                Issue(
                    code=warning,
                    severity=IssueSeverity.WARNING,
                    message="上昇表の高度軸を表端から500 ft以内で外挿しました。",
                    section_id=section.id,
                )
            )
        return _ClimbPlan(
            source_section_id=section.id,
            duration_seconds=duration_seconds,
            fuel_gal=climb.fuel_gal,
            tas_kt=tas_kt,
            route_end_distance_nm=route_end_distance,
            metadata={
                "type": "climb",
                "weight_lb": unique_weights[0],
                "table_distance_nm": climb.distance_nm,
                "route_distance_nm": route_end_distance,
                "planned_duration_seconds": duration_seconds,
                "planned_fuel_gal": climb.fuel_gal,
                "representative_tas_kt": tas_kt,
                "representative_pressure_altitude_ft": (representative_pressure_altitude_ft),
                "midpoint_temperature_c": temperature,
                "cas_kt": cas_kt,
                "tas_method": tas_method,
                "poh_table_distance_reference_nm": climb.distance_nm,
                "poh_table_distance_usage": "REFERENCE_ONLY",
                "temperature_policy": climb.temperature_policy,
                "representative_altitude_ft": (climb.representative_altitude_ft),
                "representative_isa_temperature_c": (climb.representative_isa_temperature_c),
                "temperature_delta_above_standard_c": (climb.temperature_delta_above_standard_c),
                "temperature_adjustment_factor": (climb.temperature_adjustment_factor),
                "warnings": climb.warnings,
                "boundary_method": "time-and-leg-specific-wind",
            },
        )

    def _preview_last_cruise_cas(
        self,
        environments: list[_LegEnvironment],
        end_index: int,
        cruise_policy: CruisePerformanceSelectionPolicy,
    ) -> float | None:
        last_cas: float | None = None
        for environment in environments[:end_index]:
            phase = environment.geometry.section.phase
            # The immediately preceding physical leg supplies the cruise CAS;
            # a DESCENT leg's legacy altitude is not an intermediate constraint.
            if phase in {FlightPhase.DESCENT, FlightPhase.VISUAL_ARRIVAL}:
                continue
            phase_environment = environment.for_phase(FlightPhase.CRUISE)
            temperature = phase_environment.temperature_c
            wind_speed = phase_environment.wind_speed_kt
            if temperature is None or wind_speed is None:
                continue
            section = environment.geometry.section
            tas = section.manual_tas_kt
            if tas is None:
                try:
                    selected = cruise_policy.select(
                        phase_environment.pressure_altitude_planning_ft,
                        temperature
                        - isa_temperature_c(phase_environment.pressure_altitude_planning_ft),
                        environment.geometry.distance_nm,
                        environment.geometry.true_course_deg,
                        phase_environment.wind_direction_deg_from,
                        wind_speed,
                    )
                except CruisePerformanceError:
                    continue
                tas = selected.row.ktas
            last_cas = cas_from_tas(
                tas,
                phase_environment.pressure_altitude_exact_ft,
                temperature,
            )
        return last_cas

    def _build_descent_plan(
        self,
        environments: list[_LegEnvironment],
        geometries: list[_Geometry],
        destination: Airport,
        cruise_policy: CruisePerformanceSelectionPolicy,
        issues: list[Issue],
        arrival_altitude_ft_msl: float | None,
    ) -> _DescentPlan | None:
        descent_index = next(
            (
                index
                for index, environment in enumerate(environments)
                if environment.geometry.section.phase == FlightPhase.DESCENT
            ),
            None,
        )
        if descent_index is None:
            return None
        descent_environment = environments[descent_index]
        section = descent_environment.geometry.section
        target_altitude = self._descent_target_altitude(
            descent_index,
            geometries,
            destination,
            arrival_altitude_ft_msl,
        )
        cruise_cas = self._preview_last_cruise_cas(
            environments,
            descent_index,
            cruise_policy,
        )
        if section.manual_tas_kt is None and cruise_cas is None:
            issues.append(
                self._blocker(
                    "DESCENT_TAS_UNAVAILABLE",
                    "降下TASの基準となる直前巡航CASを確定できません。",
                    section.id,
                )
            )
            return None

        cruise_altitude = float(
            geometries[descent_index - 1].section.planned_altitude_ft_msl
            if descent_index > 0
            else section.planned_altitude_ft_msl
        )
        altitude_difference = cruise_altitude - target_altitude
        if altitude_difference <= 0:
            issues.append(
                self._blocker(
                    "DESCENT_ALTITUDE_INVALID",
                    "巡航高度はVREP高度より高く設定してください。",
                    section.id,
                    metadata={
                        "cruise_altitude_ft_msl": cruise_altitude,
                        "vrep_altitude_ft_msl": target_altitude,
                    },
                )
            )
            return None

        # Miyazaki NAV2: vertical descent time plus one operational minute.
        vertical_descent_duration_seconds = altitude_difference / 500.0 * 60.0
        duration_seconds = vertical_descent_duration_seconds + 60.0
        common_environment = descent_environment.for_phase(FlightPhase.DESCENT)
        temperature = common_environment.temperature_c
        wind_speed = common_environment.wind_speed_kt
        if temperature is None or wind_speed is None:
            return None
        descent_tas = section.manual_tas_kt
        if descent_tas is None and cruise_cas is not None:
            descent_tas = tas_from_cas(
                cruise_cas,
                common_environment.pressure_altitude_exact_ft,
                temperature,
            )
        if descent_tas is None:
            return None

        def descent_ground_speed(index: int) -> float | None:
            try:
                return solve_wind_triangle(
                    environments[index].geometry.true_course_deg,
                    descent_tas,
                    common_environment.wind_direction_deg_from,
                    wind_speed,
                ).ground_speed_kt
            except WindTriangleError as error:
                issues.append(
                    self._blocker("WIND_TRIANGLE_FAILED", str(error), section.id)
                )
                return None

        offsets = self._route_leg_offsets(geometries)
        route_end_distance = offsets[descent_index][1]
        descent_gs = descent_ground_speed(descent_index)
        if descent_gs is None:
            return None
        descent_leg_seconds = geometries[descent_index].distance_nm / descent_gs * 3600.0
        if duration_seconds <= descent_leg_seconds + 1e-9:
            route_start_distance = route_end_distance - descent_gs * duration_seconds / 3600.0
            eoc_leg_index = descent_index
        else:
            previous_index = descent_index - 1
            remaining_seconds = duration_seconds - descent_leg_seconds
            if previous_index < 0:
                issues.append(
                    self._blocker(
                        "EOC_BEFORE_SUPPORTED_LEG",
                        "EOCが降下Legより前ですが、直前の巡航Legがありません。",
                        section.id,
                    )
                )
                return None
            previous_gs = descent_ground_speed(previous_index)
            if previous_gs is None:
                return None
            previous_leg_seconds = (
                geometries[previous_index].distance_nm / previous_gs * 3600.0
            )
            if remaining_seconds > previous_leg_seconds + 1e-9:
                issues.append(
                    self._blocker(
                        "EOC_BEFORE_SUPPORTED_LEG",
                        "EOCが直前の巡航Leg始点より前になります。",
                        section.id,
                        metadata={
                            "required_seconds": duration_seconds,
                            "descent_leg_seconds": descent_leg_seconds,
                            "previous_leg_seconds": previous_leg_seconds,
                        },
                    )
                )
                return None
            route_start_distance = (
                offsets[previous_index][1]
                - previous_gs * remaining_seconds / 3600.0
            )
            eoc_leg_index = previous_index

        if cruise_cas is None:
            phase_environment = descent_environment.for_phase(FlightPhase.DESCENT)
            cruise_cas = cas_from_tas(
                section.manual_tas_kt or 0.0,
                phase_environment.pressure_altitude_exact_ft,
                phase_environment.temperature_c or 0.0,
            )
        return _DescentPlan(
            source_section_id=section.id,
            duration_seconds=duration_seconds,
            route_start_distance_nm=route_start_distance,
            route_end_distance_nm=route_end_distance,
            cruise_cas_kt=cruise_cas,
            metadata={
                "type": "descent",
                "descent_rate_fpm": 500.0,
                "target_altitude_ft_msl": target_altitude,
                "cruise_altitude_ft_msl": cruise_altitude,
                "planned_duration_seconds": duration_seconds,
                "vertical_descent_duration_seconds": vertical_descent_duration_seconds,
                "operational_addition_seconds": 60.0,
                "eoc_source_section_id": str(geometries[eoc_leg_index].section.id),
                "cruise_cas_kt": cruise_cas,
                "fuel_flow_gph": 12.0,
                "boundary_method": "descent-leg-then-immediate-previous-leg-only",
            },
        )

    def _build_phase_segmentation(
        self,
        geometries: list[_Geometry],
        climb_plan: _ClimbPlan | None,
        descent_plan: _DescentPlan | None,
        additional_boundaries: tuple[RouteBoundary, ...],
        issues: list[Issue],
    ) -> PhaseSegmentation:
        physical_legs = tuple(
            PhysicalRouteLeg(
                source_index=index,
                source_id=geometry.section.id,
                start_name=geometry.start.name,
                start_latitude_deg=geometry.start.latitude_deg,
                start_longitude_deg=geometry.start.longitude_deg,
                end_name=geometry.end.name,
                end_latitude_deg=geometry.end.latitude_deg,
                end_longitude_deg=geometry.end.longitude_deg,
                adopted_distance_nm=geometry.distance_nm,
            )
            for index, geometry in enumerate(geometries)
        )
        climb_required = any(geometry.section.phase == FlightPhase.CLIMB for geometry in geometries)
        descent_required = any(
            geometry.section.phase == FlightPhase.DESCENT for geometry in geometries
        )
        visual_source_id = (
            geometries[-1].section.id
            if geometries and geometries[-1].section.phase == FlightPhase.VISUAL_ARRIVAL
            else None
        )

        def source_phase_fallback() -> PhaseSegmentation:
            try:
                fallback = split_route_into_phase_segments(
                    physical_legs,
                    visual_leg_source_id=visual_source_id,
                    additional_boundaries=additional_boundaries,
                )
            except PhaseSegmentationError as error:
                issues.append(
                    self._blocker(
                        "ROUTE_SEGMENTATION_INPUT_INVALID",
                        f"経路を安全な計算区間へ変換できません: {error}",
                    )
                )
                return PhaseSegmentation(
                    segments=(),
                    total_distance_nm=sum(geometry.distance_nm for geometry in geometries),
                )
            return fallback.__class__(
                segments=tuple(
                    replace(
                        segment,
                        phase=geometries[segment.source_index].section.phase,
                    )
                    for segment in fallback.segments
                ),
                total_distance_nm=fallback.total_distance_nm,
            )

        if (climb_required and climb_plan is None) or (descent_required and descent_plan is None):
            return source_phase_fallback()
        try:
            return split_route_into_phase_segments(
                physical_legs,
                rca_distance_nm=(None if climb_plan is None else climb_plan.route_end_distance_nm),
                eoc_distance_nm=(
                    None if descent_plan is None else descent_plan.route_start_distance_nm
                ),
                descent_end_distance_nm=(
                    None if descent_plan is None else descent_plan.route_end_distance_nm
                ),
                visual_leg_source_id=visual_source_id,
                additional_boundaries=additional_boundaries,
            )
        except PhaseSegmentationError as error:
            issues.append(
                self._blocker(
                    "PHASE_SEGMENTATION_FAILED",
                    f"RCA/EOCによる経路分割に失敗しました: {error}",
                )
            )
            return source_phase_fallback()

    @staticmethod
    def _label_rjfm_rca(
        segmentation: PhaseSegmentation,
        *,
        assumed: bool,
    ) -> PhaseSegmentation:
        point = segmentation.rca_point
        if point is None:
            return segmentation
        labeled = replace(
            point,
            label="UMK/RCA（仮定）" if assumed else "UMK/RCA",
            source_name=None if assumed else point.source_name,
        )

        def relabel(candidate: Any) -> Any:
            return labeled if "RCA" in candidate.markers else candidate

        return replace(
            segmentation,
            segments=tuple(
                replace(
                    segment,
                    start=relabel(segment.start),
                    end=relabel(segment.end),
                )
                for segment in segmentation.segments
            ),
            rca_point=labeled,
        )

    @staticmethod
    def _derived_points_from_segmentation(
        segmentation: PhaseSegmentation,
        boundary_times: dict[float, datetime],
    ) -> list[DerivedRoutePoint]:
        points: list[DerivedRoutePoint] = []
        if segmentation.rca_point is not None:
            containing = next(
                (
                    segment
                    for segment in segmentation.segments
                    if abs(segment.end_distance_nm - segmentation.rca_point.along_route_distance_nm)
                    <= 1e-9
                ),
                None,
            )
            if containing is not None and isinstance(containing.source_id, UUID):
                points.append(
                    DerivedRoutePoint(
                        type=DerivedPointType.RCA,
                        section_id=containing.source_id,
                        latitude_deg=segmentation.rca_point.latitude_deg,
                        longitude_deg=segmentation.rca_point.longitude_deg,
                        along_route_distance_nm=(segmentation.rca_point.along_route_distance_nm),
                        estimated_time_utc=boundary_times.get(
                            segmentation.rca_point.along_route_distance_nm
                        ),
                    )
                )
        if segmentation.eoc_point is not None:
            containing = next(
                (
                    segment
                    for segment in segmentation.segments
                    if abs(
                        segment.start_distance_nm - segmentation.eoc_point.along_route_distance_nm
                    )
                    <= 1e-9
                ),
                None,
            )
            if containing is not None and isinstance(containing.source_id, UUID):
                points.append(
                    DerivedRoutePoint(
                        type=DerivedPointType.EOC,
                        section_id=containing.source_id,
                        latitude_deg=segmentation.eoc_point.latitude_deg,
                        longitude_deg=segmentation.eoc_point.longitude_deg,
                        along_route_distance_nm=(segmentation.eoc_point.along_route_distance_nm),
                        estimated_time_utc=boundary_times.get(
                            segmentation.eoc_point.along_route_distance_nm
                        ),
                    )
                )
        return points

    def _calculate_iteration(
        self,
        project: Project,
        departure: Airport,
        destination: Airport,
        geometries: list[_Geometry],
        weather_results: list[WeatherResult],
        additional_boundaries: tuple[RouteBoundary, ...],
        arrival_altitude: ArrivalAltitudeResult | None,
        destination_wind: DestinationWindForecast | None,
        rjfm_departure_plan: RjfmDeparturePlan | None,
    ) -> _IterationResult:
        issues: list[Issue] = []
        arrival_altitude_ft_msl = (
            None if arrival_altitude is None else float(arrival_altitude.adopted_altitude_ft_msl)
        )
        weather_by_id = {item.request_id: item for item in weather_results}
        performance_usable = True
        try:
            self.performance.require_verified()
        except PerformanceDataError as error:
            performance_usable = False
            reason = self.performance.validation_issue_reason()
            if reason is None:
                issues.append(self._blocker("PERFORMANCE_DATA_UNAVAILABLE", str(error)))
            else:
                issues.append(
                    Issue(
                        code="PERFORMANCE_DATA_UNVERIFIED",
                        severity=IssueSeverity.BLOCKER,
                        message="検証済み性能データを選択してください。",
                        metadata={
                            "reason": reason,
                            "source_revision": (self.performance.manifest.source_revision),
                            "validation_status": (self.performance.manifest.validation_status),
                        },
                    )
                )
        climb_calculator = ClimbCalculator(
            self.performance.climb_rows,
            self.performance.manifest.climb_temperature_policy,
        )
        cruise_policy = CruisePerformanceSelectionPolicy(
            self.performance.cruise_rows,
            use_table_boundaries=True,
        )
        unique_weights = sorted({row.weight_lb for row in self.performance.climb_rows})
        environments = self._build_leg_environments(
            geometries,
            weather_by_id,
            issues,
            departure,
            destination,
            arrival_altitude_ft_msl,
        )
        climb_plan = self._build_climb_plan(
            departure,
            environments,
            climb_calculator,
            unique_weights,
            performance_usable,
            issues,
        )
        if climb_plan is not None and rjfm_departure_plan is not None:
            requested_rca_distance = float(rjfm_departure_plan.virtual_rca_distance_nm)
            route_distance = sum(geometry.distance_nm for geometry in geometries)
            if requested_rca_distance < route_distance - 1e-9:
                climb_plan = replace(
                    climb_plan,
                    route_end_distance_nm=requested_rca_distance,
                    metadata={
                        **climb_plan.metadata,
                        "boundary_method": "RJFM_UMK_FIXED_RCA",
                        "rjfm_rule_version": rjfm_departure_plan.rule_version,
                        "rjfm_main_route_mode": rjfm_departure_plan.main_route_mode.value,
                        "direct_leg_display_values_retained": True,
                        "ete_policy": "POH_CLIMB_TIME_TO_5500",
                    },
                )
            else:
                issues.append(
                    Issue(
                        code="RJFM_UMK_RCA_OUTSIDE_ROUTE",
                        severity=IssueSeverity.WARNING,
                        message=(
                            "UMK/RCA仮定距離が主経路内に収まらないため、通常RCAを表示します。"
                        ),
                        metadata={
                            "requested_rca_distance_nm": requested_rca_distance,
                            "route_distance_nm": route_distance,
                        },
                    )
                )
        descent_plan = self._build_descent_plan(
            environments,
            geometries,
            destination,
            cruise_policy,
            issues,
            arrival_altitude_ft_msl,
        )
        segmentation = self._build_phase_segmentation(
            geometries,
            climb_plan,
            descent_plan,
            additional_boundaries,
            issues,
        )
        if rjfm_departure_plan is not None and segmentation.rca_point is not None:
            segmentation = self._label_rjfm_rca(
                segmentation,
                assumed=(
                    rjfm_departure_plan.main_route_mode
                    == RjfmMainRouteMode.OMARU_VIRTUAL_UMK
                ),
            )

        sections: list[SectionResult] = []
        section_fuels: list[float | None] = []
        phases: list[FlightPhase] = []
        cumulative_distance = 0.0
        cumulative_seconds: float | None = 0.0
        departure_utc = project.planned_departure_time_jst.astimezone(timezone.utc)
        boundary_times: dict[float, datetime] = {0.0: departure_utc}
        source_time_bounds: dict[str, tuple[float, float]] = {}
        climb_source = (
            next(
                (
                    geometry.section
                    for geometry in geometries
                    if climb_plan is not None
                    and geometry.section.id == climb_plan.source_section_id
                ),
                None,
            )
            if climb_plan is not None
            else None
        )
        descent_source = (
            next(
                (
                    geometry.section
                    for geometry in geometries
                    if descent_plan is not None
                    and geometry.section.id == descent_plan.source_section_id
                ),
                None,
            )
            if descent_plan is not None
            else None
        )
        common_descent_environment = (
            next(
                (
                    environment.for_phase(FlightPhase.DESCENT)
                    for environment in environments
                    if descent_plan is not None
                    and environment.geometry.section.id == descent_plan.source_section_id
                ),
                None,
            )
            if descent_plan is not None
            else None
        )

        for segment in segmentation.segments:
            environment = environments[segment.source_index]
            geometry = environment.geometry
            section = geometry.section
            phase_environment = (
                common_descent_environment
                if segment.phase == FlightPhase.DESCENT
                and common_descent_environment is not None
                else environment.for_phase(segment.phase)
            )
            weather = phase_environment.weather
            segment_geometry = geodesic_leg(
                segment.start.latitude_deg,
                segment.start.longitude_deg,
                segment.end.latitude_deg,
                segment.end.longitude_deg,
            )
            manual_course = geometry.start.manual_true_course_deg
            course_value = _manual_or_automatic(
                geometry.geodesic.initial_true_course_deg,
                manual_course,
                metadata={
                    "method": "source physical leg WGS84 initial bearing",
                    "phase_segment": True,
                    "source_section_id": str(section.id),
                },
                warnings=("MANUAL_TRUE_COURSE",) if manual_course is not None else (),
            )
            adopted_course = course_value.adopted()
            source_manual_distance = geometry.start.manual_distance_nm
            distance_value = _automatic(
                segment.distance_nm,
                ValueState.AUTO,
                metadata={
                    "method": (
                        "distributed from source manual distance"
                        if source_manual_distance is not None
                        else "WGS84 geodesic phase segment"
                    ),
                    "source_section_id": str(section.id),
                    "source_manual_distance_nm": source_manual_distance,
                    "geodesic_segment_distance_nm": segment_geometry.distance_nm,
                    "global_start_distance_nm": segment.start_distance_nm,
                    "global_end_distance_nm": segment.end_distance_nm,
                },
                warnings=("DISTRIBUTED_FROM_MANUAL_SOURCE_DISTANCE",)
                if source_manual_distance is not None
                else (),
            )
            adopted_distance = distance_value.adopted()
            try:
                variation_decision = variation_for_departure_latitude(geometry.start.latitude_deg)
            except (TypeError, ValueError) as error:
                variation_decision = None
                issues.append(
                    Issue(
                        code="VARIATION_UNAVAILABLE",
                        severity=IssueSeverity.BLOCKER,
                        message="Leg出発点の緯度から偏差を自動判定できません。",
                        section_id=section.id,
                        metadata={
                            "departure_latitude_deg": geometry.start.latitude_deg,
                            "reason": str(error),
                        },
                    )
                )
            variation_deg_east = (
                None if variation_decision is None else variation_decision.degrees_east
            )
            magnetic_course = (
                None
                if adopted_course is None or variation_deg_east is None
                else (adopted_course + variation_deg_east) % 360
            )

            wind_direction = phase_environment.wind_direction_deg_from
            wind_speed = phase_environment.wind_speed_kt
            wind_metadata = phase_environment.weather_metadata
            wind_warnings = phase_environment.weather_warnings
            temperature = phase_environment.temperature_c
            exact_pa = phase_environment.pressure_altitude_exact_ft
            planning_pa = phase_environment.pressure_altitude_planning_ft
            tas: float | None = None
            manual_tas: float | None = None
            cas: float | None = None
            gph: float | None = None
            performance_metadata: dict[str, Any] = {
                "phase_segment": {
                    "sequence": segment.sequence,
                    "source_section_id": str(section.id),
                    "global_start_distance_nm": segment.start_distance_nm,
                    "global_end_distance_nm": segment.end_distance_nm,
                }
            }
            tas_state = ValueState.UNAVAILABLE

            if segment.phase == FlightPhase.CLIMB:
                if climb_plan is not None:
                    manual_tas = None if climb_source is None else climb_source.manual_tas_kt
                    tas = climb_plan.tas_kt
                    tas_state = (
                        ValueState.MANUAL_OVERRIDE
                        if manual_tas is not None
                        else ValueState.FIXED_RULE
                    )
                    if manual_tas is None:
                        cas = 111.0
                    performance_metadata.update(climb_plan.metadata)
                else:
                    manual_tas = section.manual_tas_kt
                    tas = manual_tas
                    tas_state = (
                        ValueState.MANUAL_OVERRIDE
                        if manual_tas is not None
                        else ValueState.UNAVAILABLE
                    )
            elif segment.phase == FlightPhase.CRUISE:
                manual_tas = section.manual_tas_kt
                tas = manual_tas
                tas_state = (
                    ValueState.MANUAL_OVERRIDE if manual_tas is not None else ValueState.UNAVAILABLE
                )
                if (
                    performance_usable
                    and temperature is not None
                    and wind_speed is not None
                    and adopted_distance is not None
                ):
                    try:
                        selected = cruise_policy.select(
                            planning_pa,
                            temperature - isa_temperature_c(planning_pa),
                            adopted_distance,
                            adopted_course or 0.0,
                            wind_direction,
                            wind_speed,
                        )
                        if tas is None:
                            tas = selected.row.ktas
                            tas_state = ValueState.PERFORMANCE_TABLE
                        gph = selected.row.gph
                        performance_metadata.update(
                            {
                                "type": "cruise",
                                "requested_condition": {
                                    "pressure_altitude_ft": planning_pa,
                                    "isa_deviation_c": temperature - isa_temperature_c(planning_pa),
                                },
                                "selected_condition": (
                                    None
                                    if selected.interpolation is None
                                    else {
                                        "pressure_altitude_ft": (
                                            selected.interpolation.altitude.lower
                                            + selected.interpolation.altitude.fraction
                                            * (
                                                selected.interpolation.altitude.upper
                                                - selected.interpolation.altitude.lower
                                            )
                                        ),
                                        "isa_deviation_c": (
                                            selected.interpolation.isa_deviation.lower
                                            + selected.interpolation.isa_deviation.fraction
                                            * (
                                                selected.interpolation.isa_deviation.upper
                                                - selected.interpolation.isa_deviation.lower
                                            )
                                        ),
                                        "power_percent_by_corner": [
                                            {
                                                "pressure_altitude_ft": corner.pressure_altitude_ft,
                                                "isa_deviation_c": corner.isa_deviation_c,
                                                "power_percent": corner.power.lower
                                                + corner.power.fraction
                                                * (corner.power.upper - corner.power.lower),
                                            }
                                            for corner in selected.interpolation.corners
                                        ],
                                    }
                                ),
                                "selected_cell": selected.row.model_dump(),
                                "reason": selected.reason,
                                "interpolation": (
                                    None
                                    if selected.interpolation is None
                                    else asdict(selected.interpolation)
                                ),
                                "warnings": selected.warnings,
                            }
                        )
                        for warning in selected.warnings:
                            messages = {
                                "CRUISE_PRESSURE_ALTITUDE_TABLE_BOUNDARY_USED": (
                                    "気圧高度が巡航性能表の範囲外のため、最寄りの表端高度を採用しました。"
                                ),
                                "CRUISE_ISA_DEVIATION_TABLE_BOUNDARY_USED": (
                                    "ISA偏差が巡航性能表の範囲外のため、最寄りの表端温度を採用しました。"
                                ),
                                "CRUISE_POWER_TABLE_BOUNDARY_USED": (
                                    "65%を挟む性能行がないため、最寄りの表端出力を採用しました。"
                                ),
                            }
                            issues.append(
                                Issue(
                                    code=warning,
                                    severity=IssueSeverity.WARNING,
                                    message=messages.get(
                                        warning,
                                        "最寄りの巡航性能表セルを採用しました。",
                                    ),
                                    section_id=section.id,
                                    segment_sequence=segment.sequence,
                                    acknowledgement_required=True,
                                )
                            )
                    except CruisePerformanceError as error:
                        issues.append(
                            self._blocker(
                                "CRUISE_PERFORMANCE_UNAVAILABLE",
                                str(error),
                                section.id,
                                segment_sequence=segment.sequence,
                            )
                        )
            elif segment.phase == FlightPhase.DESCENT:
                if descent_plan is not None:
                    manual_tas = None if descent_source is None else descent_source.manual_tas_kt
                    if manual_tas is not None:
                        tas = manual_tas
                        tas_state = ValueState.MANUAL_OVERRIDE
                    elif temperature is not None:
                        tas = tas_from_cas(
                            descent_plan.cruise_cas_kt,
                            exact_pa,
                            temperature,
                        )
                        tas_state = ValueState.AUTO
                    performance_metadata.update(descent_plan.metadata)
                else:
                    manual_tas = section.manual_tas_kt
                    tas = manual_tas
                    tas_state = (
                        ValueState.MANUAL_OVERRIDE
                        if manual_tas is not None
                        else ValueState.UNAVAILABLE
                    )
            else:
                if temperature is not None:
                    tas = tas_from_cas(121.0, exact_pa, temperature)
                    tas_state = ValueState.FIXED_RULE
                cas = 121.0
                destination_icao = destination.icao.strip().upper()
                usable_destination_wind = (
                    destination_wind is not None
                    and destination_wind.airport_icao.strip().upper() == destination_icao
                    and destination_wind.availability == Availability.AVAILABLE
                    and destination_wind.wind_speed_kt is not None
                    and not destination_wind.variable_direction
                    and (
                        destination_wind.wind_speed_kt == 0
                        or destination_wind.wind_direction_deg_from is not None
                    )
                )
                # Destination forecast wind is display-only.  The VREP to
                # destination calculation is always CALM by project policy.
                wind_direction = None
                wind_speed = 0.0
                wind_metadata = {
                    "provider": "fixed_rule",
                    "airport_icao": destination_icao,
                    "wind_adoption": "CALM",
                    "destination_forecast_available_for_display": usable_destination_wind,
                }
                wind_warnings = ()
                performance_metadata.update(
                    {
                        "type": "visual_arrival",
                        "cas_kt": 121.0,
                        "wind": "CALM",
                        "fuel_flow_gph": 12.0,
                    }
                )

            if tas is not None and temperature is not None and cas is None:
                cas = cas_from_tas(tas, exact_pa, temperature)

            wind_solution = None
            if tas is not None and wind_speed is not None:
                try:
                    wind_solution = solve_wind_triangle(
                        adopted_course or 0.0,
                        tas,
                        wind_direction,
                        wind_speed,
                    )
                except WindTriangleError as error:
                    issues.append(
                        self._blocker(
                            "WIND_TRIANGLE_FAILED",
                            str(error),
                            section.id,
                            segment_sequence=segment.sequence,
                        )
                    )
            ete_seconds = (
                None
                if wind_solution is None or adopted_distance is None
                else adopted_distance / wind_solution.ground_speed_kt * 3600.0
            )
            if (
                segment.phase == FlightPhase.CLIMB
                and climb_plan is not None
                and rjfm_departure_plan is not None
                and climb_plan.metadata.get("boundary_method") == "RJFM_UMK_FIXED_RCA"
            ):
                ete_seconds = (
                    climb_plan.duration_seconds
                    * segment.distance_nm
                    / climb_plan.route_end_distance_nm
                )
            if (
                segment.phase == FlightPhase.CLIMB
                and climb_plan is not None
                and ete_seconds is not None
            ):
                section_fuel = climb_plan.fuel_gal * ete_seconds / climb_plan.duration_seconds
            else:
                section_fuel = fuel_for_section(
                    segment.phase,
                    ete_seconds,
                    cruise_gph=gph,
                )

            cumulative_distance += segment.distance_nm
            segment_start_seconds = cumulative_seconds
            if ete_seconds is not None and cumulative_seconds is not None:
                if segment_start_seconds is None:
                    raise RuntimeError("determined route timing lost its segment start")
                arrival_seconds = segment_start_seconds + ete_seconds
                cumulative_seconds = arrival_seconds
                boundary_times[segment.end_distance_nm] = departure_utc + timedelta(
                    seconds=arrival_seconds
                )
                source_key = str(section.id)
                previous_bounds = source_time_bounds.get(source_key)
                source_time_bounds[source_key] = (
                    segment_start_seconds if previous_bounds is None else previous_bounds[0],
                    arrival_seconds,
                )
            else:
                cumulative_seconds = None

            automatic_wind_direction = (
                None if weather is None else self._numeric(weather, "wind_direction_deg_from")
            )
            automatic_wind_speed = (
                None if weather is None else self._numeric(weather, "wind_speed_kt")
            )
            phase_manual_wind = section.manual_wind_by_phase.get(segment.phase)
            manual_wind_direction = (
                phase_manual_wind.direction_deg_from
                if phase_manual_wind is not None
                else section.manual_wind_direction_deg
                if segment.phase == section.phase
                else None
            )
            manual_wind_speed = (
                phase_manual_wind.speed_kt
                if phase_manual_wind is not None
                else section.manual_wind_speed_kt
                if segment.phase == section.phase
                else None
            )
            wind_state = ValueState.AUTO
            if segment.phase == FlightPhase.VISUAL_ARRIVAL:
                automatic_wind_direction = wind_direction
                automatic_wind_speed = wind_speed
                manual_wind_direction = None
                manual_wind_speed = None
                wind_state = ValueState.AUTO if usable_destination_wind else ValueState.FIXED_RULE

            has_derived_endpoint = bool(segment.start.markers or segment.end.markers)
            planned_altitude = (
                arrival_altitude_ft_msl
                if (
                    segment.phase == FlightPhase.VISUAL_ARRIVAL
                    and arrival_altitude_ft_msl is not None
                )
                else section.planned_altitude_ft_msl
            )
            planned_altitude_metadata: dict[str, Any] = {"source": "PROJECT_INPUT"}
            if segment.phase == FlightPhase.VISUAL_ARRIVAL and arrival_altitude is not None:
                planned_altitude_metadata = {
                    "source": "ARRIVAL_ALTITUDE_RULE",
                    "rule_version": arrival_altitude.rule_version,
                    "adopted_source": arrival_altitude.adopted_source.value,
                }
            sections.append(
                SectionResult(
                    section_id=section.id,
                    sequence=len(sections),
                    phase=segment.phase,
                    segment_label=(
                        f"{segment.start.label}→{segment.end.label}"
                        if has_derived_endpoint
                        else None
                    ),
                    from_name=segment.start.label,
                    to_name=segment.end.label,
                    planned_altitude_ft_msl=_automatic(
                        planned_altitude,
                        ValueState.FIXED_RULE,
                        planned_altitude_metadata,
                    ),
                    # DESIGN.md §0.2: compatibility-only; legacy SEA must stay unused.
                    safe_enroute_altitude_ft_msl=_unavailable(),
                    loss_time_seconds=0.0,
                    pressure_altitude_exact_ft=_automatic(exact_pa),
                    pressure_altitude_planning_ft=_automatic(
                        planning_pa,
                        ValueState.FIXED_RULE,
                        {"policy": "PA_EQUALS_MSL"},
                    ),
                    true_course_deg=course_value,
                    variation_deg_east=_automatic(
                        variation_deg_east,
                        ValueState.FIXED_RULE,
                        None if variation_decision is None else variation_decision.metadata,
                    ),
                    magnetic_course_deg=_automatic(magnetic_course),
                    wind_direction_deg_from=_manual_or_automatic(
                        automatic_wind_direction,
                        manual_wind_direction,
                        state=wind_state,
                        metadata=wind_metadata,
                        warnings=wind_warnings,
                    ),
                    wind_speed_kt=_manual_or_automatic(
                        automatic_wind_speed,
                        manual_wind_speed,
                        state=wind_state,
                        metadata=wind_metadata,
                        warnings=wind_warnings,
                    ),
                    wca_deg=_automatic(None if wind_solution is None else wind_solution.wca_deg),
                    magnetic_heading_deg=_automatic(
                        None
                        if wind_solution is None or magnetic_course is None
                        else (magnetic_course + wind_solution.wca_deg) % 360
                    ),
                    temperature_c=_manual_or_automatic(
                        None if weather is None else self._numeric(weather, "temperature_c"),
                        section.manual_temperature_c_by_phase.get(
                            segment.phase,
                            section.manual_temperature_c
                            if segment.phase == section.phase
                            else None,
                        ),
                        metadata=phase_environment.weather_metadata,
                    ),
                    cas_kt=_automatic(
                        cas,
                        ValueState.FIXED_RULE
                        if segment.phase == FlightPhase.VISUAL_ARRIVAL
                        else ValueState.AUTO,
                        performance_metadata,
                    ),
                    tas_kt=_manual_or_automatic(
                        tas if manual_tas is None else None,
                        manual_tas,
                        state=tas_state,
                        metadata=performance_metadata,
                    ),
                    ground_speed_kt=_automatic(
                        None if wind_solution is None else wind_solution.ground_speed_kt
                    ),
                    zone_distance_nm=distance_value,
                    cumulative_distance_nm=_automatic(cumulative_distance),
                    zone_ete_seconds=_automatic(ete_seconds),
                    cumulative_ete_seconds=_automatic(cumulative_seconds),
                    # DESIGN.md §6.5: ETO needs an actual time check, not planned ETD.
                    eto_utc=_unavailable(),
                    section_fuel_gal=_automatic(
                        section_fuel,
                        ValueState.PERFORMANCE_TABLE
                        if segment.phase in {FlightPhase.CLIMB, FlightPhase.CRUISE}
                        else ValueState.FIXED_RULE,
                        performance_metadata,
                    ),
                    remaining_fuel_gal=_unavailable(),
                    performance_metadata=performance_metadata,
                )
            )
            section_fuels.append(section_fuel)
            phases.append(segment.phase)

        if climb_plan is not None:
            climb_indices = [
                index for index, phase in enumerate(phases) if phase == FlightPhase.CLIMB
            ]
            climb_values = [section_fuels[index] for index in climb_indices]
            if climb_indices and all(value is not None for value in climb_values):
                correction_index = climb_indices[-1]
                correction = climb_plan.fuel_gal - sum(
                    value for value in climb_values if value is not None
                )
                current_fuel = section_fuels[correction_index]
                if current_fuel is not None:
                    corrected_fuel = current_fuel + correction
                    section_fuels[correction_index] = corrected_fuel
                    sections[correction_index] = sections[correction_index].model_copy(
                        update={
                            "section_fuel_gal": sections[
                                correction_index
                            ].section_fuel_gal.model_copy(
                                update={"automatic_value": corrected_fuel}
                            )
                        }
                    )

        representative_times = {
            section_id: departure_utc + timedelta(seconds=(bounds[0] + bounds[1]) / 2.0)
            for section_id, bounds in source_time_bounds.items()
        }
        derived_points = self._derived_points_from_segmentation(
            segmentation,
            boundary_times,
        )
        if (
            climb_plan is not None
            and geometries
            and climb_plan.route_end_distance_nm > geometries[0].distance_nm + 1e-9
        ):
            issues.append(
                Issue(
                    code="RCA_BEYOND_FIRST_TURN",
                    severity=IssueSeverity.WARNING,
                    message="RCAが最初の変針点を越えます。経路を確認してください。",
                    section_id=climb_plan.source_section_id,
                    acknowledgement_required=True,
                )
            )

        remaining = remaining_fuel(project.total_usable_fuel_gal, section_fuels)
        sections = [
            result.model_copy(
                update={
                    "remaining_fuel_gal": _automatic(value) if value is not None else _unavailable()
                }
            )
            for result, value in zip(sections, remaining, strict=True)
        ]
        display_rows = build_navlog_display_rows(
            [
                NavLogPhysicalLeg(
                    section_id=geometry.section.id,
                    phase=geometry.section.phase,
                    start_name=geometry.start.name,
                    end_name=geometry.end.name,
                )
                for geometry in geometries
            ],
            sections,
            departure,
            destination,
            weather_by_id.get("departure:surface"),
            weather_by_id.get("destination:surface"),
            destination_wind,
        )
        return _IterationResult(
            sections,
            display_rows,
            self._deduplicate_issues(issues),
            representative_times,
            phases,
            section_fuels,
            derived_points,
        )

    @staticmethod
    def _section_weather(
        section: NavSection,
        weather: WeatherResult | None,
        phase: FlightPhase,
    ) -> tuple[float | None, float | None, float | None, dict[str, Any], tuple[str, ...]]:
        metadata = (
            {
                "availability": Availability.UNAVAILABLE.value,
                "reason_code": "WEATHER_RESULT_MISSING",
            }
            if weather is None
            else weather.metadata
            | {
                "values": weather.values,
                "availability": weather.availability.value,
                "reason_code": weather.reason_code,
            }
        )
        warnings = () if weather is None else weather.warnings
        automatic_direction = None
        automatic_speed = None
        automatic_temperature = None
        if weather is not None and weather.availability == Availability.AVAILABLE:
            automatic_direction = CalculationService._numeric(
                weather,
                "wind_direction_deg_from",
            )
            automatic_speed = CalculationService._numeric(weather, "wind_speed_kt")
            automatic_temperature = CalculationService._numeric(weather, "temperature_c")
        phase_manual_wind = section.manual_wind_by_phase.get(phase)
        manual_direction = (
            phase_manual_wind.direction_deg_from
            if phase_manual_wind is not None
            else section.manual_wind_direction_deg
            if phase == section.phase
            else None
        )
        manual_speed = (
            phase_manual_wind.speed_kt
            if phase_manual_wind is not None
            else section.manual_wind_speed_kt
            if phase == section.phase
            else None
        )
        direction = manual_direction if manual_direction is not None else automatic_direction
        speed = manual_speed if manual_speed is not None else automatic_speed
        manual_temperature = section.manual_temperature_c_by_phase.get(
            phase,
            section.manual_temperature_c if phase == section.phase else None,
        )
        temperature = (
            manual_temperature if manual_temperature is not None else automatic_temperature
        )
        if speed == 0:
            direction = None
        return direction, speed, temperature, metadata, warnings

    @staticmethod
    def _numeric(result: WeatherResult, key: str) -> float | None:
        value = result.values.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _descent_target_altitude(
        index: int,
        geometries: list[_Geometry],
        destination: Airport,
        arrival_altitude_ft_msl: float | None,
    ) -> float:
        if index + 1 < len(geometries):
            if (
                geometries[index + 1].section.phase == FlightPhase.VISUAL_ARRIVAL
                and arrival_altitude_ft_msl is not None
            ):
                return arrival_altitude_ft_msl
            return geometries[index + 1].section.planned_altitude_ft_msl
        if arrival_altitude_ft_msl is not None:
            return arrival_altitude_ft_msl
        return destination.pattern_altitude_ft_msl or destination.elevation_ft_msl

    def _flight_phase_sequence_issues(
        self,
        geometries: list[_Geometry],
    ) -> list[Issue]:
        phase_order = {
            FlightPhase.CLIMB: 0,
            FlightPhase.CRUISE: 1,
            FlightPhase.DESCENT: 2,
            FlightPhase.VISUAL_ARRIVAL: 3,
        }
        issues: list[Issue] = []
        counts: dict[FlightPhase, int] = {}
        previous_order = -1
        for geometry in geometries:
            phase = geometry.section.phase
            counts[phase] = counts.get(phase, 0) + 1
            current_order = phase_order[phase]
            reasons: list[str] = []
            if current_order < previous_order:
                reasons.append("phase order moves backward")
            if phase != FlightPhase.CRUISE and counts[phase] > 1:
                reasons.append(f"{phase.value} appears more than once")
            if reasons:
                issues.append(
                    Issue(
                        code="FLIGHT_PHASE_SEQUENCE_INVALID",
                        severity=IssueSeverity.BLOCKER,
                        message=(
                            "飛行Phaseの順序または出現回数が不正です。"
                            "CLIMB→CRUISE→DESCENT→VISUAL_ARRIVALの順とし、"
                            "CRUISE以外は各1回までです。"
                        ),
                        section_id=geometry.section.id,
                        metadata={
                            "phase": phase.value,
                            "reasons": reasons,
                        },
                    )
                )
            previous_order = max(previous_order, current_order)
        return issues

    def _calculation_output_completeness_issues(
        self,
        sections: list[SectionResult],
    ) -> list[Issue]:
        issues: list[Issue] = []
        for result in sections:
            required: list[tuple[str, AdoptedValue[Any]]] = [
                ("planned_altitude_ft_msl", result.planned_altitude_ft_msl),
                ("pressure_altitude_exact_ft", result.pressure_altitude_exact_ft),
                ("pressure_altitude_planning_ft", result.pressure_altitude_planning_ft),
                ("true_course_deg", result.true_course_deg),
                ("variation_deg_east", result.variation_deg_east),
                ("magnetic_course_deg", result.magnetic_course_deg),
                ("wind_speed_kt", result.wind_speed_kt),
                ("temperature_c", result.temperature_c),
                ("cas_kt", result.cas_kt),
                ("tas_kt", result.tas_kt),
                ("ground_speed_kt", result.ground_speed_kt),
                ("wca_deg", result.wca_deg),
                ("magnetic_heading_deg", result.magnetic_heading_deg),
                ("zone_distance_nm", result.zone_distance_nm),
                ("cumulative_distance_nm", result.cumulative_distance_nm),
                ("zone_ete_seconds", result.zone_ete_seconds),
                ("cumulative_ete_seconds", result.cumulative_ete_seconds),
                ("section_fuel_gal", result.section_fuel_gal),
                ("remaining_fuel_gal", result.remaining_fuel_gal),
            ]
            missing_fields = [
                name for name, adopted_value in required if adopted_value.adopted() is None
            ]
            wind_speed = result.wind_speed_kt.adopted()
            wind_direction = result.wind_direction_deg_from.adopted()
            if wind_speed is not None and wind_speed > 0 and wind_direction is None:
                missing_fields.append("wind_direction_deg_from")
            if missing_fields:
                issues.append(
                    Issue(
                        code="CALCULATION_OUTPUT_INCOMPLETE",
                        severity=IssueSeverity.BLOCKER,
                        message=("清書に必要な計算値が未確定です: " + ", ".join(missing_fields)),
                        section_id=result.section_id,
                        segment_sequence=result.sequence,
                        metadata={
                            "segment_sequence": result.sequence,
                            "missing_fields": missing_fields,
                        },
                    )
                )
        return issues

    def _missing_derived_phase_point_issues(
        self,
        geometries: list[_Geometry],
        points: list[DerivedRoutePoint],
    ) -> list[Issue]:
        present = {point.type for point in points}
        required = (
            (FlightPhase.CLIMB, DerivedPointType.RCA),
            (FlightPhase.DESCENT, DerivedPointType.EOC),
        )
        issues: list[Issue] = []
        for phase, point_type in required:
            geometry = next(
                (item for item in geometries if item.section.phase == phase),
                None,
            )
            if geometry is None or point_type in present:
                continue
            issues.append(
                Issue(
                    code="DERIVED_PHASE_POINT_MISSING",
                    severity=IssueSeverity.BLOCKER,
                    message=(
                        f"{phase.value}を含む計画ですが、{point_type.value}を算出できません。"
                    ),
                    section_id=geometry.section.id,
                    metadata={
                        "phase": phase.value,
                        "required_point": point_type.value,
                    },
                )
            )
        return issues

    @staticmethod
    def _maximum_time_delta(
        previous: dict[str, datetime],
        current: dict[str, datetime],
    ) -> float | None:
        common = previous.keys() & current.keys()
        if not common:
            return None
        return max(abs((current[key] - previous[key]).total_seconds()) for key in common)

    @staticmethod
    def _blocker(
        code: str,
        message: str,
        section_id: UUID | None = None,
        *,
        segment_sequence: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Issue:
        return Issue(
            code=code,
            severity=IssueSeverity.BLOCKER,
            message=message,
            section_id=section_id,
            segment_sequence=segment_sequence,
            metadata=metadata or {},
        )

    @staticmethod
    def _deduplicate_issues(issues: list[Issue]) -> list[Issue]:
        output: list[Issue] = []
        seen: set[tuple[str, UUID | None, int | None]] = set()
        for issue in issues:
            key = (issue.code, issue.section_id, issue.segment_sequence)
            if key not in seen:
                seen.add(key)
                output.append(issue)
        return output

    @staticmethod
    def _status(project: Project, issues: list[Issue]) -> ProjectStatus:
        blockers = [issue for issue in issues if issue.severity == IssueSeverity.BLOCKER]
        if any(issue.code == "ROUTE_INCOMPLETE" for issue in blockers):
            return ProjectStatus.ROUTE_INCOMPLETE
        if blockers:
            if any(issue.code.startswith(("FORECAST", "WEATHER")) for issue in blockers):
                return ProjectStatus.WEATHER_PENDING
            return ProjectStatus.MANUAL_INPUT_REQUIRED
        unacknowledged = [
            issue
            for issue in issues
            if issue.acknowledgement_required
            and issue.code not in project.acknowledged_warning_codes
        ]
        if unacknowledged:
            return ProjectStatus.CALCULATION_WARNING
        return ProjectStatus.READY_FOR_COPY

    def _empty_outcome(
        self,
        project: Project,
        issues: list[Issue],
        check_point_projections: list[CheckPointProjection] | None = None,
        arrival_altitude: ArrivalAltitudeResult | None = None,
    ) -> CalculationOutcome:
        return CalculationOutcome(
            project_id=project.id,
            selected_forecast_run_id=project.selected_forecast_run_id,
            qnh_hpa=_manual_or_automatic(None, project.manual_qnh_hpa),
            arrival_altitude=arrival_altitude,
            fuel_plan=FuelPlan(total_usable_gal=project.total_usable_fuel_gal),
            check_point_projections=(check_point_projections or []),
            issues=self._deduplicate_issues(issues),
            status=self._status(project, issues),
            policy_version=self.policies.version,
            performance_table_version=self.performance.manifest.source_revision,
        )
