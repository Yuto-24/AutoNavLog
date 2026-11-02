"""Draft candidates retain source geometry separately from bounded map previews."""

from types import SimpleNamespace

import pytest

from autonavlog.importers.kml import (
    ImportedConnectedLine,
    ImportedLine,
    ImportedPoint,
    ImportedPolygon,
    KmlImportResult,
)
from autonavlog.web.facade import AutoNavLogWebApplication


@pytest.mark.parametrize("count", [4, 500, 501, 2100])
@pytest.mark.parametrize("kind", ["line", "connected_lines", "polygon", "points"])
def test_candidate_payload_keeps_original_coordinates_within_map_limit(kind, count):
    coordinates = tuple((32 + i * 0.001, 131.0) for i in range(count))
    preview = (coordinates[0], coordinates[-1])
    if kind == "line":
        result = KmlImportResult(lines=(ImportedLine("line", coordinates, preview),))
    elif kind == "connected_lines":
        result = KmlImportResult(connected_lines=(ImportedConnectedLine(
            name="joined", container_path=(), segment_names=("a", "b"),
            segment_indices=(0, 1), coordinates=coordinates,
            waypoint_names=(None,) * count, waypoint_sources=(None,) * count,
            distance_nm=1, max_join_gap_nm=0, display_coordinates=preview,
        ),))
    elif kind == "polygon":
        coordinates = (*coordinates[:-1], coordinates[0])
        result = KmlImportResult(polygons=(ImportedPolygon(
            "polygon", coordinates, display_outer_boundary=preview,
        ),))
    else:
        result = KmlImportResult(points=tuple(
            ImportedPoint(str(i), lat, lon) for i, (lat, lon) in enumerate(coordinates)
        ))
    facade = SimpleNamespace(_nearest_airport_within_5_nm=lambda _: None)
    candidate = AutoNavLogWebApplication._candidate_payload(facade, result)["candidates"][0]
    assert candidate["vertexCount"] == count
    assert candidate["routeCoordinates"] == (
        [list(item) for item in coordinates] if count <= 500 else None
    )
    if kind == "line":
        assert candidate["coordinates"] == [list(item) for item in preview]
        assert len(candidate["coordinates"]) < count
