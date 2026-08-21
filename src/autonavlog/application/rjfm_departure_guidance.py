from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from math import (
    atan,
    atan2,
    ceil,
    cos,
    degrees,
    hypot,
    isfinite,
    pi,
    radians,
    sin,
    sqrt,
    tan,
)
from typing import Any, cast

from geographiclib.geodesic import Geodesic

from autonavlog.nav.geodesy import METERS_PER_NM, geodesic_leg
from autonavlog.nav.wind_triangle import WindTriangleError, solve_wind_triangle

FEET_PER_NM = 6076.11548556
KNOT_TO_NM_PER_SECOND = 1.0 / 3600.0
KNOT_TO_FEET_PER_SECOND = FEET_PER_NM / 3600.0
STANDARD_GRAVITY_FT_S2 = 32.174

POSITION_TOLERANCE_NM = 0.01
ALTITUDE_TOLERANCE_FT = 10.0
TANGENT_TOLERANCE_DEG = 0.1
MAX_DERIVED_FULL_TURNS = 60


class GuidanceStatus(str, Enum):
    VALID = "VALID"
    WARNING = "WARNING"
    INVALID = "INVALID"
    NO_SOLUTION = "NO_SOLUTION"
    UNSUPPORTED = "UNSUPPORTED"


class TurnDirection(str, Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"

    @property
    def sign(self) -> int:
        return -1 if self is TurnDirection.LEFT else 1


class TurnModel(str, Enum):
    FIXED_BANK_AIR_MASS = "FIXED_BANK_AIR_MASS"
    ADJUSTED_GROUND_CIRCLE = "ADJUSTED_GROUND_CIRCLE"


class PathPhase(str, Enum):
    INITIAL_STRAIGHT = "INITIAL_STRAIGHT"
    INITIAL_CUT_TURN = "INITIAL_CUT_TURN"
    OUTBOUND = "OUTBOUND"
    EXTENSION_TURN = "EXTENSION_TURN"
    DIRECT_UMK = "DIRECT_UMK"


class ConstraintSeverity(str, Enum):
    HARD = "HARD"
    WARNING = "WARNING"


@dataclass(frozen=True)
class GeoPoint:
    latitude_deg: float
    longitude_deg: float


@dataclass(frozen=True)
class Wind:
    direction_deg_true_from: float | None
    speed_kt: float


@dataclass(frozen=True)
class ClimbProfilePoint:
    altitude_ft: float
    cumulative_time_min: float


@dataclass(frozen=True)
class PohAltitudeTimeProfile:
    """A monotonic POH cumulative altitude/time profile.

    Times may be the raw cumulative values from the POH. The solver subtracts
    the interpolated value at runway elevation, so the first row need not be
    the runway elevation or time zero. Any temperature adjustment adopted by
    the main NAVLOG must already be reflected in these cumulative times; this
    isolated geometry solver does not choose a performance policy.
    """

    points: tuple[ClimbProfilePoint, ...]


@dataclass(frozen=True)
class Navaid:
    position: GeoPoint
    antenna_elevation_ft: float
    station_declination_deg_east: float


@dataclass(frozen=True)
class PcaRegion:
    polygon_vertices: tuple[GeoPoint, ...]
    exclusion_center: GeoPoint
    exclusion_radius_nm: float
    floor_altitude_ft: float = 656.0
    ceiling_altitude_ft: float = 2700.0


@dataclass(frozen=True)
class RunwayProcedure:
    runway_id: str
    initial_ground_course_magnetic_deg: float
    post_cut_ground_course_magnetic_deg: float
    initial_turn_direction: TurnDirection
    extension_turn_direction: TurnDirection
    initial_distance_nm: float | None = None
    initial_until_altitude_ft: float | None = None
    bank_deg: float = 20.0

    @classmethod
    def rwy27(cls) -> RunwayProcedure:
        return cls(
            runway_id="RWY27",
            initial_ground_course_magnetic_deg=272.0,
            post_cut_ground_course_magnetic_deg=317.0,
            initial_turn_direction=TurnDirection.RIGHT,
            extension_turn_direction=TurnDirection.RIGHT,
            initial_distance_nm=1.5,
        )

    @classmethod
    def rwy09(cls, traffic_pattern_altitude_ft: float = 1000.0) -> RunwayProcedure:
        return cls(
            runway_id="RWY09",
            initial_ground_course_magnetic_deg=92.0,
            post_cut_ground_course_magnetic_deg=47.0,
            initial_turn_direction=TurnDirection.LEFT,
            extension_turn_direction=TurnDirection.LEFT,
            initial_until_altitude_ft=traffic_pattern_altitude_ft,
        )


@dataclass(frozen=True)
class DepartureGuidanceRequest:
    runway_origin: GeoPoint
    runway_elevation_ft: float
    target_umk: GeoPoint
    target_altitude_ft: float
    mze: Navaid
    wind: Wind
    tas_kt: float
    climb_profile: PohAltitudeTimeProfile
    magnetic_variation_deg_east: float
    runway_procedure: RunwayProcedure
    pca_region: PcaRegion
    sample_interval_s: float = 0.25


@dataclass(frozen=True)
class TrajectorySample:
    elapsed_time_s: float
    position: GeoPoint
    altitude_ft: float
    true_heading_deg: float
    ground_track_true_deg: float
    phase: PathPhase


@dataclass(frozen=True)
class ConstraintResult:
    code: str
    passed: bool
    severity: ConstraintSeverity
    message: str
    sample_index: int | None = None


@dataclass(frozen=True)
class GuidanceCandidate:
    runway_id: str
    model: TurnModel
    status: GuidanceStatus
    turn_direction: TurnDirection
    full_turns: int
    partial_turn_angle_deg: float
    total_turn_angle_deg: float
    turn_entry: GeoPoint
    turn_exit: GeoPoint
    turn_entry_elapsed_time_s: float
    turn_entry_altitude_ft: float
    target_elapsed_time_s: float
    outbound_magnetic_course_deg: float
    direct_umk_magnetic_course_deg: float
    mze_radial_deg: float
    mze_dme_nm: float
    mze_horizontal_distance_nm: float
    position_residual_nm: float
    altitude_residual_ft: float
    tangent_residual_deg: float
    adjusted_ground_radius_nm: float | None
    maximum_required_bank_deg: float
    full_turn_exit_drift_nm: float
    constraints: tuple[ConstraintResult, ...]
    path: tuple[TrajectorySample, ...]

    @property
    def hard_valid(self) -> bool:
        return all(
            constraint.passed
            for constraint in self.constraints
            if constraint.severity is ConstraintSeverity.HARD
        )

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _to_json_value(self))


@dataclass(frozen=True)
class DepartureGuidanceResult:
    runway_id: str
    status: GuidanceStatus
    selected_candidate: GuidanceCandidate | None
    candidates: tuple[GuidanceCandidate, ...]
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _to_json_value(self))


@dataclass(frozen=True)
class _Vector:
    east: float
    north: float

    def __add__(self, other: _Vector) -> _Vector:
        return _Vector(self.east + other.east, self.north + other.north)

    def __sub__(self, other: _Vector) -> _Vector:
        return _Vector(self.east - other.east, self.north - other.north)

    def __mul__(self, scalar: float) -> _Vector:
        return _Vector(self.east * scalar, self.north * scalar)


@dataclass(frozen=True)
class _LocalFrame:
    origin: GeoPoint

    def to_local(self, point: GeoPoint) -> _Vector:
        inverse = Geodesic.WGS84.Inverse(
            self.origin.latitude_deg,
            self.origin.longitude_deg,
            point.latitude_deg,
            point.longitude_deg,
        )
        distance_nm = float(inverse["s12"]) / METERS_PER_NM
        bearing_rad = radians(float(inverse["azi1"]))
        return _Vector(distance_nm * sin(bearing_rad), distance_nm * cos(bearing_rad))

    def to_geo(self, vector: _Vector) -> GeoPoint:
        distance_nm = hypot(vector.east, vector.north)
        if distance_nm == 0:
            return self.origin
        bearing_deg = degrees(atan2(vector.east, vector.north))
        direct = Geodesic.WGS84.Direct(
            self.origin.latitude_deg,
            self.origin.longitude_deg,
            bearing_deg,
            distance_nm * METERS_PER_NM,
        )
        return GeoPoint(float(direct["lat2"]), float(direct["lon2"]))


@dataclass(frozen=True)
class _Profile:
    points: tuple[ClimbProfilePoint, ...]
    start_altitude_ft: float
    start_cumulative_time_min: float

    def cumulative_time_at(self, altitude_ft: float) -> float:
        for lower, upper in zip(self.points, self.points[1:], strict=False):
            if lower.altitude_ft <= altitude_ft <= upper.altitude_ft:
                fraction = (altitude_ft - lower.altitude_ft) / (
                    upper.altitude_ft - lower.altitude_ft
                )
                return lower.cumulative_time_min + fraction * (
                    upper.cumulative_time_min - lower.cumulative_time_min
                )
        if altitude_ft == self.points[-1].altitude_ft:
            return self.points[-1].cumulative_time_min
        raise ValueError("altitude is outside the POH profile")

    def elapsed_time_to(self, altitude_ft: float) -> float:
        return (self.cumulative_time_at(altitude_ft) - self.start_cumulative_time_min) * 60

    def altitude_at_elapsed(self, elapsed_time_s: float) -> float:
        cumulative_time = self.start_cumulative_time_min + elapsed_time_s / 60
        if cumulative_time <= self.points[0].cumulative_time_min:
            return self.points[0].altitude_ft
        for lower, upper in zip(self.points, self.points[1:], strict=False):
            if lower.cumulative_time_min <= cumulative_time <= upper.cumulative_time_min:
                fraction = (cumulative_time - lower.cumulative_time_min) / (
                    upper.cumulative_time_min - lower.cumulative_time_min
                )
                return lower.altitude_ft + fraction * (
                    upper.altitude_ft - lower.altitude_ft
                )
        return self.points[-1].altitude_ft


@dataclass(frozen=True)
class _PhysicalSolution:
    model: TurnModel
    full_turns: int
    partial_angle_rad: float
    outbound_time_s: float
    turn_time_s: float
    direct_time_s: float
    entry: _Vector
    exit: _Vector
    exit_heading_rad: float
    exit_ground_course_rad: float
    adjusted_radius_nm: float | None = None
    maximum_required_bank_deg: float = 20.0


@dataclass(frozen=True)
class _InitialPath:
    end_position: _Vector
    end_time_s: float
    outbound_heading_rad: float
    outbound_velocity: _Vector
    samples: tuple[TrajectorySample, ...]


def generate_rjfm_departure_guidance(
    request: DepartureGuidanceRequest,
) -> DepartureGuidanceResult:
    """Generate one runway's RJFM-to-UMK departure guidance without raising.

    Invalid or incomplete input returns ``UNSUPPORTED``. A valid input for
    which the timing/tangent equations have no physical solution returns
    ``NO_SOLUTION``. Constraint failures are retained on an ``INVALID``
    candidate so callers can draw diagnostic paths.
    """

    runway_id = request.runway_procedure.runway_id
    try:
        profile = _validate_request(request)
        frame = _LocalFrame(request.runway_origin)
        target = frame.to_local(request.target_umk)
        wind = _wind_vector(request.wind)
        initial = _build_initial_path(request, profile, frame, wind)
        target_time_s = profile.elapsed_time_to(request.target_altitude_ft)

        fixed_solutions = _solve_fixed_bank(
            request,
            initial,
            target,
            wind,
            target_time_s,
        )
        solutions = fixed_solutions
        issues: tuple[str, ...] = ()
        if not solutions:
            solutions, fallback_issue = _solve_adjusted_ground_circle(
                request,
                initial,
                target,
                wind,
                target_time_s,
            )
            if fallback_issue is not None:
                issues = (fallback_issue,)
        if not solutions:
            if issues:
                return DepartureGuidanceResult(
                    runway_id=runway_id,
                    status=GuidanceStatus.UNSUPPORTED,
                    selected_candidate=None,
                    candidates=(),
                    issues=issues,
                )
            return DepartureGuidanceResult(
                runway_id=runway_id,
                status=GuidanceStatus.NO_SOLUTION,
                selected_candidate=None,
                candidates=(),
                issues=issues
                + (
                    "No physical solution satisfies both the UMK target time "
                    "and tangent connection.",
                ),
            )

        candidates = tuple(
            _materialize_candidate(
                request,
                profile,
                frame,
                wind,
                initial,
                target_time_s,
                solution,
            )
            for solution in solutions
        )
        candidates = tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    candidate.total_turn_angle_deg,
                    -candidate.mze_horizontal_distance_nm,
                ),
            )
        )
        valid_candidates = tuple(candidate for candidate in candidates if candidate.hard_valid)
        selected = valid_candidates[0] if valid_candidates else candidates[0]
        return DepartureGuidanceResult(
            runway_id=runway_id,
            status=selected.status,
            selected_candidate=selected,
            candidates=candidates,
            issues=issues,
        )
    except (ValueError, WindTriangleError) as error:
        return DepartureGuidanceResult(
            runway_id=runway_id,
            status=GuidanceStatus.UNSUPPORTED,
            selected_candidate=None,
            candidates=(),
            issues=(str(error),),
        )


def is_allowed_center_route_magnetic_course(course_deg: float) -> bool:
    course = course_deg % 360.0
    return 272.0 < course < 360.0 or 0.0 <= course < 92.0


def _validate_request(request: DepartureGuidanceRequest) -> _Profile:
    _validate_point(request.runway_origin, "runway origin")
    _validate_point(request.target_umk, "UMK")
    _validate_point(request.mze.position, "MZE")
    for index, vertex in enumerate(request.pca_region.polygon_vertices):
        _validate_point(vertex, f"PCA vertex {index}")
    _validate_point(request.pca_region.exclusion_center, "PCA exclusion center")
    finite_values = (
        request.runway_elevation_ft,
        request.target_altitude_ft,
        request.mze.antenna_elevation_ft,
        request.mze.station_declination_deg_east,
        request.wind.speed_kt,
        request.tas_kt,
        request.magnetic_variation_deg_east,
        request.sample_interval_s,
        request.pca_region.exclusion_radius_nm,
        request.pca_region.floor_altitude_ft,
        request.pca_region.ceiling_altitude_ft,
    )
    if not all(isfinite(value) for value in finite_values):
        raise ValueError("guidance numeric inputs must be finite")
    if request.wind.direction_deg_true_from is not None and not isfinite(
        request.wind.direction_deg_true_from
    ):
        raise ValueError("wind direction must be finite when provided")
    if request.wind.speed_kt < 0:
        raise ValueError("wind speed cannot be negative")
    if request.wind.speed_kt > 0 and request.wind.direction_deg_true_from is None:
        raise ValueError("wind direction is required for non-calm wind")
    if request.tas_kt <= 0:
        raise ValueError("TAS must be positive")
    if request.sample_interval_s <= 0 or request.sample_interval_s > 5:
        raise ValueError("sample interval must be greater than zero and no more than 5 seconds")
    procedure = request.runway_procedure
    if abs(procedure.bank_deg - 20.0) > 1e-9:
        raise ValueError("RJFM departure guidance requires a 20-degree fixed bank")
    cutoff_count = sum(
        value is not None
        for value in (procedure.initial_distance_nm, procedure.initial_until_altitude_ft)
    )
    if cutoff_count != 1:
        raise ValueError("runway procedure requires exactly one initial cutoff condition")
    if procedure.initial_distance_nm is not None and procedure.initial_distance_nm <= 0:
        raise ValueError("initial distance must be positive")
    if (
        procedure.initial_until_altitude_ft is not None
        and procedure.initial_until_altitude_ft <= request.runway_elevation_ft
    ):
        raise ValueError("initial cutoff altitude must be above runway elevation")
    region = request.pca_region
    if len(region.polygon_vertices) < 3:
        raise ValueError("PCA polygon requires at least three vertices")
    if region.exclusion_radius_nm < 0:
        raise ValueError("PCA exclusion radius cannot be negative")
    if region.floor_altitude_ft > region.ceiling_altitude_ft:
        raise ValueError("PCA floor cannot exceed its ceiling")

    points = tuple(sorted(request.climb_profile.points, key=lambda point: point.altitude_ft))
    if len(points) < 2:
        raise ValueError("POH altitude/time profile requires at least two points")
    if not all(
        isfinite(point.altitude_ft) and isfinite(point.cumulative_time_min)
        for point in points
    ):
        raise ValueError("POH altitude/time profile must be finite")
    for lower, upper in zip(points, points[1:], strict=False):
        if upper.altitude_ft <= lower.altitude_ft:
            raise ValueError("POH profile altitudes must be strictly increasing")
        if upper.cumulative_time_min <= lower.cumulative_time_min:
            raise ValueError("POH profile times must be strictly increasing")
    if not points[0].altitude_ft <= request.runway_elevation_ft < points[-1].altitude_ft:
        raise ValueError("runway elevation is outside the POH altitude/time profile")
    profile = _Profile(
        points=points,
        start_altitude_ft=request.runway_elevation_ft,
        start_cumulative_time_min=0.0,
    )
    start_cumulative = profile.cumulative_time_at(request.runway_elevation_ft)
    profile = _Profile(points, request.runway_elevation_ft, start_cumulative)
    target_time = profile.elapsed_time_to(request.target_altitude_ft)
    if target_time <= 0:
        raise ValueError("target altitude must be above runway elevation")
    if (
        procedure.initial_until_altitude_ft is not None
        and procedure.initial_until_altitude_ft >= request.target_altitude_ft
    ):
        raise ValueError("initial cutoff altitude must be below target altitude")
    return profile


def _validate_point(point: GeoPoint, label: str) -> None:
    if not all(isfinite(value) for value in (point.latitude_deg, point.longitude_deg)):
        raise ValueError(f"{label} coordinate must be finite")
    if not -90 <= point.latitude_deg <= 90 or not -180 <= point.longitude_deg <= 180:
        raise ValueError(f"{label} coordinate is outside the WGS84 range")


def _wind_vector(wind: Wind) -> _Vector:
    if wind.speed_kt == 0:
        return _Vector(0.0, 0.0)
    assert wind.direction_deg_true_from is not None
    toward_rad = radians(wind.direction_deg_true_from + 180.0)
    speed = wind.speed_kt * KNOT_TO_NM_PER_SECOND
    return _Vector(speed * sin(toward_rad), speed * cos(toward_rad))


def _ground_velocity(heading_rad: float, tas_kt: float, wind: _Vector) -> _Vector:
    tas = tas_kt * KNOT_TO_NM_PER_SECOND
    return _Vector(tas * sin(heading_rad) + wind.east, tas * cos(heading_rad) + wind.north)


def _velocity_course(vector: _Vector) -> float:
    return atan2(vector.east, vector.north) % (2 * pi)


def _unit_for_course(course_rad: float) -> _Vector:
    return _Vector(sin(course_rad), cos(course_rad))


def _true_course(magnetic_course_deg: float, variation_deg_east: float) -> float:
    # AutoNavLog's established NAV2 convention is MC = TC + VAR.  Runway
    # procedure courses are magnetic, so invert that equation here.
    return radians((magnetic_course_deg - variation_deg_east) % 360.0)


def _magnetic_course(true_course_rad: float, variation_deg_east: float) -> float:
    return (degrees(true_course_rad) + variation_deg_east) % 360.0


def _heading_for_ground_course(
    course_rad: float,
    tas_kt: float,
    wind: Wind,
) -> tuple[float, float]:
    solved = solve_wind_triangle(
        degrees(course_rad),
        tas_kt,
        wind.direction_deg_true_from,
        wind.speed_kt,
    )
    return radians(solved.true_heading_deg), solved.ground_speed_kt * KNOT_TO_NM_PER_SECOND


def _turn_rate_rad_s(tas_kt: float, bank_deg: float) -> float:
    return STANDARD_GRAVITY_FT_S2 * tan(radians(bank_deg)) / (
        tas_kt * KNOT_TO_FEET_PER_SECOND
    )


def _turn_displacement(
    start_heading_rad: float,
    direction: TurnDirection,
    angle_rad: float,
    omega_rad_s: float,
    tas_kt: float,
    wind: _Vector,
) -> tuple[_Vector, float, float]:
    sign = direction.sign
    duration_s = angle_rad / omega_rad_s
    end_heading = start_heading_rad + sign * angle_rad
    tas = tas_kt * KNOT_TO_NM_PER_SECOND
    denominator = sign * omega_rad_s
    air = _Vector(
        tas * (cos(start_heading_rad) - cos(end_heading)) / denominator,
        tas * (sin(end_heading) - sin(start_heading_rad)) / denominator,
    )
    return air + wind * duration_s, duration_s, end_heading


def _build_initial_path(
    request: DepartureGuidanceRequest,
    profile: _Profile,
    frame: _LocalFrame,
    wind_vector: _Vector,
) -> _InitialPath:
    procedure = request.runway_procedure
    initial_course = _true_course(
        procedure.initial_ground_course_magnetic_deg,
        request.magnetic_variation_deg_east,
    )
    outbound_course = _true_course(
        procedure.post_cut_ground_course_magnetic_deg,
        request.magnetic_variation_deg_east,
    )
    initial_heading, initial_ground_speed = _heading_for_ground_course(
        initial_course,
        request.tas_kt,
        request.wind,
    )
    if procedure.initial_distance_nm is not None:
        initial_duration_s = procedure.initial_distance_nm / initial_ground_speed
    else:
        assert procedure.initial_until_altitude_ft is not None
        initial_duration_s = profile.elapsed_time_to(procedure.initial_until_altitude_ft)
    target_time_s = profile.elapsed_time_to(request.target_altitude_ft)
    if initial_duration_s >= target_time_s:
        raise ValueError("initial runway procedure consumes the complete climb-to-target time")
    initial_velocity = _unit_for_course(initial_course) * initial_ground_speed
    straight_end = initial_velocity * initial_duration_s
    outbound_heading, _ = _heading_for_ground_course(
        outbound_course,
        request.tas_kt,
        request.wind,
    )
    turn_delta = (
        (outbound_heading - initial_heading) % (2 * pi)
        if procedure.initial_turn_direction is TurnDirection.RIGHT
        else (initial_heading - outbound_heading) % (2 * pi)
    )
    if turn_delta > pi:
        raise ValueError("initial cut turn requires more than 180 degrees under the supplied wind")
    omega = _turn_rate_rad_s(request.tas_kt, procedure.bank_deg)
    turn_displacement, turn_duration, turn_end_heading = _turn_displacement(
        initial_heading,
        procedure.initial_turn_direction,
        turn_delta,
        omega,
        request.tas_kt,
        wind_vector,
    )
    end_time = initial_duration_s + turn_duration
    if end_time >= target_time_s:
        raise ValueError("initial runway procedure and 45-degree cut exceed target climb time")
    end_position = straight_end + turn_displacement
    outbound_velocity = _ground_velocity(turn_end_heading, request.tas_kt, wind_vector)

    samples: list[TrajectorySample] = [
        _sample(
            frame,
            profile,
            0.0,
            _Vector(0.0, 0.0),
            initial_heading,
            initial_course,
            PathPhase.INITIAL_STRAIGHT,
        )
    ]
    _sample_straight(
        samples,
        frame,
        profile,
        _Vector(0.0, 0.0),
        0.0,
        initial_velocity,
        initial_duration_s,
        initial_heading,
        initial_course,
        PathPhase.INITIAL_STRAIGHT,
        request.sample_interval_s,
    )
    _sample_fixed_turn(
        samples,
        frame,
        profile,
        straight_end,
        initial_duration_s,
        initial_heading,
        procedure.initial_turn_direction,
        turn_delta,
        omega,
        request.tas_kt,
        wind_vector,
        PathPhase.INITIAL_CUT_TURN,
        request.sample_interval_s,
    )
    return _InitialPath(
        end_position=end_position,
        end_time_s=end_time,
        outbound_heading_rad=turn_end_heading,
        outbound_velocity=outbound_velocity,
        samples=tuple(samples),
    )


def _solve_fixed_bank(
    request: DepartureGuidanceRequest,
    initial: _InitialPath,
    target: _Vector,
    wind: _Vector,
    target_time_s: float,
) -> tuple[_PhysicalSolution, ...]:
    omega = _turn_rate_rad_s(request.tas_kt, request.runway_procedure.bank_deg)
    full_turn_seconds = 2 * pi / omega
    maximum_full_turns = _maximum_turns_from_available_time(
        target_time_s - initial.end_time_s,
        full_turn_seconds,
    )
    results: list[_PhysicalSolution] = []
    for full_turns in range(maximum_full_turns + 1):
        roots = _find_partial_angle_roots(
            lambda angle, turns=full_turns: _fixed_bank_solution_at_angle(
                request,
                initial,
                target,
                wind,
                target_time_s,
                omega,
                turns,
                angle,
            )
        )
        for root in roots:
            solution = _fixed_bank_solution_at_angle(
                request,
                initial,
                target,
                wind,
                target_time_s,
                omega,
                full_turns,
                root,
            )
            if solution is not None:
                results.append(solution[0])
    return _deduplicate_solutions(results)


def _fixed_bank_solution_at_angle(
    request: DepartureGuidanceRequest,
    initial: _InitialPath,
    target: _Vector,
    wind: _Vector,
    target_time_s: float,
    omega: float,
    full_turns: int,
    partial_angle_rad: float,
) -> tuple[_PhysicalSolution, float] | None:
    total_angle = full_turns * 2 * pi + partial_angle_rad
    turn_displacement, turn_time, exit_heading = _turn_displacement(
        initial.outbound_heading_rad,
        request.runway_procedure.extension_turn_direction,
        total_angle,
        omega,
        request.tas_kt,
        wind,
    )
    exit_velocity = _ground_velocity(exit_heading, request.tas_kt, wind)
    remaining = target - initial.end_position - turn_displacement
    solved = _solve_two_columns(initial.outbound_velocity, exit_velocity, remaining)
    if solved is None:
        return None
    outbound_time, direct_time = solved
    if outbound_time < -1e-6 or direct_time < -1e-6:
        return None
    outbound_time = max(0.0, outbound_time)
    direct_time = max(0.0, direct_time)
    total_time = initial.end_time_s + outbound_time + turn_time + direct_time
    entry = initial.end_position + initial.outbound_velocity * outbound_time
    exit_position = entry + turn_displacement
    return (
        _PhysicalSolution(
            model=TurnModel.FIXED_BANK_AIR_MASS,
            full_turns=full_turns,
            partial_angle_rad=partial_angle_rad,
            outbound_time_s=outbound_time,
            turn_time_s=turn_time,
            direct_time_s=direct_time,
            entry=entry,
            exit=exit_position,
            exit_heading_rad=exit_heading,
            exit_ground_course_rad=_velocity_course(exit_velocity),
            maximum_required_bank_deg=request.runway_procedure.bank_deg,
        ),
        total_time - target_time_s,
    )


def _find_partial_angle_roots(
    evaluator: Any,
    *,
    subdivisions: int = 1440,
) -> tuple[float, ...]:
    epsilon = radians(0.02)
    upper = 2 * pi - epsilon
    evaluated: list[tuple[float, float]] = []
    roots: list[float] = []
    for index in range(subdivisions + 1):
        angle = epsilon + (upper - epsilon) * index / subdivisions
        result = evaluator(angle)
        if result is None:
            evaluated = []
            continue
        residual = result[1]
        if abs(residual) <= 0.01:
            roots.append(angle)
        if evaluated:
            previous_angle, previous_residual = evaluated[-1]
            if previous_residual * residual < 0:
                roots.append(
                    _bisect_root(evaluator, previous_angle, angle, previous_residual)
                )
        evaluated.append((angle, residual))
    return tuple(_deduplicate_angles(roots))


def _bisect_root(
    evaluator: Any,
    lower: float,
    upper: float,
    lower_value: float,
) -> float:
    for _ in range(60):
        midpoint = (lower + upper) / 2
        result = evaluator(midpoint)
        if result is None:
            break
        value = result[1]
        if abs(value) <= 1e-6:
            return midpoint
        if lower_value * value <= 0:
            upper = midpoint
        else:
            lower, lower_value = midpoint, value
    return (lower + upper) / 2


def _solve_adjusted_ground_circle(
    request: DepartureGuidanceRequest,
    initial: _InitialPath,
    target: _Vector,
    wind: _Vector,
    target_time_s: float,
) -> tuple[tuple[_PhysicalSolution, ...], str | None]:
    try:
        raw_radius = _maximum_fixed_bank_ground_radius(request, wind)
        radius, maximum_bank = _ensure_adjusted_bank_limit(request, raw_radius)
        lookup = _AdjustedTurnLookup.build(request, radius)
    except (ValueError, WindTriangleError) as error:
        return (), f"Adjusted-turn fallback is unsupported: {error}"

    results: list[_PhysicalSolution] = []
    maximum_full_turns = _maximum_turns_from_available_time(
        target_time_s - initial.end_time_s,
        lookup.full_circle_seconds,
    )
    for full_turns in range(maximum_full_turns + 1):
        roots = _find_partial_angle_roots(
            lambda angle, turns=full_turns: _adjusted_solution_at_angle(
                request,
                initial,
                target,
                target_time_s,
                radius,
                maximum_bank,
                lookup,
                turns,
                angle,
            )
        )
        for root in roots:
            solution = _adjusted_solution_at_angle(
                request,
                initial,
                target,
                target_time_s,
                radius,
                maximum_bank,
                lookup,
                full_turns,
                root,
            )
            if solution is not None:
                results.append(solution[0])
    return _deduplicate_solutions(results), None


def _maximum_turns_from_available_time(
    available_time_s: float,
    minimum_full_turn_time_s: float,
) -> int:
    if available_time_s <= 0 or minimum_full_turn_time_s <= 0:
        return 0
    maximum = int(available_time_s / minimum_full_turn_time_s)
    if maximum > MAX_DERIVED_FULL_TURNS:
        raise ValueError(
            "POH timing derives more than the defensive 60-turn computational ceiling"
        )
    return maximum


@dataclass(frozen=True)
class _AdjustedTurnLookup:
    step_rad: float
    cumulative_seconds: tuple[float, ...]
    start_course_rad: float
    radius_nm: float
    request: DepartureGuidanceRequest

    @classmethod
    def build(
        cls,
        request: DepartureGuidanceRequest,
        radius_nm: float,
        subdivisions: int = 1440,
    ) -> _AdjustedTurnLookup:
        start = _true_course(
            request.runway_procedure.post_cut_ground_course_magnetic_deg,
            request.magnetic_variation_deg_east,
        )
        step = 2 * pi / subdivisions
        cumulative = [0.0]
        previous_speed = _ground_speed_for_course(start, request)
        for index in range(1, subdivisions + 1):
            course = (
                start
                + request.runway_procedure.extension_turn_direction.sign * index * step
            )
            speed = _ground_speed_for_course(course, request)
            cumulative.append(
                cumulative[-1] + radius_nm * step * 2 / (previous_speed + speed)
            )
            previous_speed = speed
        return cls(step, tuple(cumulative), start, radius_nm, request)

    @property
    def full_circle_seconds(self) -> float:
        return self.cumulative_seconds[-1]

    def partial_seconds(self, angle_rad: float) -> float:
        normalized = min(max(angle_rad, 0.0), 2 * pi)
        position = normalized / self.step_rad
        lower = min(int(position), len(self.cumulative_seconds) - 2)
        fraction = position - lower
        return self.cumulative_seconds[lower] + fraction * (
            self.cumulative_seconds[lower + 1] - self.cumulative_seconds[lower]
        )


def _adjusted_solution_at_angle(
    request: DepartureGuidanceRequest,
    initial: _InitialPath,
    target: _Vector,
    target_time_s: float,
    radius_nm: float,
    maximum_bank_deg: float,
    lookup: _AdjustedTurnLookup,
    full_turns: int,
    partial_angle_rad: float,
) -> tuple[_PhysicalSolution, float] | None:
    start_course = lookup.start_course_rad
    direction = request.runway_procedure.extension_turn_direction
    exit_course = start_course + direction.sign * partial_angle_rad
    turn_displacement = _ground_circle_displacement(
        start_course,
        direction,
        partial_angle_rad,
        radius_nm,
    )
    remaining = target - initial.end_position - turn_displacement
    solved = _solve_two_columns(
        _unit_for_course(start_course),
        _unit_for_course(exit_course),
        remaining,
    )
    if solved is None:
        return None
    outbound_distance, direct_distance = solved
    if outbound_distance < -1e-6 or direct_distance < -1e-6:
        return None
    outbound_distance = max(0.0, outbound_distance)
    direct_distance = max(0.0, direct_distance)
    outbound_speed = hypot(initial.outbound_velocity.east, initial.outbound_velocity.north)
    exit_speed = _ground_speed_for_course(exit_course, request)
    outbound_time = outbound_distance / outbound_speed
    direct_time = direct_distance / exit_speed
    turn_time = full_turns * lookup.full_circle_seconds + lookup.partial_seconds(
        partial_angle_rad
    )
    total_time = initial.end_time_s + outbound_time + turn_time + direct_time
    entry = initial.end_position + _unit_for_course(start_course) * outbound_distance
    exit_position = entry + turn_displacement
    exit_heading, _ = _heading_for_ground_course(exit_course, request.tas_kt, request.wind)
    return (
        _PhysicalSolution(
            model=TurnModel.ADJUSTED_GROUND_CIRCLE,
            full_turns=full_turns,
            partial_angle_rad=partial_angle_rad,
            outbound_time_s=outbound_time,
            turn_time_s=turn_time,
            direct_time_s=direct_time,
            entry=entry,
            exit=exit_position,
            exit_heading_rad=exit_heading,
            exit_ground_course_rad=exit_course % (2 * pi),
            adjusted_radius_nm=radius_nm,
            maximum_required_bank_deg=maximum_bank_deg,
        ),
        total_time - target_time_s,
    )


def _ground_circle_displacement(
    start_course_rad: float,
    direction: TurnDirection,
    partial_angle_rad: float,
    radius_nm: float,
) -> _Vector:
    sign = direction.sign
    end_course = start_course_rad + sign * partial_angle_rad
    return _Vector(
        sign * radius_nm * (cos(start_course_rad) - cos(end_course)),
        sign * radius_nm * (sin(end_course) - sin(start_course_rad)),
    )


def _maximum_fixed_bank_ground_radius(
    request: DepartureGuidanceRequest,
    wind: _Vector,
) -> float:
    omega = _turn_rate_rad_s(request.tas_kt, request.runway_procedure.bank_deg)
    tas = request.tas_kt * KNOT_TO_NM_PER_SECOND
    radii: list[float] = []
    for index in range(1440):
        heading = 2 * pi * index / 1440
        velocity = _ground_velocity(heading, request.tas_kt, wind)
        acceleration = _Vector(-tas * cos(heading) * omega, tas * sin(heading) * omega)
        speed = hypot(velocity.east, velocity.north)
        cross = abs(_cross(velocity, acceleration))
        if cross <= 1e-15:
            raise ValueError("wind produces an unbounded fixed-bank ground radius")
        radii.append(speed**3 / cross)
    return max(radii)


def _ensure_adjusted_bank_limit(
    request: DepartureGuidanceRequest,
    radius_nm: float,
) -> tuple[float, float]:
    maximum_bank = _maximum_ground_circle_bank(request, radius_nm)
    limit = request.runway_procedure.bank_deg
    if maximum_bank > limit:
        radius_nm *= tan(radians(maximum_bank)) / tan(radians(limit)) * (1 + 1e-9)
        maximum_bank = _maximum_ground_circle_bank(request, radius_nm)
    if maximum_bank > limit + 1e-6:
        raise ValueError("adjusted ground circle would exceed the bank limit")
    return radius_nm, maximum_bank


def _maximum_ground_circle_bank(
    request: DepartureGuidanceRequest,
    radius_nm: float,
) -> float:
    epsilon = 1e-5
    maximum = 0.0
    tas_ft_s = request.tas_kt * KNOT_TO_FEET_PER_SECOND
    for index in range(720):
        course = 2 * pi * index / 720
        heading_minus, _ = _heading_for_ground_course(
            course - epsilon,
            request.tas_kt,
            request.wind,
        )
        heading_plus, speed = _heading_for_ground_course(
            course + epsilon,
            request.tas_kt,
            request.wind,
        )
        derivative = abs(_signed_angle(heading_plus, heading_minus)) / (2 * epsilon)
        course_rate = speed / radius_nm
        bank = degrees(atan(tas_ft_s * derivative * course_rate / STANDARD_GRAVITY_FT_S2))
        maximum = max(maximum, bank)
    return maximum


def _ground_speed_for_course(course_rad: float, request: DepartureGuidanceRequest) -> float:
    _, speed = _heading_for_ground_course(course_rad, request.tas_kt, request.wind)
    return speed


def _solve_two_columns(
    first: _Vector,
    second: _Vector,
    target: _Vector,
) -> tuple[float, float] | None:
    determinant = _cross(first, second)
    if abs(determinant) < 1e-12:
        return None
    first_factor = _cross(target, second) / determinant
    second_factor = _cross(first, target) / determinant
    if not all(isfinite(value) for value in (first_factor, second_factor)):
        return None
    return first_factor, second_factor


def _cross(first: _Vector, second: _Vector) -> float:
    return first.east * second.north - first.north * second.east


def _deduplicate_angles(angles: list[float]) -> list[float]:
    result: list[float] = []
    for angle in sorted(angles):
        if not result or abs(angle - result[-1]) > radians(0.01):
            result.append(angle)
    return result


def _deduplicate_solutions(solutions: list[_PhysicalSolution]) -> tuple[_PhysicalSolution, ...]:
    ordered = sorted(
        solutions,
        key=lambda solution: (solution.full_turns, solution.partial_angle_rad),
    )
    result: list[_PhysicalSolution] = []
    for solution in ordered:
        if result and (
            solution.full_turns == result[-1].full_turns
            and abs(solution.partial_angle_rad - result[-1].partial_angle_rad) < radians(0.01)
        ):
            continue
        result.append(solution)
    return tuple(result)


def _materialize_candidate(
    request: DepartureGuidanceRequest,
    profile: _Profile,
    frame: _LocalFrame,
    wind: _Vector,
    initial: _InitialPath,
    target_time_s: float,
    solution: _PhysicalSolution,
) -> GuidanceCandidate:
    path = list(initial.samples)
    outbound_course = _velocity_course(initial.outbound_velocity)
    _sample_straight(
        path,
        frame,
        profile,
        initial.end_position,
        initial.end_time_s,
        initial.outbound_velocity,
        solution.outbound_time_s,
        initial.outbound_heading_rad,
        outbound_course,
        PathPhase.OUTBOUND,
        request.sample_interval_s,
    )
    turn_start_time = initial.end_time_s + solution.outbound_time_s
    total_turn_angle = solution.full_turns * 2 * pi + solution.partial_angle_rad
    if solution.model is TurnModel.FIXED_BANK_AIR_MASS:
        _sample_fixed_turn(
            path,
            frame,
            profile,
            solution.entry,
            turn_start_time,
            initial.outbound_heading_rad,
            request.runway_procedure.extension_turn_direction,
            total_turn_angle,
            _turn_rate_rad_s(request.tas_kt, request.runway_procedure.bank_deg),
            request.tas_kt,
            wind,
            PathPhase.EXTENSION_TURN,
            request.sample_interval_s,
        )
    else:
        assert solution.adjusted_radius_nm is not None
        _sample_adjusted_turn(
            path,
            frame,
            profile,
            solution.entry,
            turn_start_time,
            outbound_course,
            total_turn_angle,
            solution.adjusted_radius_nm,
            request,
        )
    exit_velocity = _ground_velocity(solution.exit_heading_rad, request.tas_kt, wind)
    direct_start_time = turn_start_time + solution.turn_time_s
    _sample_straight(
        path,
        frame,
        profile,
        solution.exit,
        direct_start_time,
        exit_velocity,
        solution.direct_time_s,
        solution.exit_heading_rad,
        solution.exit_ground_course_rad,
        PathPhase.DIRECT_UMK,
        request.sample_interval_s,
    )
    final_position = path[-1].position
    position_residual = geodesic_leg(
        final_position.latitude_deg,
        final_position.longitude_deg,
        request.target_umk.latitude_deg,
        request.target_umk.longitude_deg,
    ).distance_nm
    altitude_residual = abs(path[-1].altitude_ft - request.target_altitude_ft)
    exit_geo = frame.to_geo(solution.exit)
    target_leg = geodesic_leg(
        exit_geo.latitude_deg,
        exit_geo.longitude_deg,
        request.target_umk.latitude_deg,
        request.target_umk.longitude_deg,
    )
    tangent_residual = abs(
        degrees(
            _signed_angle(
                radians(target_leg.initial_true_course_deg),
                solution.exit_ground_course_rad,
            )
        )
    )
    direct_magnetic = _magnetic_course(
        radians(target_leg.initial_true_course_deg), request.magnetic_variation_deg_east
    )
    outbound_magnetic = _magnetic_course(outbound_course, request.magnetic_variation_deg_east)
    entry_geo = frame.to_geo(solution.entry)
    entry_altitude = profile.altitude_at_elapsed(turn_start_time)
    horizontal = geodesic_leg(
        request.mze.position.latitude_deg,
        request.mze.position.longitude_deg,
        entry_geo.latitude_deg,
        entry_geo.longitude_deg,
    )
    vertical_nm = (entry_altitude - request.mze.antenna_elevation_ft) / FEET_PER_NM
    slant_dme = hypot(horizontal.distance_nm, vertical_nm)
    radial = (
        horizontal.initial_true_course_deg - request.mze.station_declination_deg_east
    ) % 360.0

    constraints = _candidate_constraints(
        request,
        tuple(path),
        outbound_magnetic,
        direct_magnetic,
        position_residual,
        altitude_residual,
        tangent_residual,
    )
    hard_valid = all(
        item.passed for item in constraints if item.severity is ConstraintSeverity.HARD
    )
    status = GuidanceStatus.VALID if hard_valid else GuidanceStatus.INVALID
    full_turn_exit_drift_nm = 0.0
    if solution.model is TurnModel.FIXED_BANK_AIR_MASS and solution.full_turns:
        full_turn_seconds = 2 * pi / _turn_rate_rad_s(
            request.tas_kt,
            request.runway_procedure.bank_deg,
        )
        full_turn_exit_drift_nm = (
            request.wind.speed_kt
            * KNOT_TO_NM_PER_SECOND
            * solution.full_turns
            * full_turn_seconds
        )
    return GuidanceCandidate(
        runway_id=request.runway_procedure.runway_id,
        model=solution.model,
        status=status,
        turn_direction=request.runway_procedure.extension_turn_direction,
        full_turns=solution.full_turns,
        partial_turn_angle_deg=degrees(solution.partial_angle_rad),
        total_turn_angle_deg=solution.full_turns * 360 + degrees(solution.partial_angle_rad),
        turn_entry=entry_geo,
        turn_exit=exit_geo,
        turn_entry_elapsed_time_s=turn_start_time,
        turn_entry_altitude_ft=entry_altitude,
        target_elapsed_time_s=target_time_s,
        outbound_magnetic_course_deg=outbound_magnetic,
        direct_umk_magnetic_course_deg=direct_magnetic,
        mze_radial_deg=radial,
        mze_dme_nm=slant_dme,
        mze_horizontal_distance_nm=horizontal.distance_nm,
        position_residual_nm=position_residual,
        altitude_residual_ft=altitude_residual,
        tangent_residual_deg=tangent_residual,
        adjusted_ground_radius_nm=solution.adjusted_radius_nm,
        maximum_required_bank_deg=solution.maximum_required_bank_deg,
        full_turn_exit_drift_nm=full_turn_exit_drift_nm,
        constraints=constraints,
        path=tuple(path),
    )


def _candidate_constraints(
    request: DepartureGuidanceRequest,
    path: tuple[TrajectorySample, ...],
    outbound_magnetic: float,
    direct_magnetic: float,
    position_residual: float,
    altitude_residual: float,
    tangent_residual: float,
) -> tuple[ConstraintResult, ...]:
    pca_sample = _first_pca_violation(path, request.pca_region)
    return (
        ConstraintResult(
            code="OUTBOUND_MC",
            passed=is_allowed_center_route_magnetic_course(outbound_magnetic),
            severity=ConstraintSeverity.HARD,
            message=(
                f"Outbound MC {outbound_magnetic:.6f} deg must be in (272, 360) or [0, 92)."
            ),
        ),
        ConstraintResult(
            code="DIRECT_UMK_MC",
            passed=is_allowed_center_route_magnetic_course(direct_magnetic),
            severity=ConstraintSeverity.HARD,
            message=(
                f"Direct-UMK MC {direct_magnetic:.6f} deg must be in (272, 360) or [0, 92)."
            ),
        ),
        ConstraintResult(
            code="PCA",
            passed=pca_sample is None,
            severity=ConstraintSeverity.HARD,
            message=(
                "Trajectory remains outside the PCA while in the configured vertical band."
                if pca_sample is None
                else (
                    "Trajectory enters the PCA while altitude is within the inclusive "
                    "vertical band."
                )
            ),
            sample_index=pca_sample,
        ),
        ConstraintResult(
            code="POSITION_RESIDUAL",
            passed=position_residual <= POSITION_TOLERANCE_NM,
            severity=ConstraintSeverity.HARD,
            message=f"UMK position residual is {position_residual:.6f} NM.",
        ),
        ConstraintResult(
            code="ALTITUDE_RESIDUAL",
            passed=altitude_residual <= ALTITUDE_TOLERANCE_FT,
            severity=ConstraintSeverity.HARD,
            message=f"UMK altitude residual is {altitude_residual:.3f} ft.",
        ),
        ConstraintResult(
            code="TANGENT_RESIDUAL",
            passed=tangent_residual <= TANGENT_TOLERANCE_DEG,
            severity=ConstraintSeverity.HARD,
            message=f"UMK tangent residual is {tangent_residual:.6f} deg.",
        ),
    )


def _sample(
    frame: _LocalFrame,
    profile: _Profile,
    elapsed_time_s: float,
    position: _Vector,
    heading_rad: float,
    ground_course_rad: float,
    phase: PathPhase,
) -> TrajectorySample:
    return TrajectorySample(
        elapsed_time_s=elapsed_time_s,
        position=frame.to_geo(position),
        altitude_ft=profile.altitude_at_elapsed(elapsed_time_s),
        true_heading_deg=degrees(heading_rad) % 360.0,
        ground_track_true_deg=degrees(ground_course_rad) % 360.0,
        phase=phase,
    )


def _sample_straight(
    samples: list[TrajectorySample],
    frame: _LocalFrame,
    profile: _Profile,
    start: _Vector,
    start_time_s: float,
    velocity: _Vector,
    duration_s: float,
    heading_rad: float,
    course_rad: float,
    phase: PathPhase,
    sample_interval_s: float,
) -> None:
    if duration_s <= 1e-9:
        return
    steps = max(1, ceil(duration_s / sample_interval_s))
    for index in range(1, steps + 1):
        elapsed = duration_s * index / steps
        samples.append(
            _sample(
                frame,
                profile,
                start_time_s + elapsed,
                start + velocity * elapsed,
                heading_rad,
                course_rad,
                phase,
            )
        )


def _sample_fixed_turn(
    samples: list[TrajectorySample],
    frame: _LocalFrame,
    profile: _Profile,
    start: _Vector,
    start_time_s: float,
    start_heading_rad: float,
    direction: TurnDirection,
    total_angle_rad: float,
    omega_rad_s: float,
    tas_kt: float,
    wind: _Vector,
    phase: PathPhase,
    sample_interval_s: float,
) -> None:
    duration_s = total_angle_rad / omega_rad_s
    if duration_s <= 1e-9:
        return
    steps = max(1, ceil(duration_s / sample_interval_s))
    for index in range(1, steps + 1):
        angle = total_angle_rad * index / steps
        displacement, elapsed, heading = _turn_displacement(
            start_heading_rad,
            direction,
            angle,
            omega_rad_s,
            tas_kt,
            wind,
        )
        velocity = _ground_velocity(heading, tas_kt, wind)
        samples.append(
            _sample(
                frame,
                profile,
                start_time_s + elapsed,
                start + displacement,
                heading,
                _velocity_course(velocity),
                phase,
            )
        )


def _sample_adjusted_turn(
    samples: list[TrajectorySample],
    frame: _LocalFrame,
    profile: _Profile,
    start: _Vector,
    start_time_s: float,
    start_course_rad: float,
    total_angle_rad: float,
    radius_nm: float,
    request: DepartureGuidanceRequest,
) -> None:
    if total_angle_rad <= 1e-9:
        return
    steps = max(1, ceil(degrees(total_angle_rad)))
    elapsed = 0.0
    previous_course = start_course_rad
    previous_speed = _ground_speed_for_course(previous_course, request)
    direction = request.runway_procedure.extension_turn_direction
    for index in range(1, steps + 1):
        angle = total_angle_rad * index / steps
        course = start_course_rad + direction.sign * angle
        speed = _ground_speed_for_course(course, request)
        delta_angle = total_angle_rad / steps
        elapsed += radius_nm * delta_angle * 2 / (previous_speed + speed)
        position = start + _ground_circle_displacement(
            start_course_rad,
            direction,
            angle,
            radius_nm,
        )
        heading, _ = _heading_for_ground_course(course, request.tas_kt, request.wind)
        samples.append(
            _sample(
                frame,
                profile,
                start_time_s + elapsed,
                position,
                heading,
                course,
                PathPhase.EXTENSION_TURN,
            )
        )
        previous_course = course
        previous_speed = speed


def _first_pca_violation(
    path: tuple[TrajectorySample, ...],
    region: PcaRegion,
) -> int | None:
    frame = _LocalFrame(region.exclusion_center)
    polygon = tuple(frame.to_local(point) for point in region.polygon_vertices)
    path_points = tuple(frame.to_local(sample.position) for sample in path)
    for index, (first, second) in enumerate(zip(path, path[1:], strict=False)):
        interval = _altitude_band_interval(
            first.altitude_ft,
            second.altitude_ft,
            region.floor_altitude_ft,
            region.ceiling_altitude_ft,
        )
        if interval is None:
            continue
        start_fraction, end_fraction = interval
        start = _interpolate_vector(path_points[index], path_points[index + 1], start_fraction)
        end = _interpolate_vector(path_points[index], path_points[index + 1], end_fraction)
        if _segment_intersects_protected_pca(
            start,
            end,
            polygon,
            region.exclusion_radius_nm,
        ):
            return index
    if path:
        final = path[-1]
        if (
            region.floor_altitude_ft <= final.altitude_ft <= region.ceiling_altitude_ft
            and _inside_protected_pca(path_points[-1], polygon, region.exclusion_radius_nm)
        ):
            return len(path) - 1
    return None


def _altitude_band_interval(
    start_altitude: float,
    end_altitude: float,
    floor_altitude: float,
    ceiling_altitude: float,
) -> tuple[float, float] | None:
    if start_altitude == end_altitude:
        return (0.0, 1.0) if floor_altitude <= start_altitude <= ceiling_altitude else None
    first = (floor_altitude - start_altitude) / (end_altitude - start_altitude)
    second = (ceiling_altitude - start_altitude) / (end_altitude - start_altitude)
    lower = max(0.0, min(first, second))
    upper = min(1.0, max(first, second))
    if lower > upper:
        return None
    return lower, upper


def _segment_intersects_protected_pca(
    start: _Vector,
    end: _Vector,
    polygon: tuple[_Vector, ...],
    exclusion_radius_nm: float,
) -> bool:
    fractions = [0.0, 1.0]
    for edge_start, edge_end in zip(polygon, polygon[1:] + polygon[:1], strict=False):
        intersection = _segment_intersection_fraction(start, end, edge_start, edge_end)
        if intersection is not None:
            fractions.append(intersection)
    fractions.extend(_circle_intersection_fractions(start, end, exclusion_radius_nm))
    ordered = sorted(set(round(value, 12) for value in fractions if 0 <= value <= 1))
    probes = list(ordered)
    probes.extend((lower + upper) / 2 for lower, upper in zip(ordered, ordered[1:], strict=False))
    return any(
        _inside_protected_pca(
            _interpolate_vector(start, end, fraction),
            polygon,
            exclusion_radius_nm,
        )
        for fraction in probes
    )


def _segment_intersection_fraction(
    start: _Vector,
    end: _Vector,
    edge_start: _Vector,
    edge_end: _Vector,
) -> float | None:
    direction = end - start
    edge_direction = edge_end - edge_start
    determinant = _cross(direction, edge_direction)
    if abs(determinant) < 1e-12:
        return None
    relative = edge_start - start
    along = _cross(relative, edge_direction) / determinant
    edge_along = _cross(relative, direction) / determinant
    if 0 <= along <= 1 and 0 <= edge_along <= 1:
        return along
    return None


def _circle_intersection_fractions(
    start: _Vector,
    end: _Vector,
    radius: float,
) -> tuple[float, ...]:
    direction = end - start
    a = direction.east**2 + direction.north**2
    if a <= 1e-18:
        return ()
    b = 2 * (start.east * direction.east + start.north * direction.north)
    c = start.east**2 + start.north**2 - radius**2
    discriminant = b**2 - 4 * a * c
    if discriminant < 0:
        return ()
    root = sqrt(max(0.0, discriminant))
    return tuple(
        value
        for value in ((-b - root) / (2 * a), (-b + root) / (2 * a))
        if 0 <= value <= 1
    )


def _inside_protected_pca(
    point: _Vector,
    polygon: tuple[_Vector, ...],
    exclusion_radius_nm: float,
) -> bool:
    if hypot(point.east, point.north) < exclusion_radius_nm - 1e-12:
        return False
    inside = False
    for first, second in zip(polygon, polygon[1:] + polygon[:1], strict=False):
        if _point_on_segment(point, first, second):
            return True
        crosses = (first.north > point.north) != (second.north > point.north)
        if crosses:
            east_at_crossing = first.east + (
                (point.north - first.north)
                * (second.east - first.east)
                / (second.north - first.north)
            )
            if point.east < east_at_crossing:
                inside = not inside
    return inside


def _point_on_segment(point: _Vector, start: _Vector, end: _Vector) -> bool:
    segment = end - start
    relative = point - start
    if abs(_cross(segment, relative)) > 1e-9:
        return False
    dot = relative.east * segment.east + relative.north * segment.north
    length_squared = segment.east**2 + segment.north**2
    return -1e-12 <= dot <= length_squared + 1e-12


def _interpolate_vector(start: _Vector, end: _Vector, fraction: float) -> _Vector:
    return start + (end - start) * fraction


def _signed_angle(first: float, second: float) -> float:
    return (first - second + pi) % (2 * pi) - pi


def _to_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            key: _to_json_value(item)
            for key, item in asdict(cast(Any, value)).items()
        }
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_json_value(item) for item in value]
    return value


__all__ = [
    "ALTITUDE_TOLERANCE_FT",
    "POSITION_TOLERANCE_NM",
    "TANGENT_TOLERANCE_DEG",
    "ClimbProfilePoint",
    "ConstraintResult",
    "ConstraintSeverity",
    "DepartureGuidanceRequest",
    "DepartureGuidanceResult",
    "GeoPoint",
    "GuidanceCandidate",
    "GuidanceStatus",
    "Navaid",
    "PathPhase",
    "PcaRegion",
    "PohAltitudeTimeProfile",
    "RunwayProcedure",
    "TrajectorySample",
    "TurnDirection",
    "TurnModel",
    "Wind",
    "generate_rjfm_departure_guidance",
    "is_allowed_center_route_magnetic_course",
]
