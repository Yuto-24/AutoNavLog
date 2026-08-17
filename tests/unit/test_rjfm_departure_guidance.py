from __future__ import annotations

import json
from dataclasses import replace

import pytest

import autonavlog.application.rjfm_departure_guidance as guidance_module
from autonavlog.application.rjfm_departure_guidance import (
    ALTITUDE_TOLERANCE_FT,
    POSITION_TOLERANCE_NM,
    TANGENT_TOLERANCE_DEG,
    ClimbProfilePoint,
    ConstraintSeverity,
    DepartureGuidanceRequest,
    GeoPoint,
    GuidanceStatus,
    Navaid,
    PathPhase,
    PcaRegion,
    PohAltitudeTimeProfile,
    RunwayProcedure,
    TurnDirection,
    TurnModel,
    Wind,
    generate_rjfm_departure_guidance,
    is_allowed_center_route_magnetic_course,
)
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg


def _point(origin: GeoPoint, course_deg: float, distance_nm: float) -> GeoPoint:
    latitude, longitude = point_along_leg(
        origin.latitude_deg,
        origin.longitude_deg,
        course_deg,
        distance_nm,
    )
    return GeoPoint(latitude, longitude)


def _remote_pca(origin: GeoPoint) -> PcaRegion:
    center = _point(origin, 90, 40)
    return PcaRegion(
        polygon_vertices=tuple(_point(center, course, 1) for course in (0, 90, 180, 270)),
        exclusion_center=center,
        exclusion_radius_nm=0.1,
    )


def _request(
    *,
    procedure: RunwayProcedure | None = None,
    target_course_deg: float = 15,
    target_distance_nm: float = 10,
    wind: Wind | None = None,
    minimum_dme_nm: float = 0,
) -> DepartureGuidanceRequest:
    origin = GeoPoint(31.877, 131.449)
    return DepartureGuidanceRequest(
        runway_origin=origin,
        runway_elevation_ft=15,
        target_umk=_point(origin, target_course_deg, target_distance_nm),
        target_altitude_ft=5500,
        mze=Navaid(
            position=_point(origin, 270, 3),
            antenna_elevation_ft=20,
            station_declination_deg_east=-8,
        ),
        wind=wind or Wind(None, 0),
        tas_kt=110,
        climb_profile=PohAltitudeTimeProfile(
            (
                ClimbProfilePoint(0, 0),
                ClimbProfilePoint(1000, 1.5),
                ClimbProfilePoint(3000, 4),
                ClimbProfilePoint(5500, 8),
            )
        ),
        magnetic_variation_deg_east=7,
        runway_procedure=procedure or RunwayProcedure.rwy09(),
        pca_region=_remote_pca(origin),
        minimum_turn_entry_dme_nm=minimum_dme_nm,
    )


@pytest.mark.parametrize(
    ("guidance_request", "expected_initial_course", "expected_turn_direction"),
    [
        (_request(), 85.0, TurnDirection.LEFT),
        (
            _request(
                procedure=RunwayProcedure.rwy27(),
                target_course_deg=330,
            ),
            265.0,
            TurnDirection.RIGHT,
        ),
    ],
)
def test_generates_rwy09_and_rwy27_fixed_bank_tangent_paths(
    guidance_request: DepartureGuidanceRequest,
    expected_initial_course: float,
    expected_turn_direction: TurnDirection,
) -> None:
    result = generate_rjfm_departure_guidance(guidance_request)

    assert result.status is GuidanceStatus.VALID
    assert result.selected_candidate is not None
    candidate = result.selected_candidate
    assert candidate.model is TurnModel.FIXED_BANK_AIR_MASS
    assert candidate.turn_direction is expected_turn_direction
    assert candidate.position_residual_nm <= POSITION_TOLERANCE_NM
    assert candidate.altitude_residual_ft <= ALTITUDE_TOLERANCE_FT
    assert candidate.tangent_residual_deg <= TANGENT_TOLERANCE_DEG
    assert candidate.maximum_required_bank_deg == 20
    assert candidate.path[0].ground_track_true_deg == pytest.approx(expected_initial_course)
    assert candidate.path[-1].elapsed_time_s == pytest.approx(
        candidate.target_elapsed_time_s,
        abs=1e-5,
    )
    assert candidate.path[-1].position.latitude_deg == pytest.approx(
        guidance_request.target_umk.latitude_deg,
        abs=1e-10,
    )
    assert candidate.path[-1].position.longitude_deg == pytest.approx(
        guidance_request.target_umk.longitude_deg,
        abs=1e-10,
    )
    extension_samples = [
        sample for sample in candidate.path if sample.phase is PathPhase.EXTENSION_TURN
    ]
    assert len(extension_samples) >= 2
    track_delta = (
        extension_samples[1].ground_track_true_deg
        - extension_samples[0].ground_track_true_deg
        + 180
    ) % 360 - 180
    assert track_delta * expected_turn_direction.sign > 0


def test_constant_wind_drifts_air_mass_turn_but_still_solves_tangent_and_time() -> None:
    request = _request(wind=Wind(270, 20))

    result = generate_rjfm_departure_guidance(request)

    assert result.status is GuidanceStatus.VALID
    assert result.selected_candidate is not None
    candidate = result.selected_candidate
    assert candidate.model is TurnModel.FIXED_BANK_AIR_MASS
    assert candidate.position_residual_nm <= POSITION_TOLERANCE_NM
    assert candidate.tangent_residual_deg <= TANGENT_TOLERANCE_DEG
    assert candidate.partial_turn_angle_deg > 0


@pytest.mark.parametrize(
    ("guidance_request", "expected_turn_direction"),
    [
        (_request(wind=Wind(270, 20)), TurnDirection.LEFT),
        (
            _request(
                procedure=RunwayProcedure.rwy27(),
                target_course_deg=330,
                wind=Wind(270, 20),
            ),
            TurnDirection.RIGHT,
        ),
    ],
)
def test_adjusted_ground_circle_fallback_stays_at_or_below_20_degree_bank(
    monkeypatch: pytest.MonkeyPatch,
    guidance_request: DepartureGuidanceRequest,
    expected_turn_direction: TurnDirection,
) -> None:
    monkeypatch.setattr(guidance_module, "_solve_fixed_bank", lambda *_args: ())

    result = generate_rjfm_departure_guidance(guidance_request)

    assert result.status is GuidanceStatus.VALID
    assert result.selected_candidate is not None
    candidate = result.selected_candidate
    assert candidate.model is TurnModel.ADJUSTED_GROUND_CIRCLE
    assert candidate.turn_direction is expected_turn_direction
    assert candidate.adjusted_ground_radius_nm is not None
    assert 0 < candidate.maximum_required_bank_deg <= 20
    assert candidate.position_residual_nm <= POSITION_TOLERANCE_NM
    assert candidate.altitude_residual_ft <= ALTITUDE_TOLERANCE_FT
    assert candidate.tangent_residual_deg <= TANGENT_TOLERANCE_DEG
    assert candidate.full_turn_exit_drift_nm == 0
    extension_samples = [
        sample for sample in candidate.path if sample.phase is PathPhase.EXTENSION_TURN
    ]
    assert len(extension_samples) >= 2
    track_delta = (
        extension_samples[1].ground_track_true_deg
        - extension_samples[0].ground_track_true_deg
        + 180
    ) % 360 - 180
    assert track_delta * expected_turn_direction.sign > 0


def test_fixed_air_mass_full_turn_reports_wind_exit_drift() -> None:
    request = replace(
        _request(wind=Wind(270, 20)),
        climb_profile=PohAltitudeTimeProfile(
            (
                ClimbProfilePoint(0, 0),
                ClimbProfilePoint(1000, 2.16),
                ClimbProfilePoint(3000, 6),
                ClimbProfilePoint(5500, 12),
            )
        ),
    )

    result = generate_rjfm_departure_guidance(request)

    assert result.status is GuidanceStatus.VALID
    assert result.selected_candidate is not None
    candidate = result.selected_candidate
    assert candidate.model is TurnModel.FIXED_BANK_AIR_MASS
    assert candidate.full_turns == 1
    assert candidate.full_turn_exit_drift_nm == pytest.approx(0.558, abs=0.01)


def test_pca_intersection_in_inclusive_vertical_band_is_hard_invalid() -> None:
    baseline_request = _request()
    baseline = generate_rjfm_departure_guidance(baseline_request)
    assert baseline.selected_candidate is not None
    protected_sample = next(
        sample
        for sample in baseline.selected_candidate.path
        if 1200 <= sample.altitude_ft <= 2200
    )
    polygon = tuple(
        _point(protected_sample.position, course, 0.25)
        for course in (0, 90, 180, 270)
    )
    far_exclusion = _point(baseline_request.runway_origin, 180, 40)
    request = replace(
        baseline_request,
        pca_region=PcaRegion(
            polygon_vertices=polygon,
            exclusion_center=far_exclusion,
            exclusion_radius_nm=0,
            floor_altitude_ft=protected_sample.altitude_ft,
            ceiling_altitude_ft=protected_sample.altitude_ft,
        ),
    )

    result = generate_rjfm_departure_guidance(request)

    assert result.status is GuidanceStatus.INVALID
    assert result.selected_candidate is not None
    pca = next(
        constraint
        for constraint in result.selected_candidate.constraints
        if constraint.code == "PCA"
    )
    assert not pca.passed
    assert pca.severity is ConstraintSeverity.HARD
    assert pca.sample_index is not None


def test_dme_below_configured_minimum_is_warning_not_hard_failure() -> None:
    request = _request(minimum_dme_nm=100)
    result = generate_rjfm_departure_guidance(request)

    assert result.status is GuidanceStatus.WARNING
    assert result.selected_candidate is not None
    assert result.selected_candidate.hard_valid
    dme = next(
        constraint
        for constraint in result.selected_candidate.constraints
        if constraint.code == "MZE_ENTRY_DME"
    )
    assert not dme.passed
    assert dme.severity is ConstraintSeverity.WARNING
    assert (
        result.selected_candidate.mze_dme_nm
        > result.selected_candidate.mze_horizontal_distance_nm
    )
    true_bearing = geodesic_leg(
        request.mze.position.latitude_deg,
        request.mze.position.longitude_deg,
        result.selected_candidate.turn_entry.latitude_deg,
        result.selected_candidate.turn_entry.longitude_deg,
    ).initial_true_course_deg
    assert result.selected_candidate.mze_radial_deg == pytest.approx(
        (true_bearing - request.mze.station_declination_deg_east) % 360,
    )


def test_turn_entry_dme_minimum_is_inclusive() -> None:
    baseline_request = _request()
    baseline = generate_rjfm_departure_guidance(baseline_request)
    assert baseline.selected_candidate is not None
    exact_minimum = baseline.selected_candidate.mze_dme_nm

    result = generate_rjfm_departure_guidance(
        replace(baseline_request, minimum_turn_entry_dme_nm=exact_minimum)
    )

    assert result.status is GuidanceStatus.VALID
    assert result.selected_candidate is not None
    dme = next(
        constraint
        for constraint in result.selected_candidate.constraints
        if constraint.code == "MZE_ENTRY_DME"
    )
    assert dme.passed


@pytest.mark.parametrize(
    ("course_deg", "expected"),
    [
        (0, True),
        (91.999999, True),
        (92, False),
        (272, False),
        (272.000001, True),
        (359.999999, True),
        (360, True),
    ],
)
def test_center_route_magnetic_course_boundaries_are_unrounded(
    course_deg: float,
    expected: bool,
) -> None:
    assert is_allowed_center_route_magnetic_course(course_deg) is expected


def test_unreachable_umk_returns_no_solution_instead_of_raising() -> None:
    result = generate_rjfm_departure_guidance(
        _request(target_distance_nm=100),
    )

    assert result.status is GuidanceStatus.NO_SOLUTION
    assert result.selected_candidate is None
    assert result.candidates == ()
    assert result.issues


def test_invalid_profile_returns_explicit_unsupported_result() -> None:
    request = replace(
        _request(),
        climb_profile=PohAltitudeTimeProfile(
            (
                ClimbProfilePoint(0, 0),
                ClimbProfilePoint(5500, 0),
            )
        ),
    )

    result = generate_rjfm_departure_guidance(request)

    assert result.status is GuidanceStatus.UNSUPPORTED
    assert result.selected_candidate is None
    assert "strictly increasing" in result.issues[0]


def test_result_is_json_serializable_without_pydantic() -> None:
    result = generate_rjfm_departure_guidance(_request())

    serialized = result.to_dict()
    encoded = json.dumps(serialized)

    assert serialized["status"] == "VALID"
    assert serialized["selected_candidate"]["model"] == "FIXED_BANK_AIR_MASS"
    assert serialized["selected_candidate"]["turn_direction"] == "LEFT"
    assert "full_left_turns" not in serialized["selected_candidate"]
    assert "partial_left_turn_angle_deg" not in serialized["selected_candidate"]
    assert "EXTENSION_TURN" in encoded
    assert "INITIAL_STRAIGHT" in encoded
