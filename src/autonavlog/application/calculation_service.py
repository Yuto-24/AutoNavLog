from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from autonavlog.domain.calculation import (
    CalculationOutcome,
    DerivedRoutePoint,
    FuelPlan,
    Issue,
    IterationRecord,
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
from autonavlog.domain.project import Airport, NavSection, Project, RouteNode
from autonavlog.domain.values import AdoptedValue
from autonavlog.domain.weather import ForecastRequirement, WeatherRequest, WeatherResult
from autonavlog.nav.airspeed import (
    cas_from_tas,
    isa_temperature_c,
    pressure_altitude_exact_ft,
    pressure_altitude_planning_ft,
    tas_from_cas,
)
from autonavlog.nav.fuel import build_fuel_plan, fuel_for_section, remaining_fuel
from autonavlog.nav.geodesy import GeodesicLeg, geodesic_leg, point_along_route
from autonavlog.nav.wind_triangle import WindTriangleError, solve_wind_triangle
from autonavlog.performance.climb import ClimbCalculator, ClimbPerformanceError
from autonavlog.performance.cruise import (
    CruisePerformanceError,
    CruisePerformanceSelectionPolicy,
)
from autonavlog.performance.repository import PerformanceDataError, PerformanceRepository
from autonavlog.storage.airports import AirportRepository
from autonavlog.weather.provider import WeatherProvider

from .forecast_service import ForecastService


@dataclass(frozen=True)
class CalculationPolicies:
    version: str = "nav2-v1"
    pa_500_policy: Pa500Policy = Pa500Policy.CEILING
    max_iterations: int = 5
    convergence_seconds: float = 30.0


@dataclass(frozen=True)
class _Geometry:
    section: NavSection
    start: RouteNode
    end: RouteNode
    geodesic: GeodesicLeg
    true_course_deg: float
    distance_nm: float


@dataclass
class _IterationResult:
    sections: list[SectionResult]
    issues: list[Issue]
    representative_times: dict[str, datetime]
    phases: list[FlightPhase]
    section_fuels: list[float | None]


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


def _unavailable() -> AdoptedValue[Any]:
    return AdoptedValue(automatic_status=ValueState.UNAVAILABLE)


class CalculationService:
    def __init__(
        self,
        airports: AirportRepository,
        performance: PerformanceRepository,
        policies: CalculationPolicies | None = None,
        forecast_service: ForecastService | None = None,
    ):
        self.airports = airports
        self.performance = performance
        self.policies = policies or CalculationPolicies()
        self.forecast_service = forecast_service or ForecastService()
        self.last_weather_requests: list[WeatherRequest] = []
        self.last_weather_results: list[WeatherResult] = []
        self.last_forecast_metadata: dict[str, Any] = {}

    def calculate(self, project: Project, provider: WeatherProvider) -> CalculationOutcome:
        working = project.model_copy(deep=True)
        issues: list[Issue] = []
        self.last_weather_requests = []
        self.last_weather_results = []
        self.last_forecast_metadata = {}
        geometries = self._build_geometry(working, issues)
        try:
            departure = self.airports.get(working.departure_airport_id)
            destination = self.airports.get(working.destination_airport_id)
        except KeyError as error:
            issues.append(self._blocker("AIRPORT_DATA_UNAVAILABLE", str(error)))
            return self._empty_outcome(working, issues)
        if not geometries:
            return self._empty_outcome(working, issues)

        initial_requirement = self.forecast_service.build_initial_requirement(working)
        selected_run_id = self._select_and_prepare_run(
            working,
            provider,
            initial_requirement,
            issues,
        )
        if selected_run_id is None and working.manual_qnh_hpa is None:
            return self._empty_outcome(working, issues)

        previous_times: dict[str, datetime] = {}
        iteration_records: list[IterationRecord] = []
        final: _IterationResult | None = None
        qnh_value: AdoptedValue[float] = _manual_or_automatic(
            None,
            working.manual_qnh_hpa,
        )
        converged = False
        for iteration in range(1, self.policies.max_iterations + 1):
            requests = self._weather_requests(
                working,
                departure,
                geometries,
                previous_times,
            )
            results = self._query_weather(provider, selected_run_id, requests, issues)
            qnh_value = self._adopt_qnh(working, results)
            adopted_qnh = qnh_value.adopted()
            if adopted_qnh is None:
                issues.append(
                    self._blocker(
                        "QNH_UNAVAILABLE",
                        "MSM推定QNHを取得できません。手動QNHが必要です。",
                    )
                )
                break
            iteration_result = self._calculate_iteration(
                working,
                departure,
                destination,
                geometries,
                results,
                adopted_qnh,
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
            outcome = self._empty_outcome(working, self._deduplicate_issues(issues))
            return outcome.model_copy(
                update={
                    "selected_forecast_run_id": selected_run_id,
                    "qnh_hpa": qnh_value,
                    "iterations": iteration_records,
                }
            )
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
        derived_issues, derived_points = self._derived_point_issues_and_results(
            geometries,
            final.sections,
        )
        issues.extend(derived_issues)

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
        return CalculationOutcome(
            project_id=working.id,
            selected_forecast_run_id=selected_run_id,
            qnh_hpa=qnh_value,
            sections=final.sections,
            derived_points=derived_points,
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
        nodes = {node.id: node for node in project.route_nodes}
        geometries: list[_Geometry] = []
        for section in project.ordered_sections():
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
            if project.selected_forecast_run_id is None:
                run = provider.resolve_run(requirement)
                selected_run_id = run.id
            else:
                status = provider.inspect_run_status(
                    project.selected_forecast_run_id,
                    requirement,
                )
                selected_run_id = project.selected_forecast_run_id
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
                                "新しい互換Forecast Runがあります。"
                                "保存済みRunは変更していません。"
                            ),
                        )
                    )
            prepared = provider.prepare_run(selected_run_id, requirement)
            self.last_forecast_metadata = prepared.metadata
            return selected_run_id
        except Exception as error:
            issues.append(self._blocker("FORECAST_PREPARE_FAILED", str(error)))
            return None

    def _weather_requests(
        self,
        project: Project,
        departure: Airport,
        geometries: list[_Geometry],
        previous_times: dict[str, datetime],
    ) -> list[WeatherRequest]:
        requests = [
            WeatherRequest(
                request_id="project:qnh",
                kind=WeatherRequestKind.ESTIMATED_QNH,
                latitude_deg=departure.latitude_deg,
                longitude_deg=departure.longitude_deg,
                valid_time_utc=project.planned_departure_time_jst,
                elevation_ft_msl=departure.elevation_ft_msl,
            )
        ]
        elapsed = 0.0
        for geometry in geometries:
            default_seconds = geometry.distance_nm / (
                geometry.section.manual_tas_kt or ForecastService.estimate_speed_kt
            ) * 3600.0
            valid_time = previous_times.get(
                str(geometry.section.id),
                project.planned_departure_time_jst
                + timedelta(seconds=elapsed + default_seconds / 2),
            )
            elapsed += default_seconds + geometry.section.loss_time_seconds
            requests.append(
                WeatherRequest(
                    request_id=f"section:{geometry.section.id}:aloft",
                    kind=WeatherRequestKind.ALOFT,
                    latitude_deg=geometry.geodesic.midpoint_latitude_deg,
                    longitude_deg=geometry.geodesic.midpoint_longitude_deg,
                    valid_time_utc=valid_time,
                    altitude_ft_msl=geometry.section.planned_altitude_ft_msl,
                )
            )
        return requests

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
        self.last_weather_results.extend(results)
        return results

    @staticmethod
    def _adopt_qnh(project: Project, results: list[WeatherResult]) -> AdoptedValue[float]:
        result = next((item for item in results if item.request_id == "project:qnh"), None)
        automatic = None
        metadata: dict[str, Any] = {}
        warnings: tuple[str, ...] = ()
        if result is not None:
            metadata = result.metadata | {"values": result.values, "label": "MSM推定QNH"}
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

    def _calculate_iteration(
        self,
        project: Project,
        departure: Airport,
        destination: Airport,
        geometries: list[_Geometry],
        weather_results: list[WeatherResult],
        qnh_hpa: float,
    ) -> _IterationResult:
        issues: list[Issue] = []
        weather_by_id = {item.request_id: item for item in weather_results}
        sections: list[SectionResult] = []
        section_fuels: list[float | None] = []
        cumulative_distance = 0.0
        cumulative_seconds = 0.0
        last_cruise_cas: float | None = None
        representative_times: dict[str, datetime] = {}
        performance_usable = True
        try:
            self.performance.require_verified()
        except PerformanceDataError as error:
            performance_usable = False
            issues.append(self._blocker("PERFORMANCE_DATA_UNAVAILABLE", str(error)))
        climb_calculator = ClimbCalculator(self.performance.climb_rows)
        cruise_policy = CruisePerformanceSelectionPolicy(self.performance.cruise_rows)
        unique_weights = sorted({row.weight_lb for row in self.performance.climb_rows})

        for index, geometry in enumerate(geometries):
            section = geometry.section
            weather = weather_by_id.get(f"section:{section.id}:aloft")
            wind_direction, wind_speed, temperature, weather_metadata, weather_warnings = (
                self._section_weather(section, weather)
            )
            if temperature is None:
                issues.append(
                    self._blocker(
                        "TEMPERATURE_UNAVAILABLE",
                        "気温を取得できません。手動入力が必要です。",
                        section.id,
                    )
                )
            if wind_speed is None or (wind_speed > 0 and wind_direction is None):
                issues.append(
                    self._blocker(
                        "WIND_UNAVAILABLE",
                        "風を取得できません。手動入力が必要です。",
                        section.id,
                    )
                )

            exact_pa = pressure_altitude_exact_ft(section.planned_altitude_ft_msl, qnh_hpa)
            planning_pa = pressure_altitude_planning_ft(
                exact_pa,
                self.policies.pa_500_policy,
            )
            course_value = _manual_or_automatic(
                geometry.geodesic.initial_true_course_deg,
                geometry.start.manual_true_course_deg,
                metadata={"method": "WGS84 initial bearing"},
                warnings=("MANUAL_TRUE_COURSE",)
                if geometry.start.manual_true_course_deg is not None
                else (),
            )
            distance_value = _manual_or_automatic(
                geometry.geodesic.distance_nm,
                geometry.start.manual_distance_nm,
                metadata={"method": "WGS84 geodesic"},
                warnings=("MANUAL_DISTANCE",)
                if geometry.start.manual_distance_nm is not None
                else (),
            )
            adopted_course = course_value.adopted()
            adopted_distance = distance_value.adopted()
            magnetic_course = (
                None
                if adopted_course is None
                else (adopted_course - project.default_variation_deg_east) % 360
            )

            tas: float | None = section.manual_tas_kt
            cas: float | None = None
            gph: float | None = None
            ete_seconds: float | None = None
            section_fuel: float | None = None
            performance_metadata: dict[str, Any] = {}
            tas_state = ValueState.MANUAL_OVERRIDE if tas is not None else ValueState.UNAVAILABLE

            if (
                section.phase == FlightPhase.CLIMB
                and performance_usable
                and temperature is not None
            ):
                if len(unique_weights) != 1:
                    issues.append(
                        self._blocker(
                            "CLIMB_WEIGHT_UNRESOLVED",
                            "上昇表に複数重量があり、採用重量を確定できません。",
                            section.id,
                        )
                    )
                else:
                    try:
                        departure_pa = pressure_altitude_exact_ft(
                            departure.elevation_ft_msl,
                            qnh_hpa,
                        )
                        climb = climb_calculator.calculate(
                            departure_pa,
                            exact_pa,
                            temperature,
                            unique_weights[0],
                        )
                        if tas is None:
                            tas = climb.representative_tas_kt
                            tas_state = ValueState.PERFORMANCE_TABLE
                        ete_seconds = climb.time_min * 60.0
                        section_fuel = climb.fuel_gal
                        performance_metadata = {
                            "type": "climb",
                            "weight_lb": unique_weights[0],
                            "distance_nm": climb.distance_nm,
                            "warnings": climb.warnings,
                        }
                        for warning in climb.warnings:
                            issues.append(
                                Issue(
                                    code=warning,
                                    severity=IssueSeverity.WARNING,
                                    message="上昇表の高度軸を表端から500 ft以内で外挿しました。",
                                    section_id=section.id,
                                )
                            )
                    except ClimbPerformanceError as error:
                        issues.append(
                            self._blocker("CLIMB_PERFORMANCE_UNAVAILABLE", str(error), section.id)
                        )
            elif (
                section.phase == FlightPhase.CRUISE
                and performance_usable
                and temperature is not None
            ):
                if adopted_distance is not None and wind_speed is not None:
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
                        performance_metadata = {
                            "type": "cruise",
                            "selected_cell": selected.row.model_dump(),
                            "reason": selected.reason,
                            "warnings": selected.warnings,
                        }
                        for warning in selected.warnings:
                            issues.append(
                                Issue(
                                    code=warning,
                                    severity=IssueSeverity.WARNING,
                                    message="65%に最も近い表セルを採用しました。",
                                    section_id=section.id,
                                )
                            )
                    except CruisePerformanceError as error:
                        issues.append(
                            self._blocker("CRUISE_PERFORMANCE_UNAVAILABLE", str(error), section.id)
                        )
            elif section.phase == FlightPhase.DESCENT:
                if last_cruise_cas is not None and temperature is not None and tas is None:
                    tas = tas_from_cas(last_cruise_cas, exact_pa, temperature)
                    tas_state = ValueState.AUTO
                target_altitude = self._descent_target_altitude(index, geometries, destination)
                altitude_difference = section.planned_altitude_ft_msl - target_altitude
                if altitude_difference <= 0:
                    issues.append(
                        self._blocker(
                            "DESCENT_ALTITUDE_INVALID",
                            "降下開始高度は到着側高度より高く設定してください。",
                            section.id,
                        )
                    )
                else:
                    ete_seconds = altitude_difference / 500.0 * 60.0
                    performance_metadata = {
                        "type": "descent",
                        "descent_rate_fpm": 500.0,
                        "target_altitude_ft_msl": target_altitude,
                        "fuel_flow_gph": 12.0,
                    }
            elif section.phase == FlightPhase.VISUAL_ARRIVAL:
                if temperature is not None and tas is None:
                    tas = tas_from_cas(121.0, exact_pa, temperature)
                    tas_state = ValueState.FIXED_RULE
                cas = 121.0
                wind_direction, wind_speed = None, 0.0
                performance_metadata = {
                    "type": "visual_arrival",
                    "cas_kt": 121.0,
                    "wind": "CALM_FIXED_RULE",
                    "fuel_flow_gph": 12.0,
                }

            if tas is not None and temperature is not None and cas is None:
                cas = cas_from_tas(tas, exact_pa, temperature)
            if section.phase == FlightPhase.CRUISE and cas is not None:
                last_cruise_cas = cas

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
                    issues.append(self._blocker("WIND_TRIANGLE_FAILED", str(error), section.id))
            if (
                ete_seconds is None
                and wind_solution is not None
                and adopted_distance is not None
            ):
                ete_seconds = adopted_distance / wind_solution.ground_speed_kt * 3600.0
            if section_fuel is None:
                section_fuel = fuel_for_section(
                    section.phase,
                    ete_seconds,
                    cruise_gph=gph,
                )

            cumulative_distance += adopted_distance or 0.0
            if ete_seconds is not None:
                representative_times[str(section.id)] = (
                    project.planned_departure_time_jst.astimezone(timezone.utc)
                    + timedelta(seconds=cumulative_seconds + ete_seconds / 2)
                )
                cumulative_seconds += ete_seconds + section.loss_time_seconds
                eto = (
                    project.planned_departure_time_jst.astimezone(timezone.utc)
                    + timedelta(seconds=cumulative_seconds)
                )
            else:
                eto = None

            section_fuels.append(section_fuel)
            automatic_wind_direction = (
                None
                if weather is None
                else self._numeric(weather, "wind_direction_deg_from")
            )
            automatic_wind_speed = (
                None if weather is None else self._numeric(weather, "wind_speed_kt")
            )
            manual_wind_direction = section.manual_wind_direction_deg
            manual_wind_speed = section.manual_wind_speed_kt
            wind_state = ValueState.AUTO
            if section.phase == FlightPhase.VISUAL_ARRIVAL:
                automatic_wind_direction = None
                automatic_wind_speed = 0.0
                manual_wind_direction = None
                manual_wind_speed = None
                wind_state = ValueState.FIXED_RULE
            sections.append(
                SectionResult(
                    section_id=section.id,
                    from_name=geometry.start.name,
                    to_name=geometry.end.name,
                    pressure_altitude_exact_ft=_automatic(exact_pa),
                    pressure_altitude_planning_ft=_automatic(
                        planning_pa,
                        ValueState.FIXED_RULE,
                        {"policy": self.policies.pa_500_policy},
                    ),
                    true_course_deg=course_value,
                    variation_deg_east=_automatic(
                        project.default_variation_deg_east,
                        ValueState.FIXED_RULE,
                    ),
                    magnetic_course_deg=_automatic(magnetic_course),
                    wind_direction_deg_from=_manual_or_automatic(
                        automatic_wind_direction,
                        manual_wind_direction,
                        state=wind_state,
                        metadata=weather_metadata,
                        warnings=weather_warnings,
                    ),
                    wind_speed_kt=_manual_or_automatic(
                        automatic_wind_speed,
                        manual_wind_speed,
                        state=wind_state,
                        metadata=weather_metadata,
                        warnings=weather_warnings,
                    ),
                    wca_deg=_automatic(
                        None if wind_solution is None else wind_solution.wca_deg
                    ),
                    magnetic_heading_deg=_automatic(
                        None
                        if wind_solution is None or magnetic_course is None
                        else (magnetic_course + wind_solution.wca_deg) % 360
                    ),
                    temperature_c=_manual_or_automatic(
                        None if weather is None else self._numeric(weather, "temperature_c"),
                        section.manual_temperature_c,
                        metadata=weather_metadata,
                    ),
                    cas_kt=_automatic(cas, tas_state),
                    tas_kt=_manual_or_automatic(
                        tas if section.manual_tas_kt is None else None,
                        section.manual_tas_kt,
                        state=tas_state,
                        metadata=performance_metadata,
                    ),
                    ground_speed_kt=_automatic(
                        None if wind_solution is None else wind_solution.ground_speed_kt
                    ),
                    zone_distance_nm=distance_value,
                    cumulative_distance_nm=_automatic(cumulative_distance),
                    zone_ete_seconds=_automatic(ete_seconds),
                    cumulative_ete_seconds=_automatic(
                        cumulative_seconds if ete_seconds is not None else None
                    ),
                    eto_utc=_automatic(eto),
                    section_fuel_gal=_automatic(
                        section_fuel,
                        ValueState.PERFORMANCE_TABLE
                        if section.phase in {FlightPhase.CLIMB, FlightPhase.CRUISE}
                        else ValueState.FIXED_RULE,
                    ),
                    remaining_fuel_gal=_unavailable(),
                    performance_metadata=performance_metadata,
                )
            )

        remaining = remaining_fuel(project.total_usable_fuel_gal, section_fuels)
        sections = [
            result.model_copy(
                update={
                    "remaining_fuel_gal": _automatic(value)
                    if value is not None
                    else _unavailable()
                }
            )
            for result, value in zip(sections, remaining, strict=True)
        ]
        return _IterationResult(
            sections,
            self._deduplicate_issues(issues),
            representative_times,
            [geometry.section.phase for geometry in geometries],
            section_fuels,
        )

    @staticmethod
    def _section_weather(
        section: NavSection,
        weather: WeatherResult | None,
    ) -> tuple[float | None, float | None, float | None, dict[str, Any], tuple[str, ...]]:
        metadata = {} if weather is None else weather.metadata | {"values": weather.values}
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
        direction = (
            section.manual_wind_direction_deg
            if section.manual_wind_direction_deg is not None
            else automatic_direction
        )
        speed = (
            section.manual_wind_speed_kt
            if section.manual_wind_speed_kt is not None
            else automatic_speed
        )
        temperature = (
            section.manual_temperature_c
            if section.manual_temperature_c is not None
            else automatic_temperature
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
    ) -> float:
        if index + 1 < len(geometries):
            return geometries[index + 1].section.planned_altitude_ft_msl
        return destination.pattern_altitude_ft_msl or destination.elevation_ft_msl

    def _derived_point_issues_and_results(
        self,
        geometries: list[_Geometry],
        sections: list[SectionResult],
    ) -> tuple[list[Issue], list[DerivedRoutePoint]]:
        issues: list[Issue] = []
        points: list[DerivedRoutePoint] = []
        coordinates = [
            (geometries[0].start.latitude_deg, geometries[0].start.longitude_deg),
            *[(item.end.latitude_deg, item.end.longitude_deg) for item in geometries],
        ]
        cumulative_distances: list[float] = []
        total = 0.0
        for geometry in geometries:
            total += geometry.distance_nm
            cumulative_distances.append(total)

        for geometry, result in zip(geometries, sections, strict=True):
            if geometry.section.phase == FlightPhase.CLIMB:
                gs = result.ground_speed_kt.adopted()
                ete = result.zone_ete_seconds.adopted()
                if gs is None or ete is None:
                    continue
                distance = gs * ete / 3600.0
                projection = point_along_route(coordinates, distance)
                if projection is None:
                    issues.append(
                        self._blocker(
                            "RCA_OUTSIDE_ROUTE",
                            "RCAが計画経路端を越えるため配置できません。",
                            geometry.section.id,
                        )
                    )
                else:
                    containing = geometries[projection.section_index]
                    points.append(
                        DerivedRoutePoint(
                            type=DerivedPointType.RCA,
                            section_id=containing.section.id,
                            latitude_deg=projection.latitude_deg,
                            longitude_deg=projection.longitude_deg,
                            along_route_distance_nm=distance,
                            estimated_time_utc=result.eto_utc.adopted(),
                        )
                    )
                    if projection.section_index > 0:
                        issues.append(
                            Issue(
                                code="RCA_BEYOND_FIRST_TURN",
                                severity=IssueSeverity.WARNING,
                                message="RCAが最初の変針点を越えます。経路を確認してください。",
                                section_id=geometry.section.id,
                                acknowledgement_required=True,
                            )
                        )
                break

        for index, (geometry, result) in enumerate(zip(geometries, sections, strict=True)):
            if geometry.section.phase != FlightPhase.DESCENT:
                continue
            gs = result.ground_speed_kt.adopted()
            ete = result.zone_ete_seconds.adopted()
            if gs is None or ete is None:
                break
            descent_distance = gs * ete / 3600.0
            eoc_route_distance = cumulative_distances[index] - descent_distance
            projection = point_along_route(coordinates, eoc_route_distance)
            if projection is None:
                issues.append(
                    self._blocker(
                        "EOC_OUTSIDE_ROUTE",
                        "EOCが計画経路端を越えるため配置できません。",
                        geometry.section.id,
                    )
                )
            else:
                containing = geometries[projection.section_index]
                points.append(
                    DerivedRoutePoint(
                        type=DerivedPointType.EOC,
                        section_id=containing.section.id,
                        latitude_deg=projection.latitude_deg,
                        longitude_deg=projection.longitude_deg,
                        along_route_distance_nm=eoc_route_distance,
                    )
                )
            break
        return issues, points

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
    def _blocker(code: str, message: str, section_id: UUID | None = None) -> Issue:
        return Issue(
            code=code,
            severity=IssueSeverity.BLOCKER,
            message=message,
            section_id=section_id,
        )

    @staticmethod
    def _deduplicate_issues(issues: list[Issue]) -> list[Issue]:
        output: list[Issue] = []
        seen: set[tuple[str, UUID | None]] = set()
        for issue in issues:
            key = (issue.code, issue.section_id)
            if key not in seen:
                seen.add(key)
                output.append(issue)
        return output

    @staticmethod
    def _status(project: Project, issues: list[Issue]) -> ProjectStatus:
        if any(issue.code == "ROUTE_INCOMPLETE" for issue in issues):
            return ProjectStatus.ROUTE_INCOMPLETE
        if any(issue.severity == IssueSeverity.BLOCKER for issue in issues):
            if any(
                issue.code.startswith(("FORECAST", "WEATHER"))
                for issue in issues
            ):
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

    def _empty_outcome(self, project: Project, issues: list[Issue]) -> CalculationOutcome:
        return CalculationOutcome(
            project_id=project.id,
            selected_forecast_run_id=project.selected_forecast_run_id,
            qnh_hpa=_manual_or_automatic(None, project.manual_qnh_hpa),
            fuel_plan=FuelPlan(total_usable_gal=project.total_usable_fuel_gal),
            issues=self._deduplicate_issues(issues),
            status=self._status(project, issues),
            policy_version=self.policies.version,
            performance_table_version=self.performance.manifest.source_revision,
        )
