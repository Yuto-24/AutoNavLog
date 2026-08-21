from __future__ import annotations

from collections.abc import Iterable
from math import ceil
from typing import Any, Literal, cast

from autonavlog.application.rjfm_departure_guidance import (
    ClimbProfilePoint,
    ConstraintSeverity,
    DepartureGuidanceRequest,
    GeoPoint,
    GuidanceCandidate,
    GuidanceStatus,
    Navaid,
    PcaRegion,
    PohAltitudeTimeProfile,
    RunwayProcedure,
    TurnDirection,
    TurnModel,
    Wind,
    generate_rjfm_departure_guidance,
)
from autonavlog.application.rjfm_departure_plan import (
    RjfmPlanReferences,
    apply_rjfm_departure_exception,
    rjfm_plan_matches_project,
)
from autonavlog.domain.calculation import CalculationOutcome, SectionResult
from autonavlog.domain.enums import FlightPhase
from autonavlog.domain.planning import (
    PersistedUiState,
    RjfmConstraintResult,
    RjfmCoordinate,
    RjfmDepartureGuidance,
    RjfmDeparturePlan,
    RjfmGuidancePathPoint,
    RjfmGuidanceStatus,
    RjfmRunwayGuidance,
    RjfmTurnDirection,
    RjfmTurnMethod,
)
from autonavlog.domain.project import Project
from autonavlog.nav.variation import variation_for_departure_latitude
from autonavlog.performance.climb import ClimbCalculator, ClimbPerformanceError
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.storage.rjfm_reference import (
    RjfmReferencePack,
    RunwayDeparturePolicy,
)

MAX_DISPLAY_PATH_POINTS = 600


def normalize_rjfm_departure_plan(
    project: Project,
    reference_pack: RjfmReferencePack,
) -> RjfmDeparturePlan | None:
    """Apply the coordinate-triggered RJFM exception to one editable project."""

    return apply_rjfm_departure_exception(
        project,
        RjfmPlanReferences(
            revision=reference_pack.revision,
            content_fingerprint=reference_pack.content_fingerprint,
            umk=_reference_coordinate(reference_pack, "UMK"),
            over_field=_reference_coordinate(reference_pack, "OVER_FIELD"),
            omaru=_reference_coordinate(reference_pack, "OMARU"),
        ),
    )


def build_rjfm_departure_guidance(
    project: Project,
    outcome: CalculationOutcome,
    state: PersistedUiState,
    performance: PerformanceRepository,
    reference_pack: RjfmReferencePack,
    *,
    generated_against_fingerprint: str,
) -> RjfmDepartureGuidance | None:
    """Build both-runway guidance from the same adopted CLIMB inputs as NAV LOG.

    The returned guidance is deliberately diagnostic.  Its status never becomes
    a ``CalculationOutcome.issues`` blocker and it never changes route totals.
    """

    plan = state.rjfm_departure_plan
    if plan is None:
        return None
    limitations = _limitations(reference_pack)
    if outcome.project_id != project.id:
        center_route = [
            _reference_coordinate(reference_pack, point_id)
            for point_id in reference_pack.policy.center_route_sequence
        ]
        return _unavailable_guidance(
            plan,
            reference_pack,
            generated_against_fingerprint,
            center_route,
            limitations,
            "計算結果が現在のProjectに属していないため、出発案内を生成できません。",
        )
    if (
        plan.reference_revision != reference_pack.revision
        or plan.reference_content_fingerprint != reference_pack.content_fingerprint
        or not rjfm_plan_matches_project(project, plan)
    ):
        center_route = [
            _reference_coordinate(reference_pack, point_id)
            for point_id in reference_pack.policy.center_route_sequence
        ]
        return _unavailable_guidance(
            plan,
            reference_pack,
            generated_against_fingerprint,
            center_route,
            limitations,
            "保存済みRJFM出発例外が現在の経路または参照パックと一致しません。"
            "経路を再正規化してから再計算してください。",
        )
    center_route = [plan.umk, plan.over_field, plan.omaru]
    climb = _adopted_climb_result(outcome.sections)
    if climb is None:
        return _unavailable_guidance(
            plan,
            reference_pack,
            generated_against_fingerprint,
            center_route,
            limitations,
            "採用済みのCLIMB計算結果がないため、出発案内を生成できません。",
        )
    adopted = _adopted_climb_inputs(climb)
    if adopted is None:
        return _unavailable_guidance(
            plan,
            reference_pack,
            generated_against_fingerprint,
            center_route,
            limitations,
            "CLIMBの風・TAS・POH上昇時間が未確定のため、出発案内を生成できません。",
        )
    wind_direction, wind_speed, tas_kt, target_time_seconds, temperature_c, weight_lb = adopted
    try:
        profile = _poh_profile(
            performance,
            departure_altitude_ft=float(reference_pack.airport.elevation_ft_msl),
            target_altitude_ft=float(plan.target_altitude_ft_msl),
            target_time_seconds=target_time_seconds,
            temperature_c=temperature_c,
            weight_lb=weight_lb,
            extra_altitudes=(
                float(reference_pack.airport.traffic_pattern_altitude_ft_msl),
            ),
        )
    except (ClimbPerformanceError, TypeError, ValueError) as error:
        return _unavailable_guidance(
            plan,
            reference_pack,
            generated_against_fingerprint,
            center_route,
            limitations,
            f"POH上昇プロファイルを構成できません: {error}",
        )

    variation = variation_for_departure_latitude(
        float(reference_pack.runway.center.latitude_deg)
    ).degrees_east
    direct_ground_speed = climb.ground_speed_kt.adopted()
    direct_time_seconds = (
        None
        if direct_ground_speed is None or float(direct_ground_speed) <= 0
        else float(plan.virtual_rca_distance_nm) / float(direct_ground_speed) * 3600.0
    )
    runways: list[RjfmRunwayGuidance] = []
    for runway in ("09", "27"):
        runway_policy = reference_pack.policy.runways[runway]
        solved = generate_rjfm_departure_guidance(
            DepartureGuidanceRequest(
                runway_origin=_geo(reference_pack.runway.center),
                runway_elevation_ft=float(reference_pack.airport.elevation_ft_msl),
                target_umk=_geo(plan.umk),
                target_altitude_ft=float(plan.target_altitude_ft_msl),
                mze=Navaid(
                    position=_geo(reference_pack.mze.position),
                    antenna_elevation_ft=float(reference_pack.mze.elevation_ft_msl),
                    station_declination_deg_east=float(
                        reference_pack.mze.station_declination_deg
                    ),
                ),
                wind=Wind(
                    direction_deg_true_from=wind_direction,
                    speed_kt=wind_speed,
                ),
                tas_kt=tas_kt,
                climb_profile=profile,
                magnetic_variation_deg_east=float(variation),
                runway_procedure=_runway_procedure(
                    runway_policy,
                    float(reference_pack.policy.turn_bank_angle_deg),
                ),
                pca_region=_pca_region(reference_pack),
                # Constraint checks retain quarter-second sampling.  Only the
                # persisted display polyline is reduced after classification.
                sample_interval_s=0.25,
            )
        )
        runways.append(
            _map_runway_guidance(
                runway,
                TurnDirection(runway_policy.extension_turn_direction),
                solved.status,
                solved.selected_candidate,
                solved.issues,
                direct_time_seconds,
            )
        )
    return RjfmDepartureGuidance(
        reference_revision=reference_pack.revision,
        reference_content_fingerprint=reference_pack.content_fingerprint,
        source_effective_dates=_source_effective_dates(reference_pack),
        generated_against_fingerprint=generated_against_fingerprint,
        candidates=runways,
        center_route=center_route,
        limitations=limitations,
    )


def _reference_coordinate(pack: RjfmReferencePack, point_id: str) -> RjfmCoordinate:
    point = pack.points[point_id]
    return RjfmCoordinate(
        latitude_deg=point.position.latitude_deg,
        longitude_deg=point.position.longitude_deg,
        source=f"RJFM_REFERENCE:{pack.revision}:{point.validation_status}",
        estimated_error_nm=point.estimated_error_nm,
    )


def _geo(point: Any) -> GeoPoint:
    return GeoPoint(
        latitude_deg=float(point.latitude_deg),
        longitude_deg=float(point.longitude_deg),
    )


def _adopted_climb_result(sections: Iterable[SectionResult]) -> SectionResult | None:
    return next(
        (
            section
            for section in sections
            if section.phase == FlightPhase.CLIMB
            and section.performance_metadata.get("type") == "climb"
        ),
        None,
    )


def _adopted_climb_inputs(
    climb: SectionResult,
) -> tuple[float | None, float, float, float, float, float] | None:
    wind_speed = climb.wind_speed_kt.adopted()
    tas = climb.tas_kt.adopted()
    metadata = climb.performance_metadata
    target_time = metadata.get("planned_duration_seconds")
    temperature = metadata.get("midpoint_temperature_c")
    weight = metadata.get("weight_lb")
    if wind_speed is None or tas is None:
        return None
    if target_time is None or temperature is None or weight is None:
        return None
    wind_direction = climb.wind_direction_deg_from.adopted()
    if float(wind_speed) > 0 and wind_direction is None:
        return None
    return (
        None if wind_direction is None else float(wind_direction),
        float(wind_speed),
        float(tas),
        float(target_time),
        float(temperature),
        float(weight),
    )


def _poh_profile(
    performance: PerformanceRepository,
    *,
    departure_altitude_ft: float,
    target_altitude_ft: float,
    target_time_seconds: float,
    temperature_c: float,
    weight_lb: float,
    extra_altitudes: tuple[float, ...],
) -> PohAltitudeTimeProfile:
    calculator = ClimbCalculator(
        performance.climb_rows,
        performance.manifest.climb_temperature_policy,
    )
    altitudes = {
        departure_altitude_ft,
        target_altitude_ft,
        *extra_altitudes,
        *(
            float(row.pressure_altitude_ft)
            for row in performance.climb_rows
            if departure_altitude_ft < row.pressure_altitude_ft < target_altitude_ft
        ),
    }
    ordered = sorted(
        altitude
        for altitude in altitudes
        if departure_altitude_ft <= altitude <= target_altitude_ft
    )
    raw: list[tuple[float, float]] = [(departure_altitude_ft, 0.0)]
    for altitude in ordered[1:]:
        climb = calculator.calculate(
            departure_altitude_ft,
            altitude,
            temperature_c,
            weight_lb,
        )
        raw.append((altitude, climb.time_min))
    if len(raw) < 2 or raw[-1][1] <= 0 or target_time_seconds <= 0:
        raise ValueError("5500 ftまでのPOH上昇時間が正値ではありません")
    scale = target_time_seconds / 60.0 / raw[-1][1]
    return PohAltitudeTimeProfile(
        points=tuple(
            ClimbProfilePoint(
                altitude_ft=altitude,
                cumulative_time_min=time_min * scale,
            )
            for altitude, time_min in raw
        )
    )


def _pca_region(pack: RjfmReferencePack) -> PcaRegion:
    return PcaRegion(
        polygon_vertices=tuple(_geo(point) for point in pack.pca.polygon_vertices),
        exclusion_center=_geo(pack.pca.exclusion_center),
        exclusion_radius_nm=float(pack.pca.exclusion_radius_km) / 1.852,
        floor_altitude_ft=float(pack.pca.operational_altitude_lower_ft_msl),
        ceiling_altitude_ft=float(pack.pca.operational_altitude_upper_ft_msl),
    )


def _runway_procedure(
    policy: RunwayDeparturePolicy,
    bank_deg: float,
) -> RunwayProcedure:
    return RunwayProcedure(
        runway_id=policy.runway_id,
        initial_ground_course_magnetic_deg=float(policy.initial_magnetic_course_deg),
        post_cut_ground_course_magnetic_deg=float(policy.post_cut_magnetic_course_deg),
        initial_turn_direction=TurnDirection(policy.initial_turn_direction),
        extension_turn_direction=TurnDirection(policy.extension_turn_direction),
        initial_distance_nm=(
            None
            if policy.initial_straight_distance_nm is None
            else float(policy.initial_straight_distance_nm)
        ),
        initial_until_altitude_ft=(
            None
            if policy.initial_straight_until_altitude_ft_msl is None
            else float(policy.initial_straight_until_altitude_ft_msl)
        ),
        bank_deg=bank_deg,
    )


def _map_runway_guidance(
    runway: Literal["09", "27"],
    turn_direction: TurnDirection,
    result_status: GuidanceStatus,
    candidate: GuidanceCandidate | None,
    issues: tuple[str, ...],
    direct_time_seconds: float | None,
) -> RjfmRunwayGuidance:
    if candidate is None:
        detail = " / ".join(issues) or "物理的に成立する経路解がありません。"
        return RjfmRunwayGuidance(
            runway=runway,
            status=RjfmGuidanceStatus.UNAVAILABLE,
            turn_direction=RjfmTurnDirection(turn_direction.value),
            constraints=[
                RjfmConstraintResult(
                    code=result_status.value,
                    passed=False,
                    hard=True,
                    message=f"出発案内を生成できません: {detail}",
                )
            ],
            notes=list(issues),
        )
    status = {
        GuidanceStatus.VALID: RjfmGuidanceStatus.VALID,
        GuidanceStatus.WARNING: RjfmGuidanceStatus.WARNING,
        GuidanceStatus.INVALID: RjfmGuidanceStatus.HARD_INVALID,
        GuidanceStatus.NO_SOLUTION: RjfmGuidanceStatus.UNAVAILABLE,
        GuidanceStatus.UNSUPPORTED: RjfmGuidanceStatus.UNAVAILABLE,
    }[candidate.status]
    method = (
        RjfmTurnMethod.FIXED_BANK_20
        if candidate.model == TurnModel.FIXED_BANK_AIR_MASS
        else RjfmTurnMethod.ADJUSTED_MAX_RADIUS
    )
    expected_delta = (
        None
        if direct_time_seconds is None
        else candidate.target_elapsed_time_s - direct_time_seconds
    )
    return RjfmRunwayGuidance(
        runway=runway,
        status=status,
        turn_method=method,
        path=[
            RjfmGuidancePathPoint(
                latitude_deg=sample.position.latitude_deg,
                longitude_deg=sample.position.longitude_deg,
                altitude_ft_msl=sample.altitude_ft,
                elapsed_seconds=sample.elapsed_time_s,
                segment=sample.phase.value,
            )
            for sample in _downsample_path(candidate.path)
        ],
        constraints=[
            RjfmConstraintResult(
                code=constraint.code,
                passed=constraint.passed,
                hard=constraint.severity is ConstraintSeverity.HARD,
                message=_constraint_message(constraint.code, constraint.passed),
                metadata=_constraint_metadata(candidate, constraint),
            )
            for constraint in candidate.constraints
        ],
        turn_direction=RjfmTurnDirection(candidate.turn_direction.value),
        full_turns=candidate.full_turns,
        partial_turn_deg=candidate.partial_turn_angle_deg,
        turn_entry_radial_deg=candidate.mze_radial_deg,
        turn_entry_dme_nm=candidate.mze_dme_nm,
        turn_entry_altitude_ft_msl=candidate.turn_entry_altitude_ft,
        exit_drift_nm=candidate.full_turn_exit_drift_nm,
        expected_time_delta_seconds=expected_delta,
        position_residual_nm=candidate.position_residual_nm,
        altitude_residual_ft=candidate.altitude_residual_ft,
        tangent_residual_deg=candidate.tangent_residual_deg,
        notes=list(issues),
    )


def _downsample_path(path: tuple[Any, ...]) -> tuple[Any, ...]:
    if len(path) <= MAX_DISPLAY_PATH_POINTS:
        return path
    stride = ceil((len(path) - 1) / (MAX_DISPLAY_PATH_POINTS - 1))
    indices = list(range(0, len(path), stride))
    if indices[-1] != len(path) - 1:
        indices.append(len(path) - 1)
    selected = tuple(path[index] for index in indices)
    if len(selected) > MAX_DISPLAY_PATH_POINTS:
        raise RuntimeError("RJFM display path downsampling exceeded its point limit")
    return selected


def _constraint_metadata(candidate: GuidanceCandidate, constraint: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {"solver_message": constraint.message}
    index = constraint.sample_index
    if index is None or not 0 <= index < len(candidate.path):
        return metadata
    sample = candidate.path[index]
    metadata["violation_sample"] = {
        "latitude_deg": sample.position.latitude_deg,
        "longitude_deg": sample.position.longitude_deg,
        "altitude_ft_msl": sample.altitude_ft,
        "elapsed_seconds": sample.elapsed_time_s,
    }
    return metadata


def _constraint_message(code: str, passed: bool) -> str:
    label = {
        "OUTBOUND_MC": "延長直線の磁針路",
        "DIRECT_UMK_MC": "最終UMK直線の磁針路",
        "PCA": "宮崎特別管制区の対象高度帯",
        "POSITION_RESIDUAL": "UMK位置一致",
        "ALTITUDE_RESIDUAL": "UMK 5500 ft一致",
        "TANGENT_RESIDUAL": "延長旋回からUMK直線への接線接続",
    }.get(code, code)
    return f"{label}: {'適合' if passed else '不適合'}"


def _source_effective_dates(pack: RjfmReferencePack) -> dict[str, str]:
    labels = {
        "cac-rjfm-training-r6-5-1": "訓練要領",
        "aip-rjfm-2026-03-01-public-mirror": "AIP RJFM",
        "mlit-special-control-area-consolidated-2024-02-08": "PCA告示",
        "user-approved-rjfm-guidance-policy-2026-08-16": "運用設定",
        "user-approved-rjfm-rwy-turn-policy-2026-08-17": "RWY別旋回設定",
    }
    return {
        labels.get(source.id, source.id): source.effective_date
        for source in pack.data.sources
        if source.effective_date is not None and source.id in labels
    }


def _limitations(pack: RjfmReferencePack) -> list[str]:
    point_error = max(float(point.estimated_error_nm) for point in pack.points.values())
    return [
        (
            "UMK・OVER FIELD・OMARUは添付図からの未検証デジタイズです"
            f"（推定誤差 最大{point_error:.2f} NM）。KML座標がある点はKMLを優先します。"
        ),
        (
            "PCA判定の656–2700 ft MSL（上下端を含む）は利用者承認の運用値で、"
            "告示200–800 mの厳密換算値ではありません。"
        ),
        "地形・障害物・未定義の他空域・ATC指示は経路ソルバーの判定対象外です。",
    ]


def _unavailable_guidance(
    plan: RjfmDeparturePlan,
    pack: RjfmReferencePack,
    fingerprint: str,
    center_route: list[RjfmCoordinate],
    limitations: list[str],
    reason: str,
) -> RjfmDepartureGuidance:
    candidates = [
        RjfmRunwayGuidance(
            runway=cast(Literal["09", "27"], runway),
            status=RjfmGuidanceStatus.UNAVAILABLE,
            turn_direction=RjfmTurnDirection(
                pack.policy.runways[runway].extension_turn_direction
            ),
            constraints=[
                RjfmConstraintResult(
                    code="GUIDANCE_INPUT_UNAVAILABLE",
                    passed=False,
                    hard=True,
                    message=reason,
                )
            ],
            notes=[reason],
        )
        for runway in ("09", "27")
    ]
    return RjfmDepartureGuidance(
        reference_revision=pack.revision,
        reference_content_fingerprint=pack.content_fingerprint,
        source_effective_dates=_source_effective_dates(pack),
        generated_against_fingerprint=fingerprint,
        candidates=candidates,
        center_route=center_route,
        limitations=limitations,
    )


__all__ = [
    "build_rjfm_departure_guidance",
    "normalize_rjfm_departure_plan",
]
