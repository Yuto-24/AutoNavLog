"""Standalone west-extension optimizer; it never mutates NAV LOG route distance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import ceil, hypot, isfinite

from autonavlog.application.rjfm_inbound_geometry import (
    GeometryUnsupportedError,
    polyline_boundary_metrics,
    sample_geodesic_points,
)
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
from autonavlog.nav.wind_triangle import WindTriangleError, solve_wind_triangle
from autonavlog.storage.rjfm_inbound_reference import GeoPoint

_EPSILON = 1e-9
_MIN_BEARING_EVALUATIONS = 1
_MIN_DISTANCE_EVALUATIONS = 3
_VERTICAL_FEET_PER_NM = 6076.11548556
_INITIAL_DISTANCE_SCAN_POINTS = 17


class InboundGuidanceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    ADVERSE_WIND = "ADVERSE_WIND"
    NO_SOLUTION = "NO_SOLUTION"
    CONVERGENCE_FAILURE = "CONVERGENCE_FAILURE"


@dataclass(frozen=True)
class InboundGuidanceRequest:
    umk: GeoPoint
    vrep: GeoPoint
    required_ete_min: float
    descent_tas_kt: float
    wind_direction_deg_true_from: float | None
    wind_speed_kt: float
    magnetic_variation_deg_east: float
    mze: GeoPoint
    mze_elevation_ft_msl: float
    turn_altitude_ft_msl: float
    descent_start_altitude_ft_msl: float | None = None
    target_altitude_ft_msl: float | None = None
    descent_rate_fpm: float | None = None
    bearing_min_magnetic_deg: float = 250.0
    bearing_max_magnetic_deg: float = 290.0
    boundary: tuple[GeoPoint, ...] = ()
    boundary_model_error_nm: float = 0.0
    reference_revision: str = "synthetic"
    dme_increment_nm: float = 0.5
    coarse_bearing_step_deg: float = 5.0
    max_bearing_evaluations: int = 64
    max_distance_evaluations: int = 96
    bearing_tolerance_deg: float = 1e-3
    distance_tolerance_nm: float = 1e-3
    max_extension_distance_nm: float = 240.0


@dataclass(frozen=True)
class InboundGuidanceSolution:
    status: InboundGuidanceStatus
    reason_code: str | None
    raw_turn_point: GeoPoint | None = None
    rounded_turn_point: GeoPoint | None = None
    bearing_magnetic_deg: float | None = None
    actual_bearing_magnetic_deg: float | None = None
    raw_extra_distance_nm: float | None = None
    extra_distance_nm: float | None = None
    raw_predicted_ete_min: float | None = None
    predicted_ete_min: float | None = None
    raw_dme_nm: float | None = None
    rounded_dme_nm: float | None = None
    raw_turn_altitude_ft_msl: float | None = None
    rounded_turn_altitude_ft_msl: float | None = None
    raw_minimum_boundary_clearance_nm: float | None = None
    minimum_boundary_clearance_nm: float | None = None
    reference_revision: str | None = None


@dataclass(frozen=True)
class _RouteMetrics:
    total_distance_nm: float
    total_ete_min: float
    minimum_clearance_nm: float
    intersects_boundary: bool


@dataclass(frozen=True)
class _DistanceEvaluation:
    bearing_magnetic_deg: float
    raw_turn_point: GeoPoint
    rounded_turn_point: GeoPoint | None
    raw_predicted_ete_min: float | None
    predicted_ete_min: float | None
    raw_extra_distance_nm: float | None
    extra_distance_nm: float | None
    raw_dme_nm: float | None
    rounded_dme_nm: float | None
    raw_turn_altitude_ft_msl: float | None
    rounded_turn_altitude_ft_msl: float | None
    raw_minimum_boundary_clearance_nm: float | None
    minimum_boundary_clearance_nm: float | None
    actual_bearing_magnetic_deg: float | None
    raw_feasible: bool
    rounded_feasible: bool
    acceptable: bool
    adverse_wind: bool


@dataclass(frozen=True)
class _BearingSearchResult:
    best: _DistanceEvaluation | None
    best_raw: _DistanceEvaluation | None
    adverse_only: bool
    convergence_failure: bool


def solve_rjfm_inbound_west_extension(request: InboundGuidanceRequest) -> InboundGuidanceSolution:
    if not _valid(request):
        return InboundGuidanceSolution(
            InboundGuidanceStatus.NO_SOLUTION,
            "INVALID_INPUT",
            reference_revision=request.reference_revision,
        )
    if len(request.boundary) < 3:
        return InboundGuidanceSolution(
            InboundGuidanceStatus.UNAVAILABLE,
            "KS43_REFERENCE_UNAVAILABLE",
            reference_revision=request.reference_revision,
        )
    if not _request_geometry_supported(request):
        return InboundGuidanceSolution(
            InboundGuidanceStatus.NO_SOLUTION,
            "GEOMETRY_UNSUPPORTED",
            reference_revision=request.reference_revision,
        )

    coarse_bearings = _bearing_grid(
        request.bearing_min_magnetic_deg,
        request.bearing_max_magnetic_deg,
        request.coarse_bearing_step_deg,
    )
    if len(coarse_bearings) > request.max_bearing_evaluations:
        return InboundGuidanceSolution(
            InboundGuidanceStatus.CONVERGENCE_FAILURE,
            "BEARING_EVALUATION_BUDGET_EXCEEDED",
            reference_revision=request.reference_revision,
        )

    direct_distance_nm = geodesic_leg(
        request.umk.latitude_deg,
        request.umk.longitude_deg,
        request.vrep.latitude_deg,
        request.vrep.longitude_deg,
    ).distance_nm
    evaluations: dict[float, _BearingSearchResult] = {}
    for bearing in coarse_bearings:
        evaluations[bearing] = _search_bearing(request, bearing, direct_distance_nm)

    # A direct course is a continuous candidate, not a coarse-grid privilege.
    # It is particularly important when time is the active constraint and
    # several bearings have numerically indistinguishable raw extra distance.
    direct_bearing = _direct_bearing_magnetic_deg(request)
    if (
        len(evaluations) < request.max_bearing_evaluations
        and _bearing_within_range(direct_bearing, request)
        and direct_bearing not in evaluations
    ):
        evaluations[direct_bearing] = _search_bearing(
            request,
            direct_bearing,
            direct_distance_nm,
        )

    while len(evaluations) < request.max_bearing_evaluations:
        interval = _select_bearing_interval(request, evaluations)
        if interval is None or interval[1] - interval[0] <= request.bearing_tolerance_deg:
            break
        midpoint = round((interval[0] + interval[1]) / 2.0, 9)
        if midpoint in evaluations:
            break
        evaluations[midpoint] = _search_bearing(request, midpoint, direct_distance_nm)
        focus_width = _focus_interval_width(
            evaluations,
            _focus_bearing(request, evaluations),
        )
        if (
            any(result.best is not None for result in evaluations.values())
            and focus_width is not None
            and focus_width <= request.bearing_tolerance_deg
        ):
            break

    _refine_direct_bearing_window(request, evaluations, direct_distance_nm)

    best_interval = _select_bearing_interval(request, evaluations)
    focus_width = _focus_interval_width(evaluations, _focus_bearing(request, evaluations))
    bearing_convergence_failure = (
        len(evaluations) >= request.max_bearing_evaluations
        and best_interval is not None
        and best_interval[1] - best_interval[0] > request.bearing_tolerance_deg
        and (focus_width is None or focus_width > request.bearing_tolerance_deg)
    )
    acceptable = [result.best for result in evaluations.values() if result.best is not None]
    if acceptable and not (
        bearing_convergence_failure
        or any(result.convergence_failure for result in evaluations.values())
    ):
        best = _best_bearing_candidate(request, acceptable)
        return InboundGuidanceSolution(
            InboundGuidanceStatus.AVAILABLE,
            None,
            raw_turn_point=best.raw_turn_point,
            rounded_turn_point=best.rounded_turn_point,
            bearing_magnetic_deg=best.bearing_magnetic_deg,
            actual_bearing_magnetic_deg=best.actual_bearing_magnetic_deg,
            raw_extra_distance_nm=best.raw_extra_distance_nm,
            extra_distance_nm=best.extra_distance_nm,
            raw_predicted_ete_min=best.raw_predicted_ete_min,
            predicted_ete_min=best.predicted_ete_min,
            raw_dme_nm=best.raw_dme_nm,
            rounded_dme_nm=best.rounded_dme_nm,
            raw_turn_altitude_ft_msl=best.raw_turn_altitude_ft_msl,
            rounded_turn_altitude_ft_msl=best.rounded_turn_altitude_ft_msl,
            raw_minimum_boundary_clearance_nm=best.raw_minimum_boundary_clearance_nm,
            minimum_boundary_clearance_nm=best.minimum_boundary_clearance_nm,
            reference_revision=request.reference_revision,
        )

    results = tuple(evaluations.values())
    if results and all(result.adverse_only for result in results):
        return InboundGuidanceSolution(
            InboundGuidanceStatus.ADVERSE_WIND,
            "ADVERSE_WIND",
            reference_revision=request.reference_revision,
        )
    if bearing_convergence_failure or any(result.convergence_failure for result in results):
        return InboundGuidanceSolution(
            InboundGuidanceStatus.CONVERGENCE_FAILURE,
            "CONVERGENCE_FAILURE",
            reference_revision=request.reference_revision,
        )
    return InboundGuidanceSolution(
        InboundGuidanceStatus.NO_SOLUTION,
        "NO_FEASIBLE_KS43_AVOIDANCE",
        reference_revision=request.reference_revision,
    )


def _bearing_grid(low: float, high: float, step: float) -> tuple[float, ...]:
    if high <= low + _EPSILON:
        return (round(low, 9),)
    values = {round(low, 9), round(high, 9)}
    count = int((high - low) / step)
    for index in range(count + 1):
        values.add(round(min(high, low + index * step), 9))
    return tuple(sorted(values))


def _search_bearing(
    request: InboundGuidanceRequest,
    bearing_magnetic_deg: float,
    direct_distance_nm: float,
) -> _BearingSearchResult:
    if request.max_distance_evaluations < _MIN_DISTANCE_EVALUATIONS:
        return _BearingSearchResult(None, None, False, True)

    cache: dict[float, _DistanceEvaluation] = {}
    for distance_nm in _seed_distances(
        request.max_extension_distance_nm,
        request.max_distance_evaluations,
    ):
        cache[distance_nm] = _evaluate_distance(
            request,
            bearing_magnetic_deg,
            distance_nm,
            direct_distance_nm,
        )

    while len(cache) < request.max_distance_evaluations:
        interval = _select_distance_interval(request, cache)
        if interval is None or interval[1] - interval[0] <= request.distance_tolerance_nm:
            break
        midpoint = round((interval[0] + interval[1]) / 2.0, 9)
        if midpoint in cache:
            break
        cache[midpoint] = _evaluate_distance(
            request,
            bearing_magnetic_deg,
            midpoint,
            direct_distance_nm,
        )

    acceptable = [item for item in cache.values() if item.acceptable]
    best = None
    if acceptable:
        best = min(
            acceptable,
            key=lambda item: (
                _sortable_value(item.raw_extra_distance_nm),
                _sortable_value(item.extra_distance_nm),
                _sortable_value(item.rounded_dme_nm),
            ),
        )

    raw_candidates = [item for item in cache.values() if item.raw_feasible]
    best_raw = None
    if raw_candidates:
        best_raw = min(
            raw_candidates,
            key=lambda item: (
                _sortable_value(item.raw_extra_distance_nm),
                _sortable_value(item.raw_dme_nm),
            ),
        )

    best_interval = _select_distance_interval(request, cache)
    convergence_failure = (
        len(cache) >= request.max_distance_evaluations
        and best_interval is not None
        and best_interval[1] - best_interval[0] > request.distance_tolerance_nm
    )
    return _BearingSearchResult(
        best=best,
        best_raw=best_raw,
        adverse_only=bool(cache) and all(item.adverse_wind for item in cache.values()),
        convergence_failure=convergence_failure,
    )


def _seed_distances(max_extension_distance_nm: float, budget: int) -> tuple[float, ...]:
    if budget <= 1:
        return (0.0,)
    count = min(budget, _INITIAL_DISTANCE_SCAN_POINTS)
    values = {0.0, round(max_extension_distance_nm, 9)}
    for index in range(count):
        values.add(round(max_extension_distance_nm * index / (count - 1), 9))
    return tuple(sorted(values))


def _select_bearing_interval(
    request: InboundGuidanceRequest,
    evaluations: dict[float, _BearingSearchResult],
) -> tuple[float, float] | None:
    choice: tuple[float, float] | None = None
    best_priority = 0.0
    focus_bearing = _focus_bearing(request, evaluations)
    ordered = sorted(evaluations)
    for left, right in zip(ordered, ordered[1:], strict=False):
        priority = _bearing_interval_priority(
            evaluations[left],
            evaluations[right],
            right - left,
            focus_bearing,
            left,
            right,
        )
        if priority > best_priority:
            best_priority = priority
            choice = (left, right)
    return choice


def _bearing_interval_priority(
    left_result: _BearingSearchResult,
    right_result: _BearingSearchResult,
    width: float,
    focus_bearing: float,
    left_bearing: float,
    right_bearing: float,
) -> float:
    left_raw = left_result.best_raw
    right_raw = right_result.best_raw
    if left_raw is None and right_raw is None:
        return 0.0
    priority = width
    if left_bearing - _EPSILON <= focus_bearing <= right_bearing + _EPSILON:
        priority += 500.0
    if (left_raw is None) != (right_raw is None):
        return priority + 300.0
    assert left_raw is not None and right_raw is not None
    left_raw_extra = _sortable_value(left_raw.raw_extra_distance_nm)
    right_raw_extra = _sortable_value(right_raw.raw_extra_distance_nm)
    priority += 100.0 / (1.0 + min(left_raw_extra, right_raw_extra))
    if abs(left_raw_extra - right_raw_extra) > 1e-6:
        priority += 120.0
    if (left_result.best is None) != (right_result.best is None):
        priority += 180.0
    elif left_result.best is not None and right_result.best is not None:
        left_extra = _sortable_value(left_result.best.extra_distance_nm)
        right_extra = _sortable_value(right_result.best.extra_distance_nm)
        if abs(left_extra - right_extra) > 1e-6:
            priority += 80.0
    return priority


def _adjacent_pairs(cache: dict[float, _DistanceEvaluation]) -> tuple[tuple[float, float], ...]:
    ordered = sorted(cache)
    return tuple(zip(ordered, ordered[1:], strict=False))


def _select_distance_interval(
    request: InboundGuidanceRequest,
    cache: dict[float, _DistanceEvaluation],
) -> tuple[float, float] | None:
    choice: tuple[float, float] | None = None
    best_priority = 0.0
    for left, right in _adjacent_pairs(cache):
        priority = _distance_interval_priority(request, cache[left], cache[right], right - left)
        if priority > best_priority:
            best_priority = priority
            choice = (left, right)
    return choice


def _distance_interval_priority(
    request: InboundGuidanceRequest,
    left: _DistanceEvaluation,
    right: _DistanceEvaluation,
    width: float,
) -> float:
    if width <= request.distance_tolerance_nm or (left.adverse_wind and right.adverse_wind):
        return 0.0

    raw_time_crosses = _time_straddles(
        request.required_ete_min,
        left.raw_predicted_ete_min,
        right.raw_predicted_ete_min,
    )
    raw_boundary = left.raw_feasible != right.raw_feasible
    rounded_boundary = left.rounded_feasible != right.rounded_feasible
    acceptable_boundary = left.acceptable != right.acceptable
    if not (raw_time_crosses or raw_boundary or rounded_boundary or acceptable_boundary):
        return 0.0

    priority = width
    if raw_boundary:
        priority += 300.0
    if acceptable_boundary:
        priority += 240.0
    if rounded_boundary:
        priority += 180.0
    if raw_time_crosses:
        priority += 140.0
    return priority


def _time_straddles(
    required_ete_min: float,
    left_time: float | None,
    right_time: float | None,
) -> bool:
    if left_time is None or right_time is None:
        return False
    left_gap = left_time - required_ete_min
    right_gap = right_time - required_ete_min
    return left_gap == 0.0 or right_gap == 0.0 or left_gap * right_gap < 0.0


def _focus_bearing(
    request: InboundGuidanceRequest,
    evaluations: dict[float, _BearingSearchResult],
) -> float:
    direct_bearing = _direct_bearing_magnetic_deg(request)
    candidates = [result.best_raw for result in evaluations.values() if result.best_raw is not None]
    if not candidates:
        candidates = [result.best for result in evaluations.values() if result.best is not None]
    if not candidates:
        return direct_bearing

    raw_best = min(_sortable_value(candidate.raw_extra_distance_nm) for candidate in candidates)
    near_optimal = [
        candidate
        for candidate in candidates
        if _sortable_value(candidate.raw_extra_distance_nm)
        <= raw_best + request.distance_tolerance_nm
    ]
    local_candidates = [
        candidate
        for candidate in near_optimal
        if abs(candidate.bearing_magnetic_deg - direct_bearing)
        <= request.coarse_bearing_step_deg + request.bearing_tolerance_deg
    ]
    if local_candidates:
        best = min(
            local_candidates,
            key=lambda candidate: (
                _sortable_value(candidate.extra_distance_nm),
                abs(candidate.bearing_magnetic_deg - direct_bearing),
                candidate.bearing_magnetic_deg,
            ),
        )
        return best.bearing_magnetic_deg

    best = min(
        near_optimal,
        key=lambda candidate: (
            abs(candidate.bearing_magnetic_deg - direct_bearing),
            _sortable_value(candidate.extra_distance_nm),
            candidate.bearing_magnetic_deg,
        ),
    )
    return best.bearing_magnetic_deg


def _best_focus_candidate(
    request: InboundGuidanceRequest,
    candidates: list[_DistanceEvaluation],
) -> _DistanceEvaluation:
    best = candidates[0]
    direct_bearing = _direct_bearing_magnetic_deg(request)
    for candidate in candidates[1:]:
        if _focus_candidate_is_better(
            candidate,
            best,
            request.distance_tolerance_nm,
            direct_bearing,
        ):
            best = candidate
    return best


def _focus_candidate_is_better(
    candidate: _DistanceEvaluation,
    current: _DistanceEvaluation,
    distance_tolerance_nm: float,
    direct_bearing: float,
) -> bool:
    if _meaningfully_less(
        candidate.raw_extra_distance_nm,
        current.raw_extra_distance_nm,
        distance_tolerance_nm,
    ):
        return True
    if _meaningfully_less(
        current.raw_extra_distance_nm,
        candidate.raw_extra_distance_nm,
        distance_tolerance_nm,
    ):
        return False
    if _sortable_value(candidate.extra_distance_nm) + _EPSILON < _sortable_value(
        current.extra_distance_nm
    ):
        return True
    if _sortable_value(current.extra_distance_nm) + _EPSILON < _sortable_value(
        candidate.extra_distance_nm
    ):
        return False
    candidate_direct_gap = abs(candidate.bearing_magnetic_deg - direct_bearing)
    current_direct_gap = abs(current.bearing_magnetic_deg - direct_bearing)
    if candidate_direct_gap + _EPSILON < current_direct_gap:
        return True
    if current_direct_gap + _EPSILON < candidate_direct_gap:
        return False
    return candidate.bearing_magnetic_deg < current.bearing_magnetic_deg


def _best_bearing_candidate(
    request: InboundGuidanceRequest,
    candidates: list[_DistanceEvaluation],
) -> _DistanceEvaluation:
    best = candidates[0]
    direct_bearing = _direct_bearing_magnetic_deg(request)
    for candidate in candidates[1:]:
        if _bearing_candidate_is_better(
            candidate,
            best,
            request.distance_tolerance_nm,
            direct_bearing,
        ):
            best = candidate
    return best


def _bearing_candidate_is_better(
    candidate: _DistanceEvaluation,
    current: _DistanceEvaluation,
    distance_tolerance_nm: float,
    direct_bearing: float,
) -> bool:
    # Raw extra distance is the objective. Treat values within the requested
    # distance tolerance as one numerical basin, then use directness only as a
    # deterministic tie-breaker. Rounded metrics may never outrank a material
    # raw-distance improvement, but they make a stable secondary tie-break.
    if _meaningfully_less(
        candidate.raw_extra_distance_nm,
        current.raw_extra_distance_nm,
        distance_tolerance_nm,
    ):
        return True
    if _meaningfully_less(
        current.raw_extra_distance_nm,
        candidate.raw_extra_distance_nm,
        distance_tolerance_nm,
    ):
        return False
    candidate_direct_gap = abs(candidate.bearing_magnetic_deg - direct_bearing)
    current_direct_gap = abs(current.bearing_magnetic_deg - direct_bearing)
    if candidate_direct_gap + _EPSILON < current_direct_gap:
        return True
    if current_direct_gap + _EPSILON < candidate_direct_gap:
        return False
    if _sortable_value(candidate.extra_distance_nm) + _EPSILON < _sortable_value(
        current.extra_distance_nm
    ):
        return True
    if _sortable_value(current.extra_distance_nm) + _EPSILON < _sortable_value(
        candidate.extra_distance_nm
    ):
        return False
    candidate_actual_gap = abs(
        (candidate.actual_bearing_magnetic_deg or candidate.bearing_magnetic_deg)
        - candidate.bearing_magnetic_deg
    )
    current_actual_gap = abs(
        (current.actual_bearing_magnetic_deg or current.bearing_magnetic_deg)
        - current.bearing_magnetic_deg
    )
    if candidate_actual_gap + _EPSILON < current_actual_gap:
        return True
    if current_actual_gap + _EPSILON < candidate_actual_gap:
        return False
    return candidate.bearing_magnetic_deg < current.bearing_magnetic_deg


def _meaningfully_less(left: float | None, right: float | None, tolerance: float) -> bool:
    if left is None:
        return False
    if right is None:
        return True
    return left + tolerance < right


def _direct_bearing_magnetic_deg(request: InboundGuidanceRequest) -> float:
    return (
        geodesic_leg(
            request.umk.latitude_deg,
            request.umk.longitude_deg,
            request.vrep.latitude_deg,
            request.vrep.longitude_deg,
        ).initial_true_course_deg
        + request.magnetic_variation_deg_east
    ) % 360.0


def _focus_interval_width(
    evaluations: dict[float, _BearingSearchResult],
    focus_bearing: float,
) -> float | None:
    ordered = sorted(evaluations)
    widths = [
        right - left
        for left, right in zip(ordered, ordered[1:], strict=False)
        if left - _EPSILON <= focus_bearing <= right + _EPSILON
    ]
    if not widths:
        return None
    return min(widths)


def _refine_direct_bearing_window(
    request: InboundGuidanceRequest,
    evaluations: dict[float, _BearingSearchResult],
    direct_distance_nm: float,
) -> None:
    direct_bearing = _direct_bearing_magnetic_deg(request)
    if not (
        request.bearing_min_magnetic_deg - request.bearing_tolerance_deg
        <= direct_bearing
        <= request.bearing_max_magnetic_deg + request.bearing_tolerance_deg
    ):
        return
    step = request.coarse_bearing_step_deg / 2.0
    while (
        step > request.bearing_tolerance_deg and len(evaluations) < request.max_bearing_evaluations
    ):
        for offset in (-step, step):
            bearing = round(direct_bearing + offset, 9)
            if not _bearing_within_range(bearing, request) or bearing in evaluations:
                continue
            evaluations[bearing] = _search_bearing(request, bearing, direct_distance_nm)
            if len(evaluations) >= request.max_bearing_evaluations:
                return
        step /= 2.0


def _sortable_value(value: float | None) -> float:
    return float("inf") if value is None else value


def _evaluate_distance(
    request: InboundGuidanceRequest,
    bearing_magnetic_deg: float,
    raw_distance_nm: float,
    direct_distance_nm: float,
) -> _DistanceEvaluation:
    true_course_deg = (bearing_magnetic_deg - request.magnetic_variation_deg_east) % 360.0
    raw_turn = _point_on_ray(request.umk, true_course_deg, raw_distance_nm)
    raw_first_leg = _leg(request.umk, raw_turn, request)
    raw_metrics, adverse_raw = _route_metrics(request, raw_turn)

    raw_turn_altitude = None
    raw_dme_nm = None
    if raw_first_leg is not None:
        raw_turn_altitude = _turn_altitude_for_first_leg(request, raw_first_leg[1])
        raw_dme_nm = _slant_dme_nm(request, raw_turn, raw_turn_altitude)
    rounded_dme_nm = None if raw_dme_nm is None else _round_up(raw_dme_nm, request.dme_increment_nm)
    rounded_turn = None
    if rounded_dme_nm is not None:
        rounded_turn = _rounded_turn_point(
            request,
            true_course_deg,
            raw_distance_nm,
            rounded_dme_nm,
        )

    raw_predicted_ete_min = None
    raw_extra_distance_nm = None
    raw_minimum_boundary_clearance_nm = None
    raw_feasible = False
    if raw_metrics is not None:
        raw_predicted_ete_min = raw_metrics.total_ete_min
        raw_extra_distance_nm = raw_metrics.total_distance_nm - direct_distance_nm
        raw_minimum_boundary_clearance_nm = raw_metrics.minimum_clearance_nm
        raw_feasible = (
            raw_metrics.total_ete_min + _EPSILON >= request.required_ete_min
            and not raw_metrics.intersects_boundary
        )

    rounded_first_leg = None if rounded_turn is None else _leg(request.umk, rounded_turn, request)
    rounded_metrics: _RouteMetrics | None = None
    adverse_rounded = False
    rounded_turn_altitude = None
    actual_bearing = None
    predicted_ete_min = None
    extra_distance_nm = None
    minimum_boundary_clearance_nm = None
    rounded_feasible = False
    if rounded_turn is not None and rounded_first_leg is not None:
        rounded_turn_altitude = _turn_altitude_for_first_leg(request, rounded_first_leg[1])
        rounded_metrics, adverse_rounded = _route_metrics(request, rounded_turn)
        actual_bearing = (
            geodesic_leg(
                request.umk.latitude_deg,
                request.umk.longitude_deg,
                rounded_turn.latitude_deg,
                rounded_turn.longitude_deg,
            ).initial_true_course_deg
            + request.magnetic_variation_deg_east
        ) % 360.0
        if rounded_metrics is not None:
            predicted_ete_min = rounded_metrics.total_ete_min
            extra_distance_nm = rounded_metrics.total_distance_nm - direct_distance_nm
            minimum_boundary_clearance_nm = rounded_metrics.minimum_clearance_nm
            rounded_feasible = (
                rounded_metrics.total_ete_min + _EPSILON >= request.required_ete_min
                and not rounded_metrics.intersects_boundary
                and actual_bearing is not None
                and _bearing_within_range(actual_bearing, request)
            )

    return _DistanceEvaluation(
        bearing_magnetic_deg=bearing_magnetic_deg,
        raw_turn_point=raw_turn,
        rounded_turn_point=rounded_turn,
        raw_predicted_ete_min=raw_predicted_ete_min,
        predicted_ete_min=predicted_ete_min,
        raw_extra_distance_nm=raw_extra_distance_nm,
        extra_distance_nm=extra_distance_nm,
        raw_dme_nm=raw_dme_nm,
        rounded_dme_nm=rounded_dme_nm,
        raw_turn_altitude_ft_msl=raw_turn_altitude,
        rounded_turn_altitude_ft_msl=rounded_turn_altitude,
        raw_minimum_boundary_clearance_nm=raw_minimum_boundary_clearance_nm,
        minimum_boundary_clearance_nm=minimum_boundary_clearance_nm,
        actual_bearing_magnetic_deg=actual_bearing,
        raw_feasible=raw_feasible,
        rounded_feasible=rounded_feasible,
        acceptable=raw_feasible and rounded_feasible,
        adverse_wind=adverse_raw or adverse_rounded or (raw_first_leg is None),
    )


def _turn_altitude_for_first_leg(
    request: InboundGuidanceRequest,
    first_leg_ete_min: float,
) -> float:
    if (
        request.descent_start_altitude_ft_msl is None
        or request.target_altitude_ft_msl is None
        or request.descent_rate_fpm is None
    ):
        return request.turn_altitude_ft_msl
    profile_altitude = (
        request.descent_start_altitude_ft_msl - request.descent_rate_fpm * first_leg_ete_min
    )
    return max(request.target_altitude_ft_msl, profile_altitude)


def _point_on_ray(origin: GeoPoint, true_course_deg: float, distance_nm: float) -> GeoPoint:
    latitude_deg, longitude_deg = point_along_leg(
        origin.latitude_deg,
        origin.longitude_deg,
        true_course_deg,
        distance_nm,
    )
    return GeoPoint(latitude_deg, longitude_deg)


def _rounded_turn_point(
    request: InboundGuidanceRequest,
    true_course_deg: float,
    raw_distance_nm: float,
    rounded_dme_nm: float,
) -> GeoPoint | None:
    def difference(distance_nm: float) -> float | None:
        point = _point_on_ray(request.umk, true_course_deg, distance_nm)
        first_leg = _leg(request.umk, point, request)
        if first_leg is None:
            return None
        altitude_ft = _turn_altitude_for_first_leg(request, first_leg[1])
        return _slant_dme_nm(request, point, altitude_ft) - rounded_dme_nm

    lower = max(0.0, raw_distance_nm)
    lower_difference = difference(lower)
    if lower_difference is None:
        return None
    if abs(lower_difference) <= request.distance_tolerance_nm:
        return _point_on_ray(request.umk, true_course_deg, lower)

    upper = min(request.max_extension_distance_nm, max(lower + 0.5, lower * 1.1 + 0.5))
    upper_difference = difference(upper)
    expansions = 0
    while (
        upper_difference is not None
        and upper_difference < 0
        and upper < request.max_extension_distance_nm - request.distance_tolerance_nm
    ):
        next_upper = min(request.max_extension_distance_nm, max(upper + 1.0, upper * 1.5))
        if next_upper <= upper + request.distance_tolerance_nm:
            break
        upper = next_upper
        upper_difference = difference(upper)
        expansions += 1
        if expansions > 32:
            return None
    if upper_difference is None or upper_difference < 0:
        return None

    for _ in range(48):
        midpoint = (lower + upper) / 2.0
        midpoint_difference = difference(midpoint)
        if midpoint_difference is None:
            return None
        if (
            abs(midpoint_difference) <= request.distance_tolerance_nm
            or upper - lower <= request.distance_tolerance_nm
        ):
            return _point_on_ray(request.umk, true_course_deg, midpoint)
        if midpoint_difference < 0:
            lower = midpoint
        else:
            upper = midpoint
    return _point_on_ray(request.umk, true_course_deg, (lower + upper) / 2.0)


def _slant_dme_nm(
    request: InboundGuidanceRequest,
    point: GeoPoint,
    turn_altitude_ft_msl: float,
) -> float:
    horizontal_nm = geodesic_leg(
        request.mze.latitude_deg,
        request.mze.longitude_deg,
        point.latitude_deg,
        point.longitude_deg,
    ).distance_nm
    vertical_nm = abs(turn_altitude_ft_msl - request.mze_elevation_ft_msl) / _VERTICAL_FEET_PER_NM
    return hypot(horizontal_nm, vertical_nm)


def _round_up(value: float, increment: float) -> float:
    return ceil((value - _EPSILON) / increment) * increment


def _request_geometry_supported(request: InboundGuidanceRequest) -> bool:
    try:
        direct_points = sample_geodesic_points(request.umk, request.vrep)
        polyline_boundary_metrics(
            direct_points,
            request.boundary,
            boundary_model_error_nm=request.boundary_model_error_nm,
        )
    except GeometryUnsupportedError:
        return False
    return True


def _route_metrics(
    request: InboundGuidanceRequest,
    turn: GeoPoint,
) -> tuple[_RouteMetrics | None, bool]:
    first = _leg(request.umk, turn, request)
    second = _leg(turn, request.vrep, request)
    if first is None or second is None:
        return None, True

    try:
        first_intersects, first_clearance = polyline_boundary_metrics(
            sample_geodesic_points(request.umk, turn),
            request.boundary,
            boundary_model_error_nm=request.boundary_model_error_nm,
        )
        second_intersects, second_clearance = polyline_boundary_metrics(
            sample_geodesic_points(turn, request.vrep),
            request.boundary,
            boundary_model_error_nm=request.boundary_model_error_nm,
        )
    except GeometryUnsupportedError:
        # A turn candidate can leave the reviewed local geometry domain. It is
        # simply infeasible; other bearings and distances remain searchable.
        return None, False
    intersects = first_intersects or second_intersects
    return (
        _RouteMetrics(
            total_distance_nm=first[0] + second[0],
            total_ete_min=first[1] + second[1],
            minimum_clearance_nm=0.0 if intersects else min(first_clearance, second_clearance),
            intersects_boundary=intersects,
        ),
        False,
    )


def _leg(
    start: GeoPoint,
    end: GeoPoint,
    request: InboundGuidanceRequest,
) -> tuple[float, float] | None:
    leg = geodesic_leg(start.latitude_deg, start.longitude_deg, end.latitude_deg, end.longitude_deg)
    try:
        wind = solve_wind_triangle(
            leg.initial_true_course_deg,
            request.descent_tas_kt,
            request.wind_direction_deg_true_from,
            request.wind_speed_kt,
        )
    except WindTriangleError:
        return None
    return leg.distance_nm, leg.distance_nm / wind.ground_speed_kt * 60.0


def _bearing_within_range(value: float, request: InboundGuidanceRequest) -> bool:
    return (
        request.bearing_min_magnetic_deg - request.bearing_tolerance_deg
        <= value
        <= request.bearing_max_magnetic_deg + request.bearing_tolerance_deg
    )


def _valid(request: InboundGuidanceRequest) -> bool:
    scalar_values = (
        request.required_ete_min,
        request.descent_tas_kt,
        request.wind_speed_kt,
        request.magnetic_variation_deg_east,
        request.mze_elevation_ft_msl,
        request.turn_altitude_ft_msl,
        request.dme_increment_nm,
        request.coarse_bearing_step_deg,
        request.bearing_tolerance_deg,
        request.distance_tolerance_nm,
        request.max_extension_distance_nm,
        request.boundary_model_error_nm,
    )
    if not all(isfinite(value) for value in scalar_values):
        return False
    if request.wind_direction_deg_true_from is not None and not isfinite(
        request.wind_direction_deg_true_from
    ):
        return False
    if not all(
        _valid_point(point) for point in (request.umk, request.vrep, request.mze, *request.boundary)
    ):
        return False
    profile_values = (
        request.descent_start_altitude_ft_msl,
        request.target_altitude_ft_msl,
        request.descent_rate_fpm,
    )
    if any(value is not None for value in profile_values):
        if any(value is None or not isfinite(value) for value in profile_values):
            return False
        assert request.descent_start_altitude_ft_msl is not None
        assert request.target_altitude_ft_msl is not None
        assert request.descent_rate_fpm is not None
        if (
            request.descent_rate_fpm < 0
            or request.descent_start_altitude_ft_msl < request.target_altitude_ft_msl
        ):
            return False
    return (
        request.required_ete_min > 0
        and request.descent_tas_kt > 0
        and request.wind_speed_kt >= 0
        and request.dme_increment_nm > 0
        and request.coarse_bearing_step_deg > 0
        and request.bearing_tolerance_deg > 0
        and request.distance_tolerance_nm > 0
        and request.max_extension_distance_nm > 0
        and request.boundary_model_error_nm >= 0
        and request.max_bearing_evaluations >= _MIN_BEARING_EVALUATIONS
        and request.max_distance_evaluations >= _MIN_DISTANCE_EVALUATIONS
        and 0.0 <= request.bearing_min_magnetic_deg <= request.bearing_max_magnetic_deg <= 360.0
        and (request.wind_speed_kt == 0 or request.wind_direction_deg_true_from is not None)
    )


def _valid_point(point: GeoPoint) -> bool:
    return (
        isfinite(point.latitude_deg)
        and isfinite(point.longitude_deg)
        and -90.0 <= point.latitude_deg <= 90.0
        and -180.0 <= point.longitude_deg <= 180.0
    )
