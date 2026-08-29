from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from autonavlog.domain.enums import FlightPhase
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg

SourceId = str | UUID

RCA_LABEL = "RCA"
EOC_LABEL = "EOC"
DESCENT_END_LABEL = "DESCENT_END"

_DISTANCE_TOLERANCE_NM = 1e-9
_COORDINATE_TOLERANCE_DEG = 1e-9
_MARKER_ORDER = (RCA_LABEL, EOC_LABEL, DESCENT_END_LABEL)
EOC_SNAP_TOLERANCE_NM = 0.5


class PhaseSegmentationError(ValueError):
    """Raised when physical legs or phase boundaries cannot form a safe partition."""


@dataclass(frozen=True)
class PhysicalRouteLeg:
    """One ordered physical leg and its adopted planning distance."""

    source_index: int
    source_id: SourceId
    start_name: str
    start_latitude_deg: float
    start_longitude_deg: float
    end_name: str
    end_latitude_deg: float
    end_longitude_deg: float
    adopted_distance_nm: float
    start_node_id: UUID | None = None
    end_node_id: UUID | None = None


@dataclass(frozen=True)
class RouteBoundary:
    """A named non-physical cut on the adopted route distance axis."""

    distance_nm: float
    label: str


@dataclass(frozen=True)
class SegmentPoint:
    """A physical or derived point on the adopted along-route distance axis."""

    label: str
    latitude_deg: float
    longitude_deg: float
    along_route_distance_nm: float
    source_name: str | None = None
    source_node_id: UUID | None = None
    markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhaseSegment:
    """A distance-conserving sub-segment contained within one physical source leg."""

    sequence: int
    source_index: int
    source_id: SourceId
    phase: FlightPhase
    start: SegmentPoint
    end: SegmentPoint
    start_distance_nm: float
    end_distance_nm: float
    distance_nm: float


@dataclass(frozen=True)
class PhaseSegmentation:
    """Complete phase partition plus stable references to derived boundaries."""

    segments: tuple[PhaseSegment, ...]
    total_distance_nm: float
    rca_point: SegmentPoint | None = None
    eoc_point: SegmentPoint | None = None
    descent_end_point: SegmentPoint | None = None


def split_route_into_phase_segments(
    legs: tuple[PhysicalRouteLeg, ...] | list[PhysicalRouteLeg],
    *,
    rca_distance_nm: float | None = None,
    eoc_distance_nm: float | None = None,
    descent_end_distance_nm: float | None = None,
    visual_leg_source_id: SourceId | None = None,
    additional_boundaries: Sequence[RouteBoundary] = (),
) -> PhaseSegmentation:
    """Split ordered physical legs at phase boundaries without losing distance.

    Phase priority is CLIMB from route start through RCA, DESCENT from EOC
    through the descent end, then VISUAL_ARRIVAL on the caller-designated final
    physical leg after the descent end (if supplied). All remaining distance is
    CRUISE.
    """

    ordered = tuple(legs)
    starts, physical_boundaries = _validate_and_measure_legs(ordered)
    total_distance_nm = physical_boundaries[-1]
    eoc_distance_nm = _snap_eoc_to_physical_turn(
        eoc_distance_nm,
        physical_boundaries,
        excluded_boundaries=(descent_end_distance_nm,),
    )
    _validate_phase_boundaries(
        total_distance_nm,
        rca_distance_nm,
        eoc_distance_nm,
        descent_end_distance_nm,
    )
    _validate_additional_boundaries(total_distance_nm, additional_boundaries)
    visual_leg_index = _validate_visual_leg(ordered, visual_leg_source_id)

    cuts: list[tuple[float, set[str]]] = [(distance, set()) for distance in physical_boundaries]
    _add_boundary(cuts, rca_distance_nm, RCA_LABEL)
    _add_boundary(cuts, eoc_distance_nm, EOC_LABEL)
    _add_boundary(cuts, descent_end_distance_nm, DESCENT_END_LABEL)
    for boundary in additional_boundaries:
        _add_boundary(cuts, boundary.distance_nm, boundary.label)
    cuts.sort(key=lambda item: item[0])

    points = {
        distance: _point_at_distance(
            ordered,
            starts,
            physical_boundaries,
            distance,
            markers,
        )
        for distance, markers in cuts
    }

    segments: list[PhaseSegment] = []
    for start_cut, end_cut in zip(cuts, cuts[1:], strict=False):
        start_distance = start_cut[0]
        end_distance = end_cut[0]
        distance = end_distance - start_distance
        if distance <= _DISTANCE_TOLERANCE_NM:
            continue
        midpoint = start_distance + distance / 2.0
        leg_index = min(
            bisect_right(starts, midpoint) - 1,
            len(ordered) - 1,
        )
        leg = ordered[leg_index]
        phase = _phase_at_distance(
            midpoint,
            leg_index,
            rca_distance_nm,
            eoc_distance_nm,
            descent_end_distance_nm,
            visual_leg_index,
        )
        segments.append(
            PhaseSegment(
                sequence=len(segments),
                source_index=leg.source_index,
                source_id=leg.source_id,
                phase=phase,
                start=points[start_distance],
                end=points[end_distance],
                start_distance_nm=start_distance,
                end_distance_nm=end_distance,
                distance_nm=distance,
            )
        )

    segmented_distance = sum(segment.distance_nm for segment in segments)
    if abs(segmented_distance - total_distance_nm) > _DISTANCE_TOLERANCE_NM:
        raise PhaseSegmentationError(
            "phase segmentation did not conserve the adopted route distance"
        )
    for previous, current in zip(segments, segments[1:], strict=False):
        if previous.end != current.start:
            raise PhaseSegmentationError(
                "phase segmentation produced non-contiguous segment endpoints"
            )
    if visual_leg_source_id is not None and not any(
        segment.phase == FlightPhase.VISUAL_ARRIVAL for segment in segments
    ):
        raise PhaseSegmentationError(
            "the designated visual leg has no distance available for VISUAL_ARRIVAL"
        )

    marker_points = {marker: point for point in points.values() for marker in point.markers}
    return PhaseSegmentation(
        segments=tuple(segments),
        total_distance_nm=total_distance_nm,
        rca_point=marker_points.get(RCA_LABEL),
        eoc_point=marker_points.get(EOC_LABEL),
        descent_end_point=marker_points.get(DESCENT_END_LABEL),
    )


def _validate_and_measure_legs(
    legs: tuple[PhysicalRouteLeg, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if not legs:
        raise PhaseSegmentationError("at least one physical leg is required")
    source_indices: set[int] = set()
    source_ids: set[SourceId] = set()
    starts: list[float] = []
    boundaries = [0.0]
    for position, leg in enumerate(legs):
        if isinstance(leg.source_index, bool) or leg.source_index < 0:
            raise PhaseSegmentationError("physical leg source indices must be non-negative")
        if leg.source_index in source_indices:
            raise PhaseSegmentationError("physical leg source indices must be unique")
        if leg.source_id in source_ids:
            raise PhaseSegmentationError("physical leg source ids must be unique")
        source_indices.add(leg.source_index)
        source_ids.add(leg.source_id)
        if not leg.start_name or not leg.end_name:
            raise PhaseSegmentationError("physical leg endpoint names cannot be empty")
        _validate_coordinate(leg.start_latitude_deg, leg.start_longitude_deg)
        _validate_coordinate(leg.end_latitude_deg, leg.end_longitude_deg)
        if not isfinite(leg.adopted_distance_nm) or leg.adopted_distance_nm <= 0:
            raise PhaseSegmentationError(
                "physical leg adopted distances must be finite and positive"
            )
        if _coordinates_equal(
            leg.start_latitude_deg,
            leg.start_longitude_deg,
            leg.end_latitude_deg,
            leg.end_longitude_deg,
        ):
            raise PhaseSegmentationError(
                "a positive-distance physical leg cannot have identical endpoints"
            )
        if position:
            previous = legs[position - 1]
            if not _coordinates_equal(
                previous.end_latitude_deg,
                previous.end_longitude_deg,
                leg.start_latitude_deg,
                leg.start_longitude_deg,
            ):
                raise PhaseSegmentationError(
                    "physical legs must be coordinate-contiguous in input order"
                )
        starts.append(boundaries[-1])
        boundaries.append(boundaries[-1] + leg.adopted_distance_nm)
    return tuple(starts), tuple(boundaries)


def _validate_coordinate(latitude_deg: float, longitude_deg: float) -> None:
    if (
        not isfinite(latitude_deg)
        or not isfinite(longitude_deg)
        or not -90 <= latitude_deg <= 90
        or not -180 <= longitude_deg <= 180
    ):
        raise PhaseSegmentationError("physical leg coordinates are invalid")


def _coordinates_equal(
    first_latitude_deg: float,
    first_longitude_deg: float,
    second_latitude_deg: float,
    second_longitude_deg: float,
) -> bool:
    return (
        abs(first_latitude_deg - second_latitude_deg) <= _COORDINATE_TOLERANCE_DEG
        and abs(first_longitude_deg - second_longitude_deg) <= _COORDINATE_TOLERANCE_DEG
    )


def _validate_phase_boundaries(
    total_distance_nm: float,
    rca_distance_nm: float | None,
    eoc_distance_nm: float | None,
    descent_end_distance_nm: float | None,
) -> None:
    for label, value in (
        (RCA_LABEL, rca_distance_nm),
        (EOC_LABEL, eoc_distance_nm),
        (DESCENT_END_LABEL, descent_end_distance_nm),
    ):
        if value is None:
            continue
        if not isfinite(value) or value < 0 or value > total_distance_nm:
            raise PhaseSegmentationError(
                f"{label} distance must be within the adopted route distance"
            )
    if rca_distance_nm is not None and rca_distance_nm <= 0:
        raise PhaseSegmentationError("RCA distance must be greater than route start")
    if (eoc_distance_nm is None) != (descent_end_distance_nm is None):
        raise PhaseSegmentationError("EOC and descent end distances must be supplied together")
    if (
        eoc_distance_nm is not None
        and descent_end_distance_nm is not None
        and eoc_distance_nm >= descent_end_distance_nm
    ):
        raise PhaseSegmentationError("EOC distance must be less than descent end distance")
    if (
        rca_distance_nm is not None
        and eoc_distance_nm is not None
        and rca_distance_nm > eoc_distance_nm
    ):
        raise PhaseSegmentationError("CLIMB and DESCENT phase ranges overlap")


def _validate_additional_boundaries(
    total_distance_nm: float,
    boundaries: Sequence[RouteBoundary],
) -> None:
    for boundary in boundaries:
        if (
            not isfinite(boundary.distance_nm)
            or boundary.distance_nm <= _DISTANCE_TOLERANCE_NM
            or boundary.distance_nm >= total_distance_nm - _DISTANCE_TOLERANCE_NM
        ):
            raise PhaseSegmentationError("additional boundary must be strictly inside the route")
        if not boundary.label.strip() or boundary.label in _MARKER_ORDER:
            raise PhaseSegmentationError("additional boundary label is invalid")


def _validate_visual_leg(
    legs: tuple[PhysicalRouteLeg, ...],
    visual_leg_source_id: SourceId | None,
) -> int | None:
    if visual_leg_source_id is None:
        return None
    matching = [index for index, leg in enumerate(legs) if leg.source_id == visual_leg_source_id]
    if not matching:
        raise PhaseSegmentationError("the designated visual leg does not exist")
    if matching[0] != len(legs) - 1:
        raise PhaseSegmentationError("the designated visual leg must be the final physical leg")
    return matching[0]


def _add_boundary(
    cuts: list[tuple[float, set[str]]],
    distance_nm: float | None,
    marker: str,
) -> None:
    if distance_nm is None:
        return
    for existing_distance, markers in cuts:
        if abs(existing_distance - distance_nm) <= _DISTANCE_TOLERANCE_NM:
            markers.add(marker)
            return
    cuts.append((distance_nm, {marker}))


def _snap_eoc_to_physical_turn(
    distance_nm: float | None,
    physical_boundaries: tuple[float, ...],
    *,
    excluded_boundaries: tuple[float | None, ...] = (),
) -> float | None:
    """Snap EOC to an eligible physical turn when strictly within 0.5 NM.

    The descent end (normally VREP) is excluded so an EOC cannot collapse onto
    its own terminal boundary.
    """

    if distance_nm is None or not isfinite(distance_nm):
        return distance_nm
    candidates = tuple(
        boundary
        for boundary in physical_boundaries[1:-1]
        if not any(
            excluded is not None and abs(boundary - excluded) <= _DISTANCE_TOLERANCE_NM
            for excluded in excluded_boundaries
        )
    )
    if not candidates:
        return distance_nm
    nearest = min(candidates, key=lambda boundary: abs(boundary - distance_nm))
    if abs(nearest - distance_nm) < EOC_SNAP_TOLERANCE_NM:
        return nearest
    return distance_nm


def _point_at_distance(
    legs: tuple[PhysicalRouteLeg, ...],
    starts: tuple[float, ...],
    boundaries: tuple[float, ...],
    distance_nm: float,
    markers: set[str],
) -> SegmentPoint:
    phase_markers = tuple(marker for marker in _MARKER_ORDER if marker in markers)
    other_markers = tuple(sorted(markers.difference(_MARKER_ORDER)))
    marker_tuple = phase_markers + other_markers
    physical_index = next(
        (
            index
            for index, boundary in enumerate(boundaries)
            if abs(boundary - distance_nm) <= _DISTANCE_TOLERANCE_NM
        ),
        None,
    )
    source_name: str | None = None
    source_node_id: UUID | None = None
    if physical_index is not None:
        if physical_index == 0:
            latitude = legs[0].start_latitude_deg
            longitude = legs[0].start_longitude_deg
            source_name = legs[0].start_name
            source_node_id = legs[0].start_node_id
        else:
            leg = legs[physical_index - 1]
            latitude = leg.end_latitude_deg
            longitude = leg.end_longitude_deg
            source_name = leg.end_name
            source_node_id = leg.end_node_id
    else:
        leg_index = min(
            bisect_right(starts, distance_nm) - 1,
            len(legs) - 1,
        )
        leg = legs[leg_index]
        fraction = (distance_nm - starts[leg_index]) / leg.adopted_distance_nm
        geometry = geodesic_leg(
            leg.start_latitude_deg,
            leg.start_longitude_deg,
            leg.end_latitude_deg,
            leg.end_longitude_deg,
        )
        latitude, longitude = point_along_leg(
            leg.start_latitude_deg,
            leg.start_longitude_deg,
            geometry.initial_true_course_deg,
            geometry.distance_nm * fraction,
        )
    # DESCENT_END is an internal calculation boundary, not a NAV LOG waypoint.
    # At the physical VREP boundary, retain the imported point name for display.
    display_markers = tuple(marker for marker in marker_tuple if marker != DESCENT_END_LABEL)
    label: str | None
    if source_name is not None and EOC_LABEL in display_markers:
        other_display_markers = tuple(
            marker for marker in display_markers if marker != EOC_LABEL
        )
        label_parts = (source_name, *other_display_markers, EOC_LABEL)
        label = " / ".join(dict.fromkeys(label_parts))
    else:
        label = "/".join(display_markers) if display_markers else source_name
    if label is None and DESCENT_END_LABEL in marker_tuple:
        label = DESCENT_END_LABEL
    if label is None:
        raise PhaseSegmentationError("an internal split point is missing a stable label")
    return SegmentPoint(
        label=label,
        latitude_deg=latitude,
        longitude_deg=longitude,
        along_route_distance_nm=distance_nm,
        source_name=source_name,
        source_node_id=source_node_id,
        markers=marker_tuple,
    )


def _phase_at_distance(
    distance_nm: float,
    leg_index: int,
    rca_distance_nm: float | None,
    eoc_distance_nm: float | None,
    descent_end_distance_nm: float | None,
    visual_leg_index: int | None,
) -> FlightPhase:
    if rca_distance_nm is not None and distance_nm < rca_distance_nm:
        return FlightPhase.CLIMB
    if (
        eoc_distance_nm is not None
        and descent_end_distance_nm is not None
        and eoc_distance_nm <= distance_nm < descent_end_distance_nm
    ):
        return FlightPhase.DESCENT
    if visual_leg_index is not None and leg_index == visual_leg_index:
        if descent_end_distance_nm is None or distance_nm >= descent_end_distance_nm:
            return FlightPhase.VISUAL_ARRIVAL
    return FlightPhase.CRUISE
