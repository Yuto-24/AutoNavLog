from __future__ import annotations

import pytest

from autonavlog.application.phase_segments import (
    PhaseSegmentationError,
    PhysicalRouteLeg,
    RouteBoundary,
    split_route_into_phase_segments,
)
from autonavlog.domain.enums import FlightPhase


def _leg() -> PhysicalRouteLeg:
    return PhysicalRouteLeg(
        source_index=0,
        source_id="leg-1",
        start_name="A",
        start_latitude_deg=31.0,
        start_longitude_deg=131.0,
        end_name="B",
        end_latitude_deg=31.2,
        end_longitude_deg=131.2,
        adopted_distance_nm=10.0,
    )


def test_additional_boundaries_split_without_changing_distance_or_phase() -> None:
    result = split_route_into_phase_segments(
        [_leg()],
        rca_distance_nm=5.0,
        additional_boundaries=(
            RouteBoundary(distance_nm=2.0, label="CP ALPHA"),
            RouteBoundary(distance_nm=7.0, label="CP BRAVO"),
        ),
    )

    assert [segment.distance_nm for segment in result.segments] == [
        2.0,
        3.0,
        2.0,
        3.0,
    ]
    assert [segment.phase for segment in result.segments] == [
        FlightPhase.CLIMB,
        FlightPhase.CLIMB,
        FlightPhase.CRUISE,
        FlightPhase.CRUISE,
    ]
    assert sum(segment.distance_nm for segment in result.segments) == 10.0
    assert result.segments[0].end.label == "CP ALPHA"
    assert result.segments[2].end.label == "CP BRAVO"


def test_additional_boundary_can_share_a_phase_cut_without_zero_segment() -> None:
    result = split_route_into_phase_segments(
        [_leg()],
        rca_distance_nm=5.0,
        additional_boundaries=(RouteBoundary(distance_nm=5.0, label="CP ALPHA"),),
    )

    assert len(result.segments) == 2
    shared = result.segments[0].end
    assert shared.markers == ("RCA", "CP ALPHA")
    assert shared.label == "RCA/CP ALPHA"


@pytest.mark.parametrize("distance", [0.0, 10.0, float("nan")])
def test_additional_boundary_must_be_strictly_inside_route(
    distance: float,
) -> None:
    with pytest.raises(PhaseSegmentationError, match="strictly inside"):
        split_route_into_phase_segments(
            [_leg()],
            additional_boundaries=(RouteBoundary(distance_nm=distance, label="CP"),),
        )
