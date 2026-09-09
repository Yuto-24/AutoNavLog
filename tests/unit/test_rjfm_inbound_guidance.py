from __future__ import annotations

from dataclasses import replace
from math import ceil, hypot, isclose

import pytest

import autonavlog.application.rjfm_inbound_geometry as geometry_module
import autonavlog.application.rjfm_inbound_guidance as guidance_module
from autonavlog.application.rjfm_inbound_guidance import (
    InboundGuidanceRequest,
    InboundGuidanceSolution,
    InboundGuidanceStatus,
    solve_rjfm_inbound_west_extension,
)
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
from autonavlog.storage.rjfm_inbound_reference import GeoPoint


def _point_on_course(origin: GeoPoint, true_course_deg: float, distance_nm: float) -> GeoPoint:
    latitude_deg, longitude_deg = point_along_leg(
        origin.latitude_deg,
        origin.longitude_deg,
        true_course_deg,
        distance_nm,
    )
    return GeoPoint(latitude_deg, longitude_deg)


def _boundary_far_from_route() -> tuple[GeoPoint, ...]:
    return (
        GeoPoint(5.0, 5.0),
        GeoPoint(5.0, 6.0),
        GeoPoint(6.0, 6.0),
        GeoPoint(6.0, 5.0),
    )


def _request(
    vrep: GeoPoint,
    *,
    umk: GeoPoint | None = None,
    mze: GeoPoint | None = None,
    required_ete_min: float = 18.5,
    descent_tas_kt: float = 120.0,
    wind_direction_deg_true_from: float | None = None,
    wind_speed_kt: float = 0.0,
    magnetic_variation_deg_east: float = 0.0,
    mze_elevation_ft_msl: float = 0.0,
    turn_altitude_ft_msl: float = 0.0,
    bearing_min_magnetic_deg: float = 250.0,
    bearing_max_magnetic_deg: float = 290.0,
    boundary: tuple[GeoPoint, ...] | None = None,
    max_extension_distance_nm: float = 60.0,
    max_bearing_evaluations: int = 64,
    max_distance_evaluations: int = 96,
) -> InboundGuidanceRequest:
    fixed_umk = umk or GeoPoint(0.0, 0.0)
    fixed_mze = mze or fixed_umk
    return InboundGuidanceRequest(
        umk=fixed_umk,
        vrep=vrep,
        required_ete_min=required_ete_min,
        descent_tas_kt=descent_tas_kt,
        wind_direction_deg_true_from=wind_direction_deg_true_from,
        wind_speed_kt=wind_speed_kt,
        magnetic_variation_deg_east=magnetic_variation_deg_east,
        mze=fixed_mze,
        mze_elevation_ft_msl=mze_elevation_ft_msl,
        turn_altitude_ft_msl=turn_altitude_ft_msl,
        bearing_min_magnetic_deg=bearing_min_magnetic_deg,
        bearing_max_magnetic_deg=bearing_max_magnetic_deg,
        boundary=boundary or _boundary_far_from_route(),
        reference_revision="synthetic-boundary-r1",
        max_extension_distance_nm=max_extension_distance_nm,
        max_bearing_evaluations=max_bearing_evaluations,
        max_distance_evaluations=max_distance_evaluations,
    )


def test_solver_finds_non_grid_calm_solution_and_beats_coarse_scan() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 257.4, 30.0)
    request = _request(vrep, umk=umk, required_ete_min=18.5)
    original = (
        request.umk.latitude_deg,
        request.umk.longitude_deg,
        request.vrep.latitude_deg,
        request.vrep.longitude_deg,
        request.mze.latitude_deg,
        request.mze.longitude_deg,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.AVAILABLE
    assert result.reason_code is None
    assert result.raw_extra_distance_nm == pytest.approx(7.0, abs=0.05)
    assert result.extra_distance_nm is not None
    assert result.extra_distance_nm >= result.raw_extra_distance_nm
    assert result.predicted_ete_min is not None
    assert result.predicted_ete_min >= request.required_ete_min
    assert result.minimum_boundary_clearance_nm is not None
    assert result.minimum_boundary_clearance_nm > 0.0
    assert result.raw_dme_nm is not None and result.rounded_dme_nm is not None
    assert result.rounded_dme_nm >= result.raw_dme_nm
    assert isclose(
        result.rounded_dme_nm * 2.0,
        round(result.rounded_dme_nm * 2.0),
        abs_tol=1e-9,
    )
    assert original == (
        request.umk.latitude_deg,
        request.umk.longitude_deg,
        request.vrep.latitude_deg,
        request.vrep.longitude_deg,
        request.mze.latitude_deg,
        request.mze.longitude_deg,
    )

    coarse_raw_candidates: list[float] = []
    for bearing in range(250, 291, 5):
        coarse = solve_rjfm_inbound_west_extension(
            replace(
                request,
                bearing_min_magnetic_deg=float(bearing),
                bearing_max_magnetic_deg=float(bearing),
            )
        )
        if (
            coarse.status is InboundGuidanceStatus.AVAILABLE
            and coarse.raw_extra_distance_nm is not None
        ):
            coarse_raw_candidates.append(coarse.raw_extra_distance_nm)
    assert coarse_raw_candidates
    assert (
        result.raw_extra_distance_nm <= min(coarse_raw_candidates) + request.distance_tolerance_nm
    )


def test_solver_handles_forecast_wind_with_feasible_rounded_solution() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 266.2, 28.0)
    request = _request(
        vrep,
        umk=umk,
        required_ete_min=18.2,
        wind_direction_deg_true_from=330.0,
        wind_speed_kt=18.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.AVAILABLE
    assert result.predicted_ete_min is not None
    assert result.predicted_ete_min >= request.required_ete_min
    assert result.extra_distance_nm is not None and result.extra_distance_nm > 0.0
    assert result.minimum_boundary_clearance_nm is not None
    assert result.minimum_boundary_clearance_nm > 100.0
    assert result.actual_bearing_magnetic_deg is not None
    assert (
        request.bearing_min_magnetic_deg
        <= result.actual_bearing_magnetic_deg
        <= request.bearing_max_magnetic_deg
    )


@pytest.mark.parametrize("boundary_bearing", [250.0, 290.0])
def test_solver_can_select_bearing_range_boundaries(boundary_bearing: float) -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, boundary_bearing, 30.0)
    request = _request(vrep, umk=umk, required_ete_min=18.0)

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.AVAILABLE
    assert result.bearing_magnetic_deg == pytest.approx(boundary_bearing, abs=0.05)
    assert result.actual_bearing_magnetic_deg == pytest.approx(boundary_bearing, abs=0.05)


@pytest.mark.parametrize(
    "boundary",
    [
        (
            GeoPoint(-0.05, -0.60),
            GeoPoint(-0.05, -0.45),
            GeoPoint(0.05, -0.45),
            GeoPoint(0.05, -0.60),
        ),
        (
            GeoPoint(0.0, -0.45),
            GeoPoint(0.05, -0.55),
            GeoPoint(0.05, -0.35),
        ),
        (
            GeoPoint(-0.05, -0.55),
            GeoPoint(-0.05, -0.45),
            GeoPoint(0.05, -0.45),
            GeoPoint(0.05, -0.55),
        ),
    ],
)
def test_solver_rejects_intersection_tangent_and_endpoint_inside(
    boundary: tuple[GeoPoint, ...],
) -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 270.0, 30.0)
    request = _request(
        vrep,
        umk=umk,
        boundary=boundary,
        required_ete_min=18.5,
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.NO_SOLUTION
    assert result.reason_code == "NO_FEASIBLE_KS43_AVOIDANCE"


def test_solver_accepts_collinear_but_disjoint_polygon_edge() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 270.0, 30.0)
    boundary = (
        GeoPoint(0.0, -1.60),
        GeoPoint(0.05, -1.60),
        GeoPoint(0.05, -1.30),
        GeoPoint(0.0, -1.30),
    )
    request = _request(
        vrep,
        umk=umk,
        boundary=boundary,
        required_ete_min=18.5,
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.AVAILABLE
    assert result.minimum_boundary_clearance_nm is not None
    assert result.minimum_boundary_clearance_nm > 40.0


def test_route_metrics_skips_second_boundary_when_first_leg_intersects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0),
        umk=GeoPoint(0.0, 0.0),
    )
    turn = _point_on_course(request.umk, 270.0, 5.0)
    boundary_calls = 0

    def fake_polyline_boundary_metrics(
        sampled_route: object,
        polygon: tuple[GeoPoint, ...],
        *,
        boundary_model_error_nm: float = 0.0,
    ) -> tuple[bool, float]:
        del sampled_route, polygon, boundary_model_error_nm
        nonlocal boundary_calls
        boundary_calls += 1
        return True, 0.0

    monkeypatch.setattr(
        guidance_module,
        "_polyline_boundary_metrics_for_sampled_route",
        fake_polyline_boundary_metrics,
    )

    metrics, geometry_unsupported = guidance_module._route_metrics(request, turn)

    assert not geometry_unsupported
    assert metrics is not None
    assert metrics.intersects_boundary
    assert metrics.minimum_clearance_nm == 0.0
    assert boundary_calls == 1


def test_route_metrics_reuses_precomputed_first_leg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0),
        umk=GeoPoint(0.0, 0.0),
    )
    turn = _point_on_course(request.umk, 270.0, 5.0)
    precomputed_first_leg = (5.0, 2.5)
    leg_calls: list[tuple[GeoPoint, GeoPoint]] = []

    def counting_leg(
        start: GeoPoint,
        end: GeoPoint,
        leg_request: InboundGuidanceRequest,
    ) -> tuple[float, float]:
        del leg_request
        leg_calls.append((start, end))
        return (10.0, 5.0)

    monkeypatch.setattr(guidance_module, "_leg", counting_leg)

    metrics, geometry_unsupported = guidance_module._route_metrics(
        request,
        turn,
        first_leg=precomputed_first_leg,
    )

    assert not geometry_unsupported
    assert metrics is not None
    assert metrics.total_distance_nm == pytest.approx(precomputed_first_leg[0] + 10.0)
    assert metrics.total_ete_min == pytest.approx(precomputed_first_leg[1] + 5.0)
    assert leg_calls == [(turn, request.vrep)]


def test_route_metrics_preserves_unsupported_second_leg_even_when_first_leg_intersects() -> None:
    request = _request(
        _point_on_course(GeoPoint(0.0, 0.0), 90.0, 30.0),
        umk=GeoPoint(0.0, 0.0),
        boundary=(
            GeoPoint(-0.05, -0.25),
            GeoPoint(-0.05, -0.15),
            GeoPoint(0.05, -0.15),
            GeoPoint(0.05, -0.25),
        ),
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
        max_extension_distance_nm=240.0,
    )
    turn = _point_on_course(request.umk, 270.0, 225.0)

    metrics, geometry_unsupported = guidance_module._route_metrics(request, turn)

    assert metrics is None
    assert not geometry_unsupported


def test_route_metrics_rejects_invalid_second_sample_even_when_first_leg_intersects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        _point_on_course(GeoPoint(0.0, 0.0), 270.0, 30.0),
        umk=GeoPoint(0.0, 0.0),
        boundary=(
            GeoPoint(-0.05, -0.25),
            GeoPoint(-0.05, -0.15),
            GeoPoint(0.05, -0.15),
            GeoPoint(0.05, -0.25),
        ),
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
    )
    turn = _point_on_course(request.umk, 270.0, 5.0)
    valid_first = guidance_module._sampled_geodesic_route(request.umk, turn)
    invalid_second = geometry_module._KnownValidSampledRoute(
        (GeoPoint(46.0, 0.0), GeoPoint(46.0, 0.001)),
        _sentinel=geometry_module._SAMPLED_ROUTE_SENTINEL,
    )

    def fake_sampled_geodesic_route(start: GeoPoint, end: GeoPoint):
        if start == request.umk and end == turn:
            return valid_first
        if start == turn and end == request.vrep:
            return invalid_second
        raise AssertionError("unexpected leg")

    monkeypatch.setattr(guidance_module, "_sampled_geodesic_route", fake_sampled_geodesic_route)

    metrics, geometry_unsupported = guidance_module._route_metrics(request, turn)

    assert metrics is None
    assert not geometry_unsupported


def test_solver_reconstructs_outward_half_dme_on_selected_umk_ray() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 270.0, 30.0)
    request = _request(
        vrep,
        umk=umk,
        mze=GeoPoint(0.15, 0.0),
        mze_elevation_ft_msl=0.0,
        turn_altitude_ft_msl=3000.0,
        required_ete_min=18.2,
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.AVAILABLE
    assert result.raw_turn_point is not None and result.rounded_turn_point is not None
    assert result.raw_dme_nm is not None and result.rounded_dme_nm is not None
    assert result.rounded_dme_nm > result.raw_dme_nm
    assert result.rounded_dme_nm == pytest.approx(
        ceil(result.raw_dme_nm * 2.0) / 2.0,
        abs=1e-9,
    )
    assert result.rounded_turn_point.longitude_deg < result.raw_turn_point.longitude_deg
    assert result.actual_bearing_magnetic_deg == pytest.approx(270.0, abs=0.05)
    assert result.predicted_ete_min is not None
    assert result.predicted_ete_min >= request.required_ete_min


def test_solver_continues_past_initial_time_sufficient_point_to_find_obstacle_behind_solution() -> (
    None
):
    request = InboundGuidanceRequest(
        umk=GeoPoint(0.0, 0.0),
        vrep=GeoPoint(0.2, 0.0),
        required_ete_min=8.0,
        descent_tas_kt=120.0,
        wind_direction_deg_true_from=None,
        wind_speed_kt=0.0,
        magnetic_variation_deg_east=0.0,
        mze=GeoPoint(0.0, 0.0),
        mze_elevation_ft_msl=0.0,
        turn_altitude_ft_msl=0.0,
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
        boundary=(
            GeoPoint(0.05, -0.15),
            GeoPoint(0.05, 0.0),
            GeoPoint(0.15, 0.0),
            GeoPoint(0.15, -0.15),
        ),
        reference_revision="synthetic-boundary-r1",
        max_extension_distance_nm=60.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.AVAILABLE
    assert result.reason_code is None
    assert result.raw_turn_point is not None and result.rounded_turn_point is not None
    assert result.raw_turn_point.longitude_deg < -0.15
    assert result.rounded_turn_point.longitude_deg < -0.15
    assert result.raw_predicted_ete_min is not None
    assert result.raw_predicted_ete_min >= request.required_ete_min
    assert result.raw_extra_distance_nm is not None and result.raw_extra_distance_nm > 60.0
    assert result.minimum_boundary_clearance_nm is not None
    assert result.minimum_boundary_clearance_nm > 0.0


def test_solver_uses_profile_turn_altitude_when_reconstructing_slant_dme() -> None:
    request = InboundGuidanceRequest(
        umk=GeoPoint(0.0, 0.0),
        vrep=GeoPoint(0.0, -0.35),
        required_ete_min=8.2,
        descent_tas_kt=120.0,
        wind_direction_deg_true_from=None,
        wind_speed_kt=0.0,
        magnetic_variation_deg_east=0.0,
        mze=GeoPoint(0.15, 0.0),
        mze_elevation_ft_msl=0.0,
        turn_altitude_ft_msl=4500.0,
        descent_start_altitude_ft_msl=4500.0,
        target_altitude_ft_msl=1500.0,
        descent_rate_fpm=500.0,
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
        boundary=_boundary_far_from_route(),
        reference_revision="synthetic-boundary-r1",
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.AVAILABLE
    assert result.raw_turn_point is not None and result.rounded_turn_point is not None
    assert result.raw_turn_altitude_ft_msl is not None
    assert result.rounded_turn_altitude_ft_msl is not None
    assert result.raw_dme_nm is not None and result.rounded_dme_nm is not None
    assert request.target_altitude_ft_msl is not None
    assert request.descent_start_altitude_ft_msl is not None
    assert request.descent_rate_fpm is not None

    raw_first_leg = geodesic_leg(
        request.umk.latitude_deg,
        request.umk.longitude_deg,
        result.raw_turn_point.latitude_deg,
        result.raw_turn_point.longitude_deg,
    )
    rounded_first_leg = geodesic_leg(
        request.umk.latitude_deg,
        request.umk.longitude_deg,
        result.rounded_turn_point.latitude_deg,
        result.rounded_turn_point.longitude_deg,
    )
    raw_expected_altitude = max(
        request.target_altitude_ft_msl,
        request.descent_start_altitude_ft_msl
        - raw_first_leg.distance_nm / request.descent_tas_kt * 60.0 * request.descent_rate_fpm,
    )
    rounded_expected_altitude = max(
        request.target_altitude_ft_msl,
        request.descent_start_altitude_ft_msl
        - rounded_first_leg.distance_nm / request.descent_tas_kt * 60.0 * request.descent_rate_fpm,
    )
    assert result.raw_turn_altitude_ft_msl == pytest.approx(raw_expected_altitude)
    assert result.rounded_turn_altitude_ft_msl == pytest.approx(rounded_expected_altitude)
    assert result.rounded_turn_altitude_ft_msl < result.raw_turn_altitude_ft_msl

    raw_horizontal_dme = geodesic_leg(
        request.mze.latitude_deg,
        request.mze.longitude_deg,
        result.raw_turn_point.latitude_deg,
        result.raw_turn_point.longitude_deg,
    ).distance_nm
    rounded_horizontal_dme = geodesic_leg(
        request.mze.latitude_deg,
        request.mze.longitude_deg,
        result.rounded_turn_point.latitude_deg,
        result.rounded_turn_point.longitude_deg,
    ).distance_nm
    raw_expected_dme = hypot(
        raw_horizontal_dme,
        abs(result.raw_turn_altitude_ft_msl - request.mze_elevation_ft_msl) / 6076.11548556,
    )
    rounded_expected_dme = hypot(
        rounded_horizontal_dme,
        abs(result.rounded_turn_altitude_ft_msl - request.mze_elevation_ft_msl) / 6076.11548556,
    )
    assert result.raw_dme_nm == pytest.approx(raw_expected_dme)
    assert result.rounded_dme_nm == pytest.approx(
        ceil(rounded_expected_dme * 2.0) / 2.0,
        abs=1e-9,
    )


def test_solver_rejects_when_only_the_rounded_candidate_crosses_boundary() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 270.0, 30.0)
    baseline = _request(
        vrep,
        umk=umk,
        mze=GeoPoint(0.15, 0.0),
        mze_elevation_ft_msl=0.0,
        turn_altitude_ft_msl=3000.0,
        required_ete_min=18.2,
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
    )
    baseline_result = solve_rjfm_inbound_west_extension(baseline)
    assert baseline_result.status is InboundGuidanceStatus.AVAILABLE
    assert baseline_result.raw_turn_point is not None
    assert baseline_result.rounded_turn_point is not None

    raw_lon = baseline_result.raw_turn_point.longitude_deg
    rounded_lon = baseline_result.rounded_turn_point.longitude_deg
    strip_west = min(raw_lon, rounded_lon) + 0.0005
    strip_east = max(raw_lon, rounded_lon) - 0.0005
    boundary = (
        GeoPoint(-0.03, strip_west),
        GeoPoint(-0.03, strip_east),
        GeoPoint(0.03, strip_east),
        GeoPoint(0.03, strip_west),
    )

    result = solve_rjfm_inbound_west_extension(replace(baseline, boundary=boundary))

    assert result.status is InboundGuidanceStatus.NO_SOLUTION
    assert result.reason_code == "NO_FEASIBLE_KS43_AVOIDANCE"


def test_solver_reports_adverse_wind_when_every_candidate_is_physically_invalid() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 270.0, 30.0)
    request = _request(
        vrep,
        umk=umk,
        descent_tas_kt=90.0,
        wind_direction_deg_true_from=180.0,
        wind_speed_kt=100.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.ADVERSE_WIND
    assert result.reason_code == "ADVERSE_WIND"


def test_solver_reports_no_solution_when_required_time_exceeds_extension_cap() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 270.0, 30.0)
    request = _request(
        vrep,
        umk=umk,
        required_ete_min=25.0,
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
        max_extension_distance_nm=32.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.NO_SOLUTION
    assert result.reason_code == "NO_FEASIBLE_KS43_AVOIDANCE"


def test_solver_reports_convergence_failure_when_bearing_budget_cannot_cover_coarse_scan() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 260.0, 30.0)
    request = _request(vrep, umk=umk, max_bearing_evaluations=4)

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.CONVERGENCE_FAILURE
    assert result.reason_code == "BEARING_EVALUATION_BUDGET_EXCEEDED"


def test_solver_reports_convergence_failure_when_bearing_budget_stops_at_coarse_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 257.4, 30.0)
    request = _request(
        vrep,
        umk=umk,
        required_ete_min=18.5,
        max_bearing_evaluations=9,
    )

    # Budget handling owns the bearing loop, not distance search / polygon geometry.
    # Supply feasible, converged per-bearing results so only the bearing budget
    # can cause failure. Real numerical searches remain in the solver regressions.
    searched = []

    def search_bearing(request, bearing, direct_distance):
        searched.append(bearing)
        candidate = guidance_module._DistanceEvaluation(
            bearing_magnetic_deg=bearing,
            raw_turn_point=GeoPoint(0.0, -0.6),
            rounded_turn_point=GeoPoint(0.0, -0.61),
            raw_predicted_ete_min=18.5,
            predicted_ete_min=18.6,
            raw_extra_distance_nm=7.0 + abs(bearing - 257.4),
            extra_distance_nm=7.5 + abs(bearing - 257.4),
            raw_dme_nm=37.0,
            rounded_dme_nm=37.5,
            raw_turn_altitude_ft_msl=0.0,
            rounded_turn_altitude_ft_msl=0.0,
            raw_minimum_boundary_clearance_nm=100.0,
            minimum_boundary_clearance_nm=100.0,
            actual_bearing_magnetic_deg=bearing,
            raw_feasible=True,
            rounded_feasible=True,
            acceptable=True,
            adverse_wind=False,
        )
        return guidance_module._BearingSearchResult(candidate, candidate, False, False)

    monkeypatch.setattr(guidance_module, "_search_bearing", search_bearing)

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.CONVERGENCE_FAILURE
    assert result.reason_code == "CONVERGENCE_FAILURE"
    assert searched == list(range(250, 291, 5))
    assert result == InboundGuidanceSolution(
        status=InboundGuidanceStatus.CONVERGENCE_FAILURE,
        reason_code="CONVERGENCE_FAILURE",
        reference_revision=request.reference_revision,
    )


def test_solver_reports_convergence_failure_when_distance_budget_stops_at_seed_scan() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 257.4, 30.0)
    request = _request(
        vrep,
        umk=umk,
        required_ete_min=18.5,
        bearing_min_magnetic_deg=257.4,
        bearing_max_magnetic_deg=257.4,
        max_distance_evaluations=17,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.CONVERGENCE_FAILURE
    assert result.reason_code == "CONVERGENCE_FAILURE"


def test_solver_preserves_zero_extra_distance_candidates() -> None:
    umk = GeoPoint(0.0, 0.0)
    vrep = _point_on_course(umk, 270.0, 30.0)
    request = _request(
        vrep,
        umk=umk,
        required_ete_min=15.0,
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.AVAILABLE
    assert result.bearing_magnetic_deg == pytest.approx(270.0, abs=0.05)
    assert result.actual_bearing_magnetic_deg == pytest.approx(270.0, abs=0.05)
    assert result.raw_extra_distance_nm == pytest.approx(0.0, abs=1e-9)
    assert result.extra_distance_nm == pytest.approx(0.0, abs=1e-9)
    assert result.predicted_ete_min == pytest.approx(15.0)


def test_solver_rejects_nonfinite_inputs_without_raising() -> None:
    request = _request(
        GeoPoint(0.0, -0.5),
        wind_direction_deg_true_from=float("inf"),
        wind_speed_kt=10.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.NO_SOLUTION
    assert result.reason_code == "INVALID_INPUT"


def test_solver_rejects_unsupported_input_geometry_without_numeric_solution() -> None:
    request = _request(
        GeoPoint(46.0, 0.0),
        umk=GeoPoint(46.0, 0.01),
        mze=GeoPoint(46.0, 0.01),
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.NO_SOLUTION
    assert result.reason_code == "GEOMETRY_UNSUPPORTED"
    assert result.raw_turn_point is None
    assert result.rounded_turn_point is None
    assert result.raw_dme_nm is None
    assert result.rounded_dme_nm is None


def test_solver_rejects_out_of_domain_seed_and_continues_search() -> None:
    umk = GeoPoint(0.0, 0.0)
    # The 240 NM westward seed makes turn-to-VREP 270 NM and must be
    # rejected without ending the search for the shorter feasible extension.
    vrep = _point_on_course(umk, 90.0, 30.0)
    request = _request(
        vrep,
        umk=umk,
        required_ete_min=18.5,
        bearing_min_magnetic_deg=270.0,
        bearing_max_magnetic_deg=270.0,
        max_extension_distance_nm=240.0,
    )

    result = solve_rjfm_inbound_west_extension(request)

    assert result.status is InboundGuidanceStatus.AVAILABLE
    assert result.extra_distance_nm is not None
    assert result.extra_distance_nm < 240.0
