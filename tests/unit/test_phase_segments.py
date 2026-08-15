from __future__ import annotations

import pytest

from autonavlog.application.phase_segments import (
    EOC_LABEL,
    RCA_LABEL,
    PhaseSegmentationError,
    PhysicalRouteLeg,
    split_route_into_phase_segments,
)
from autonavlog.domain.enums import FlightPhase


def _legs() -> list[PhysicalRouteLeg]:
    return [
        PhysicalRouteLeg(
            source_index=10,
            source_id="leg-a",
            start_name="A",
            start_latitude_deg=31.0,
            start_longitude_deg=131.0,
            end_name="B",
            end_latitude_deg=31.0,
            end_longitude_deg=131.1,
            adopted_distance_nm=10.0,
        ),
        PhysicalRouteLeg(
            source_index=20,
            source_id="leg-b",
            start_name="B",
            start_latitude_deg=31.0,
            start_longitude_deg=131.1,
            end_name="C",
            end_latitude_deg=31.1,
            end_longitude_deg=131.2,
            adopted_distance_nm=20.0,
        ),
        PhysicalRouteLeg(
            source_index=30,
            source_id="leg-c",
            start_name="C",
            start_latitude_deg=31.1,
            start_longitude_deg=131.2,
            end_name="D",
            end_latitude_deg=31.2,
            end_longitude_deg=131.3,
            adopted_distance_nm=30.0,
        ),
    ]


def test_partition_conserves_distance_and_assigns_all_phases() -> None:
    result = split_route_into_phase_segments(
        _legs(),
        rca_distance_nm=5.0,
        eoc_distance_nm=25.0,
        descent_end_distance_nm=45.0,
        visual_leg_source_id="leg-c",
    )

    assert result.total_distance_nm == pytest.approx(60.0)
    assert [segment.distance_nm for segment in result.segments] == pytest.approx(
        [5.0, 5.0, 15.0, 5.0, 15.0, 15.0]
    )
    assert [segment.phase for segment in result.segments] == [
        FlightPhase.CLIMB,
        FlightPhase.CRUISE,
        FlightPhase.CRUISE,
        FlightPhase.DESCENT,
        FlightPhase.DESCENT,
        FlightPhase.VISUAL_ARRIVAL,
    ]
    assert [segment.source_id for segment in result.segments] == [
        "leg-a",
        "leg-a",
        "leg-b",
        "leg-b",
        "leg-c",
        "leg-c",
    ]
    assert sum(segment.distance_nm for segment in result.segments) == pytest.approx(
        result.total_distance_nm
    )
    assert all(
        previous.end is current.start
        for previous, current in zip(
            result.segments,
            result.segments[1:],
            strict=False,
        )
    )
    assert result.rca_point is not None
    assert result.rca_point.label == RCA_LABEL
    assert result.eoc_point is not None
    assert result.eoc_point.label == EOC_LABEL
    assert result.descent_end_point is not None
    assert result.descent_end_point.label == "DESCENT_END"


def test_rca_can_cross_physical_leg_boundaries() -> None:
    result = split_route_into_phase_segments(
        _legs(),
        rca_distance_nm=25.0,
    )

    assert [
        (segment.source_id, segment.phase, segment.start_distance_nm, segment.end_distance_nm)
        for segment in result.segments
    ] == [
        ("leg-a", FlightPhase.CLIMB, 0.0, 10.0),
        ("leg-b", FlightPhase.CLIMB, 10.0, 25.0),
        ("leg-b", FlightPhase.CRUISE, 25.0, 30.0),
        ("leg-c", FlightPhase.CRUISE, 30.0, 60.0),
    ]


def test_boundary_exactly_on_physical_node_keeps_coordinate_and_stable_label() -> None:
    result = split_route_into_phase_segments(
        _legs(),
        rca_distance_nm=10.0,
        eoc_distance_nm=30.0,
        descent_end_distance_nm=50.0,
        visual_leg_source_id="leg-c",
    )

    assert result.rca_point is not None
    assert result.rca_point.label == "RCA"
    assert result.rca_point.source_name == "B"
    assert (result.rca_point.latitude_deg, result.rca_point.longitude_deg) == (
        31.0,
        131.1,
    )
    assert result.eoc_point is not None
    assert result.eoc_point.label == "C / EOC"
    assert result.eoc_point.source_name == "C"
    assert (result.eoc_point.latitude_deg, result.eoc_point.longitude_deg) == (
        31.1,
        131.2,
    )
    assert all(segment.distance_nm > 0 for segment in result.segments)



def test_descent_end_at_vrep_keeps_physical_point_name() -> None:
    result = split_route_into_phase_segments(
        _legs(),
        eoc_distance_nm=25.0,
        descent_end_distance_nm=30.0,
        visual_leg_source_id="leg-c",
    )

    assert result.descent_end_point is not None
    assert result.descent_end_point.label == "C"
    assert result.descent_end_point.source_name == "C"
    assert (
        result.descent_end_point.latitude_deg,
        result.descent_end_point.longitude_deg,
    ) == (31.1, 131.2)
    assert result.segments[-1].start.label == "C"
    assert (
        result.segments[-1].start.latitude_deg,
        result.segments[-1].start.longitude_deg,
    ) == (31.1, 131.2)


def test_coincident_rca_and_eoc_preserve_both_markers() -> None:
    result = split_route_into_phase_segments(
        _legs(),
        rca_distance_nm=10.0,
        eoc_distance_nm=10.0,
        descent_end_distance_nm=20.0,
    )

    assert result.rca_point is result.eoc_point
    assert result.rca_point is not None
    assert result.rca_point.label == "B / RCA / EOC"
    assert result.rca_point.markers == ("RCA", "EOC")


@pytest.mark.parametrize(
    ("eoc_distance", "expected_distance", "expected_label"),
    [
        (29.51, 30.0, "C / EOC"),
        (29.50, 29.50, "EOC"),
    ],
)
def test_eoc_snap_threshold_is_strictly_less_than_half_nm(
    eoc_distance: float,
    expected_distance: float,
    expected_label: str,
) -> None:
    result = split_route_into_phase_segments(
        _legs(),
        eoc_distance_nm=eoc_distance,
        descent_end_distance_nm=50.0,
    )

    assert result.eoc_point is not None
    assert result.eoc_point.along_route_distance_nm == pytest.approx(expected_distance)
    assert result.eoc_point.label == expected_label


def test_closed_physical_route_is_accepted_and_output_remains_closed() -> None:
    legs = [
        PhysicalRouteLeg(
            source_index=0,
            source_id="outbound",
            start_name="A",
            start_latitude_deg=31.0,
            start_longitude_deg=131.0,
            end_name="B",
            end_latitude_deg=31.1,
            end_longitude_deg=131.1,
            adopted_distance_nm=10.0,
        ),
        PhysicalRouteLeg(
            source_index=1,
            source_id="return",
            start_name="B",
            start_latitude_deg=31.1,
            start_longitude_deg=131.1,
            end_name="A",
            end_latitude_deg=31.0,
            end_longitude_deg=131.0,
            adopted_distance_nm=10.0,
        ),
    ]

    result = split_route_into_phase_segments(legs)

    first = result.segments[0].start
    last = result.segments[-1].end
    assert (first.latitude_deg, first.longitude_deg) == (
        last.latitude_deg,
        last.longitude_deg,
    )
    assert sum(segment.distance_nm for segment in result.segments) == pytest.approx(20.0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"rca_distance_nm": -0.1}, "RCA distance"),
        ({"rca_distance_nm": 60.1}, "RCA distance"),
        ({"eoc_distance_nm": 20.0}, "supplied together"),
        ({"descent_end_distance_nm": 20.0}, "supplied together"),
        (
            {"eoc_distance_nm": 20.0, "descent_end_distance_nm": 20.0},
            "must be less",
        ),
        (
            {
                "rca_distance_nm": 30.0,
                "eoc_distance_nm": 20.0,
                "descent_end_distance_nm": 40.0,
            },
            "overlap",
        ),
        ({"visual_leg_source_id": "missing"}, "does not exist"),
        ({"visual_leg_source_id": "leg-b"}, "final physical leg"),
        (
            {
                "rca_distance_nm": 60.0,
                "visual_leg_source_id": "leg-c",
            },
            "no distance available",
        ),
    ],
)
def test_invalid_boundaries_and_visual_assignment_are_rejected(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(PhaseSegmentationError, match=message):
        split_route_into_phase_segments(_legs(), **kwargs)  # type: ignore[arg-type]


def test_disconnected_physical_legs_are_rejected() -> None:
    legs = _legs()
    legs[1] = PhysicalRouteLeg(
        source_index=20,
        source_id="leg-b",
        start_name="B shifted",
        start_latitude_deg=31.01,
        start_longitude_deg=131.1,
        end_name="C",
        end_latitude_deg=31.1,
        end_longitude_deg=131.2,
        adopted_distance_nm=20.0,
    )

    with pytest.raises(PhaseSegmentationError, match="coordinate-contiguous"):
        split_route_into_phase_segments(legs)


def test_duplicate_source_identity_is_rejected() -> None:
    legs = _legs()
    legs[1] = PhysicalRouteLeg(
        source_index=legs[0].source_index,
        source_id=legs[0].source_id,
        start_name=legs[1].start_name,
        start_latitude_deg=legs[1].start_latitude_deg,
        start_longitude_deg=legs[1].start_longitude_deg,
        end_name=legs[1].end_name,
        end_latitude_deg=legs[1].end_latitude_deg,
        end_longitude_deg=legs[1].end_longitude_deg,
        adopted_distance_nm=legs[1].adopted_distance_nm,
    )

    with pytest.raises(PhaseSegmentationError, match="source indices must be unique"):
        split_route_into_phase_segments(legs)
