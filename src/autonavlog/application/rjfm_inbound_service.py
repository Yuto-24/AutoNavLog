"""Non-authoritative RJFM inbound west-extension guidance.

This adapter deliberately consumes a completed NAV LOG outcome. It does not
change physical route geometry, calculation-zone time, fuel, project status,
or issue severity. A solver/reference failure is represented only in the
returned guidance diagnostic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite

from autonavlog.application.rjfm_inbound_guidance import (
    InboundGuidanceRequest,
    InboundGuidanceSolution,
    InboundGuidanceStatus,
    solve_rjfm_inbound_west_extension,
)
from autonavlog.application.rjfm_inbound_plan import rjfm_inbound_plan_matches_project
from autonavlog.domain.calculation import (
    CalculationOutcome,
    RjfmInboundGuidance,
    RjfmInboundGuidancePoint,
    SectionResult,
)
from autonavlog.domain.enums import FlightPhase
from autonavlog.domain.planning import PersistedUiState, RjfmInboundPlan
from autonavlog.domain.project import NavSection, Project, RouteNode
from autonavlog.nav.variation import variation_for_departure_latitude
from autonavlog.storage.rjfm_inbound_reference import (
    GeoPoint,
    RjfmInboundGuidanceReference,
)

_OPERATIONAL_DESCENT_TYPE = "rjfm_inbound_operational_descent"


@dataclass(frozen=True)
class _SolverInputs:
    umk: GeoPoint
    vrep: GeoPoint
    required_ete_min: float
    descent_tas_kt: float
    wind_direction_deg_true_from: float | None
    wind_speed_kt: float
    magnetic_variation_deg_east: float
    turn_altitude_ft_msl: float
    descent_start_altitude_ft_msl: float
    target_altitude_ft_msl: float
    descent_rate_fpm: float


InboundSolver = Callable[[InboundGuidanceRequest], InboundGuidanceSolution]


def build_rjfm_inbound_guidance(
    project: Project,
    outcome: CalculationOutcome,
    state: PersistedUiState,
    reference: RjfmInboundGuidanceReference | None,
    *,
    generated_against_fingerprint: str,
    solver: InboundSolver = solve_rjfm_inbound_west_extension,
) -> RjfmInboundGuidance | None:
    """Return warning-only diagnostics and never let adapter failures affect NAV LOG."""

    try:
        return _build_rjfm_inbound_guidance(
            project,
            outcome,
            state,
            reference,
            generated_against_fingerprint=generated_against_fingerprint,
            solver=solver,
        )
    except Exception:
        return _unavailable(
            "GUIDANCE_ADAPTER_FAILURE",
            "西方延長案内を安全に表示できません。NAV LOGは保持しています。",
            generated_against_fingerprint,
            reference=reference,
        )


def _build_rjfm_inbound_guidance(
    project: Project,
    outcome: CalculationOutcome,
    state: PersistedUiState,
    reference: RjfmInboundGuidanceReference | None,
    *,
    generated_against_fingerprint: str,
    solver: InboundSolver = solve_rjfm_inbound_west_extension,
) -> RjfmInboundGuidance | None:
    """Build one transient diagnostic from current, authoritative NAV LOG inputs.

    The caller must not persist the result. Persisting a computed turn point
    would allow route, forecast, rate, altitude, or reference edits to revive
    stale numeric guidance after reload.
    """

    plan = state.rjfm_inbound_plan
    if plan is None:
        return None
    if outcome.project_id != project.id:
        return _unavailable(
            "OUTCOME_PROJECT_MISMATCH",
            "現在のProjectに属さない計算結果のため、西方延長案内を表示できません。",
            generated_against_fingerprint,
        )
    if not _outcome_matches_current_plan(project, outcome, plan):
        return _unavailable(
            "INBOUND_PLAN_STALE",
            "経路、高度、または降下率が再計算前のため、西方延長案内を表示できません。",
            generated_against_fingerprint,
        )
    if reference is None:
        return _unavailable(
            "REFERENCE_LOAD_FAILED",
            "西方延長案内の参照パックを読み込めません。NAV LOGは保持しています。",
            generated_against_fingerprint,
        )
    if not reference.available_for_solver:
        return _unavailable(
            reference.reason_code or "KS43_REFERENCE_UNAVAILABLE",
            reference.message
            or "KS4-3境界の計算用参照が未検証のため、西方延長案内は利用できません。",
            generated_against_fingerprint,
            reference=reference,
        )
    inputs = _solver_inputs(project, outcome, plan)
    if inputs is None:
        return _unavailable(
            "AUTHORITATIVE_DESCENT_INPUTS_UNAVAILABLE",
            "採用済みのRJFM運用降下入力を取得できないため、西方延長案内を表示できません。",
            generated_against_fingerprint,
            reference=reference,
        )
    assert reference.boundary is not None
    try:
        solution = solver(
            InboundGuidanceRequest(
                umk=inputs.umk,
                vrep=inputs.vrep,
                required_ete_min=inputs.required_ete_min,
                descent_tas_kt=inputs.descent_tas_kt,
                wind_direction_deg_true_from=inputs.wind_direction_deg_true_from,
                wind_speed_kt=inputs.wind_speed_kt,
                magnetic_variation_deg_east=inputs.magnetic_variation_deg_east,
                mze=reference.mze_position,
                mze_elevation_ft_msl=reference.mze_elevation_ft_msl,
                turn_altitude_ft_msl=inputs.turn_altitude_ft_msl,
                descent_start_altitude_ft_msl=inputs.descent_start_altitude_ft_msl,
                target_altitude_ft_msl=inputs.target_altitude_ft_msl,
                descent_rate_fpm=inputs.descent_rate_fpm,
                bearing_min_magnetic_deg=reference.policy.magnetic_bearing_min_deg,
                bearing_max_magnetic_deg=reference.policy.magnetic_bearing_max_deg,
                boundary=reference.boundary.polygon_vertices,
                boundary_model_error_nm=reference.boundary.maximum_model_error_nm,
                reference_revision=reference.revision,
                dme_increment_nm=reference.policy.dme_rounding_increment_nm,
                coarse_bearing_step_deg=reference.policy.coarse_bearing_step_deg,
            )
        )
    except Exception:
        return _unavailable(
            "SOLVER_EXCEPTION",
            "西方延長案内の探索中に失敗しました。NAV LOGは保持しています。",
            generated_against_fingerprint,
            reference=reference,
        )
    if solution.reference_revision != reference.revision:
        return _unavailable(
            "REFERENCE_REVISION_MISMATCH",
            "西方延長案内の解と参照パックの版が一致しないため、表示を抑止しました。",
            generated_against_fingerprint,
            reference=reference,
        )
    return _from_solution(solution, reference, generated_against_fingerprint)


def _outcome_matches_current_plan(
    project: Project,
    outcome: CalculationOutcome,
    plan: RjfmInboundPlan,
) -> bool:
    """Require the completed outcome to cover every current UMK→VREP source leg."""

    arrival = outcome.arrival_altitude
    adopted_altitude = None if arrival is None else arrival.adopted_altitude_ft_msl
    # Reuse the full persisted-plan matcher before examining the result. A
    # matching UMK/VREP pair alone could have lost a controlled OMARU→UMK leg.
    if not rjfm_inbound_plan_matches_project(project, plan):
        return False
    if plan.adopted_vrep_altitude_ft_msl != adopted_altitude:
        return False
    controlled_ids = plan.controlled_section_ids or (plan.controlled_section_id,)
    project_sections = {section.id: section for section in project.ordered_sections()}
    if any(
        section_id not in project_sections
        or project_sections[section_id].planned_altitude_ft_msl != 4500.0
        or project_sections[section_id].phase is not FlightPhase.CRUISE
        for section_id in controlled_ids
    ):
        return False

    expected = _umk_to_vrep_sections(project, plan)
    operational = tuple(
        section
        for section in outcome.sections
        if section.phase is FlightPhase.DESCENT
        and section.performance_metadata.get("type") == _OPERATIONAL_DESCENT_TYPE
    )
    if not expected or not operational:
        return False
    metadata = operational[0].performance_metadata
    if (
        metadata.get("rjfm_inbound_rule_version") != plan.rule_version
        or metadata.get("descent_rate_fpm") != float(plan.descent_rate_fpm)
        or metadata.get("cruise_altitude_ft_msl") != 4500.0
        or metadata.get("target_altitude_ft_msl") != adopted_altitude
        or metadata.get("eoc_source_section_id") != str(expected[0].id)
    ):
        return False

    # Checkpoint projections may split a physical source section into several
    # calculation zones. Collapse only adjacent equal IDs, then require the
    # complete physical UMK→VREP slice in its original order.
    grouped = _group_operational_sections(operational)
    if [group[0].section_id for group in grouped] != [section.id for section in expected]:
        return False
    for expected_section, group in zip(expected, grouped, strict=True):
        start = _route_node(project, expected_section.from_node_id)
        end = _route_node(project, expected_section.to_node_id)
        first = group[0]
        last = group[-1]
        if start is None or end is None:
            return False
        if (
            first.from_node_id != start.id
            or last.to_node_id != end.id
            or not _coordinates_match(
                first.from_latitude_deg,
                first.from_longitude_deg,
                start.latitude_deg,
                start.longitude_deg,
            )
            or not _coordinates_match(
                last.to_latitude_deg,
                last.to_longitude_deg,
                end.latitude_deg,
                end.longitude_deg,
            )
        ):
            return False
        if any(
            current.section_id != expected_section.id
            or (
                previous.to_latitude_deg != current.from_latitude_deg
                or previous.to_longitude_deg != current.from_longitude_deg
            )
            for previous, current in zip(group, group[1:], strict=False)
        ):
            return False
    return True


def _umk_to_vrep_sections(
    project: Project,
    plan: RjfmInboundPlan,
) -> tuple[NavSection, ...]:
    sections = project.ordered_sections()
    nodes = project.ordered_nodes()
    node_index = {node.id: index for index, node in enumerate(nodes)}
    try:
        start = node_index[plan.umk_node_id]
        end = node_index[plan.vrep_node_id]
    except KeyError:
        return ()
    if end <= start or end > len(sections):
        return ()
    slice_ = tuple(sections[start:end])
    if not slice_:
        return ()
    expected_nodes = nodes[start : end + 1]
    if any(
        section.from_node_id != left.id or section.to_node_id != right.id
        for section, left, right in zip(
            slice_, expected_nodes[:-1], expected_nodes[1:], strict=True
        )
    ):
        return ()
    return slice_


def _group_operational_sections(
    sections: tuple[SectionResult, ...],
) -> tuple[tuple[SectionResult, ...], ...]:
    groups: list[list[SectionResult]] = []
    for section in sections:
        if not groups or groups[-1][-1].section_id != section.section_id:
            groups.append([section])
        else:
            groups[-1].append(section)
    return tuple(tuple(group) for group in groups)


def _coordinates_match(
    latitude: float | None,
    longitude: float | None,
    expected_latitude: float,
    expected_longitude: float,
) -> bool:
    return (
        latitude is not None
        and longitude is not None
        and latitude == expected_latitude
        and longitude == expected_longitude
    )


def _solver_inputs(
    project: Project,
    outcome: CalculationOutcome,
    plan: RjfmInboundPlan,
) -> _SolverInputs | None:
    umk = _route_node(project, plan.umk_node_id)
    vrep = _route_node(project, plan.vrep_node_id)
    if umk is None or vrep is None:
        return None
    descent = tuple(
        section
        for section in outcome.sections
        if section.phase is FlightPhase.DESCENT
        and section.performance_metadata.get("type") == _OPERATIONAL_DESCENT_TYPE
    )
    if not descent:
        return None
    metadata = descent[0].performance_metadata
    duration_seconds = _finite_number(metadata.get("planned_duration_seconds"))
    turn_altitude = _finite_number(metadata.get("cruise_altitude_ft_msl"))
    target_altitude = _finite_number(metadata.get("target_altitude_ft_msl"))
    descent_rate = _finite_number(metadata.get("descent_rate_fpm"))
    wind_section_id = metadata.get("descent_wind_source_section_id")
    if not isinstance(wind_section_id, str):
        return None
    wind_source = next(
        (section for section in descent if str(section.section_id) == wind_section_id),
        None,
    )
    if wind_source is None:
        return None
    tas = _finite_number(wind_source.tas_kt.adopted())
    wind_speed = _finite_number(wind_source.wind_speed_kt.adopted())
    direction = _finite_number(wind_source.wind_direction_deg_from.adopted())
    if (
        duration_seconds is None
        or turn_altitude is None
        or target_altitude is None
        or descent_rate is None
        or tas is None
        or wind_speed is None
    ):
        return None
    if wind_speed > 0 and direction is None:
        return None
    if duration_seconds <= 0 or tas <= 0 or descent_rate <= 0 or turn_altitude < target_altitude:
        return None
    return _SolverInputs(
        umk=_point(umk),
        vrep=_point(vrep),
        required_ete_min=duration_seconds / 60.0,
        descent_tas_kt=tas,
        wind_direction_deg_true_from=direction,
        wind_speed_kt=wind_speed,
        magnetic_variation_deg_east=float(
            variation_for_departure_latitude(umk.latitude_deg).degrees_east
        ),
        turn_altitude_ft_msl=turn_altitude,
        descent_start_altitude_ft_msl=turn_altitude,
        target_altitude_ft_msl=target_altitude,
        descent_rate_fpm=descent_rate,
    )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        numeric = float(value)
    except ValueError:
        return None
    return numeric if isfinite(numeric) else None


def _route_node(project: Project, node_id: object) -> RouteNode | None:
    return next((node for node in project.route_nodes if node.id == node_id), None)


def _point(node: RouteNode) -> GeoPoint:
    return GeoPoint(latitude_deg=node.latitude_deg, longitude_deg=node.longitude_deg)


def _unavailable(
    reason_code: str,
    message: str,
    generated_against_fingerprint: str,
    *,
    reference: RjfmInboundGuidanceReference | None = None,
) -> RjfmInboundGuidance:
    return RjfmInboundGuidance(
        status="UNAVAILABLE",
        reason_code=reason_code,
        message=message,
        generated_against_fingerprint=generated_against_fingerprint,
        reference_revision=None if reference is None else reference.revision,
        reference_content_fingerprint=(
            None if reference is None else reference.content_fingerprint
        ),
    )


def _from_solution(
    solution: InboundGuidanceSolution,
    reference: RjfmInboundGuidanceReference,
    generated_against_fingerprint: str,
) -> RjfmInboundGuidance:
    if solution.status is not InboundGuidanceStatus.AVAILABLE:
        return _unavailable(
            solution.reason_code or solution.status.value,
            _solution_message(solution.status),
            generated_against_fingerprint,
            reference=reference,
        ).model_copy(update={"status": solution.status.value})
    if not _complete_available_solution(solution):
        return _unavailable(
            "AVAILABLE_SOLUTION_INCOMPLETE",
            "西方延長案内の解に表示可能な丸め後の地点がありません。",
            generated_against_fingerprint,
            reference=reference,
        )
    return RjfmInboundGuidance(
        status="AVAILABLE",
        reason_code=None,
        message=(
            "UMK通過後の西方延長案内を生成しました。丸め後のMZE DME地点からVREPへ向かってください。"
        ),
        generated_against_fingerprint=generated_against_fingerprint,
        reference_revision=reference.revision,
        reference_content_fingerprint=reference.content_fingerprint,
        raw_turn_point=_guidance_point(solution.raw_turn_point),
        rounded_turn_point=_guidance_point(solution.rounded_turn_point),
        bearing_magnetic_deg=solution.bearing_magnetic_deg,
        actual_bearing_magnetic_deg=solution.actual_bearing_magnetic_deg,
        raw_extra_distance_nm=solution.raw_extra_distance_nm,
        extra_distance_nm=solution.extra_distance_nm,
        raw_predicted_ete_min=solution.raw_predicted_ete_min,
        predicted_ete_min=solution.predicted_ete_min,
        raw_dme_nm=solution.raw_dme_nm,
        rounded_dme_nm=solution.rounded_dme_nm,
        raw_turn_altitude_ft_msl=solution.raw_turn_altitude_ft_msl,
        rounded_turn_altitude_ft_msl=solution.rounded_turn_altitude_ft_msl,
        raw_minimum_boundary_clearance_nm=solution.raw_minimum_boundary_clearance_nm,
        minimum_boundary_clearance_nm=solution.minimum_boundary_clearance_nm,
    )


def _guidance_point(point: GeoPoint | None) -> RjfmInboundGuidancePoint | None:
    if point is None:
        return None
    return RjfmInboundGuidancePoint(
        latitude_deg=point.latitude_deg,
        longitude_deg=point.longitude_deg,
    )


def _complete_available_solution(solution: InboundGuidanceSolution) -> bool:
    """Reject malformed solver output before it crosses the diagnostic boundary."""

    raw_point = solution.raw_turn_point
    rounded_point = solution.rounded_turn_point
    if raw_point is None or rounded_point is None:
        return False
    points = (raw_point, rounded_point)
    if not all(
        isfinite(value) for point in points for value in (point.latitude_deg, point.longitude_deg)
    ):
        return False
    if not all(
        -90.0 <= point.latitude_deg <= 90.0 and -180.0 <= point.longitude_deg <= 180.0
        for point in points
    ):
        return False
    numeric = (
        solution.bearing_magnetic_deg,
        solution.actual_bearing_magnetic_deg,
        solution.raw_extra_distance_nm,
        solution.extra_distance_nm,
        solution.raw_predicted_ete_min,
        solution.predicted_ete_min,
        solution.raw_dme_nm,
        solution.rounded_dme_nm,
        solution.raw_turn_altitude_ft_msl,
        solution.rounded_turn_altitude_ft_msl,
        solution.raw_minimum_boundary_clearance_nm,
        solution.minimum_boundary_clearance_nm,
    )
    if not all(value is not None and isfinite(value) for value in numeric):
        return False
    return all(value is not None and value >= 0.0 for value in numeric[2:])


def _solution_message(status: InboundGuidanceStatus) -> str:
    return {
        InboundGuidanceStatus.UNAVAILABLE: "西方延長案内の計算用参照が利用できません。",
        InboundGuidanceStatus.ADVERSE_WIND: "現在の降下風では西方延長案内を成立させられません。",
        InboundGuidanceStatus.NO_SOLUTION: (
            "KS4-3を回避して必要な降下時間を満たす西方延長案内が見つかりません。"
        ),
        InboundGuidanceStatus.CONVERGENCE_FAILURE: "西方延長案内の探索が収束しませんでした。",
        InboundGuidanceStatus.AVAILABLE: "",
    }[status]
