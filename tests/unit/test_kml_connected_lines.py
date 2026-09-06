from __future__ import annotations

from pathlib import Path

import pytest

import autonavlog.importers.kml as kml_module
from autonavlog.importers.kml import (
    KmlRouteCoordinateLimitExceeded,
    import_kml_text,
    select_imported_connected_line,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "connected_oita_routes.kml"
ISSUE_129_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "issue_129_omaru_point.kml"


def test_same_container_lines_connect_in_document_order_and_adopt_best_junction_point() -> None:
    imported = import_kml_text(
        """<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
        <Folder><name>Parent</name><Folder><name>Route A</name>
        <Placemark><name>START→TURN</name><LineString><coordinates>
        131.0,31.0 131.1,31.0
        </coordinates></LineString></Placemark>
        <Placemark><name>FIRST</name><Point><coordinates>
        131.1,31.0
        </coordinates></Point></Placemark>
        <Placemark><name>MID-LINE C'K</name><Point><coordinates>
        131.05,31.0
        </coordinates></Point></Placemark>
        <Placemark><name>BEST</name><Point><coordinates>
        131.10005,31.00005
        </coordinates></Point></Placemark>
        <Placemark><name>BEST-LATER</name><Point><coordinates>
        131.10005,31.00005
        </coordinates></Point></Placemark>
        <Placemark><name>TURN→END</name><LineString><coordinates>
        131.1001,31.0001 131.2,31.1
        </coordinates></LineString></Placemark>
        </Folder></Folder></Document></kml>"""
    )

    assert len(imported.lines) == 2
    assert imported.lines[0].container_path == ("Doc", "Parent", "Route A")
    assert imported.lines[0].document_order < imported.lines[1].document_order
    assert len(imported.connected_lines) == 1
    connected = imported.connected_lines[0]
    assert connected.name == "Route A"
    assert connected.container_path == ("Doc", "Parent", "Route A")
    assert connected.segment_names == ("START→TURN", "TURN→END")
    assert connected.segment_indices == (0, 1)
    assert connected.coordinates == (
        (31.0, 131.0),
        (31.00005, 131.10005),
        (31.1, 131.2),
    )
    assert connected.waypoint_names == ("START", "BEST", "END")
    assert connected.waypoint_sources == ("line", "point", "line")
    assert "MID-LINE C'K" not in connected.waypoint_names
    assert imported.warnings == ()


def test_connected_lines_warn_when_merging_more_than_ten_meters_without_point() -> None:
    imported = import_kml_text(
        """<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
        <Folder><name>No Point</name>
        <Placemark><name>A</name><LineString><coordinates>
        131.0,31.0 131.1,31.0
        </coordinates></LineString></Placemark>
        <Placemark><name>B</name><LineString><coordinates>
        131.1,31.00015 131.2,31.1
        </coordinates></LineString></Placemark>
        </Folder></Document></kml>"""
    )

    assert len(imported.connected_lines) == 1
    assert imported.connected_lines[0].coordinates[1] == (31.0, 131.1)
    assert len(imported.warnings) == 1
    assert "without a matching Point Placemark" in imported.warnings[0]


def test_reversed_line_group_is_not_connected() -> None:
    imported = import_kml_text(
        """<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
        <Folder><name>Reversed</name>
        <Placemark><name>A→B</name><LineString><coordinates>
        131.0,31.0 131.1,31.0
        </coordinates></LineString></Placemark>
        <Placemark><name>C→B</name><LineString><coordinates>
        131.2,31.0 131.1,31.0
        </coordinates></LineString></Placemark>
        </Folder></Document></kml>"""
    )

    assert len(imported.lines) == 2
    assert imported.connected_lines == ()
    assert len(imported.warnings) == 1
    assert "kept as individual candidates" in imported.warnings[0]


def test_connectable_lines_in_the_wrong_document_order_are_not_reordered() -> None:
    imported = import_kml_text(
        """<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
        <Folder><name>Wrong order</name>
        <Placemark><name>B→C</name><LineString><coordinates>
        131.1,31.0 131.2,31.0
        </coordinates></LineString></Placemark>
        <Placemark><name>A→B</name><LineString><coordinates>
        131.0,31.0 131.1,31.0
        </coordinates></LineString></Placemark>
        </Folder></Document></kml>"""
    )

    assert [line.name for line in imported.lines] == ["B→C", "A→B"]
    assert imported.connected_lines == ()
    assert len(imported.warnings) == 1
    assert "kept as individual candidates" in imported.warnings[0]


def test_connected_group_that_collapses_after_deduplication_falls_back_to_lines() -> None:
    imported = import_kml_text(
        """<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
        <Folder><name>Degenerate</name>
        <Placemark><name>A</name><LineString><coordinates>
        131,31 131.00002,31
        </coordinates></LineString></Placemark>
        <Placemark><name>B</name><LineString><coordinates>
        131.00002,31 131.00004,31
        </coordinates></LineString></Placemark>
        </Folder></Document></kml>"""
    )

    assert len(imported.lines) == 2
    assert imported.connected_lines == ()
    assert len(imported.warnings) == 1
    assert "fewer than two distinct points" in imported.warnings[0]
    assert "kept as individual candidates" in imported.warnings[0]


def test_same_named_sibling_folders_remain_distinct_connected_candidates() -> None:
    imported = import_kml_text(
        """<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
        <Folder><name>Route</name>
        <Placemark><name>A1</name><LineString><coordinates>131,31 131.1,31</coordinates>
        </LineString></Placemark>
        <Placemark><name>A2</name><LineString><coordinates>131.1,31 131.2,31</coordinates>
        </LineString></Placemark></Folder>
        <Folder><name>Route</name>
        <Placemark><name>B1</name><LineString><coordinates>131,32 131.1,32</coordinates>
        </LineString></Placemark>
        <Placemark><name>B2</name><LineString><coordinates>131.1,32 131.2,32</coordinates>
        </LineString></Placemark></Folder>
        </Document></kml>"""
    )

    assert [item.container_path for item in imported.connected_lines] == [
        ("Doc", "Route"),
        ("Doc", "Route"),
    ]
    assert [item.segment_indices for item in imported.connected_lines] == [(0, 1), (2, 3)]
    assert [item.segment_names for item in imported.connected_lines] == [
        ("A1", "A2"),
        ("B1", "B2"),
    ]


def test_selected_connected_line_enforces_combined_500_coordinate_limit() -> None:
    first = " ".join(f"{131 + index * 0.0002},31" for index in range(300))
    join_longitude = 131 + 299 * 0.0002
    second = " ".join(
        [f"{join_longitude},31"]
        + [f"{join_longitude + index * 0.0002},31" for index in range(1, 202)]
    )
    imported = import_kml_text(
        f"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
        <Folder><name>Long</name>
        <Placemark><name>A</name><LineString><coordinates>{first}</coordinates>
        </LineString></Placemark>
        <Placemark><name>B</name><LineString><coordinates>{second}</coordinates>
        </LineString></Placemark></Folder></Document></kml>"""
    )

    assert len(imported.connected_lines[0].coordinates) == 501
    with pytest.raises(KmlRouteCoordinateLimitExceeded) as captured:
        select_imported_connected_line(imported, 0)
    assert captured.value.coordinate_count == 501
    assert captured.value.maximum == 500


def test_connected_line_deduplication_keeps_waypoint_name_and_source_aligned() -> None:
    imported = import_kml_text(
        """<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
        <Folder><name>Route</name>
        <Placemark><name>NEAR→TURN</name><LineString><coordinates>
        131,31 131.00005,31 131.1,31 131.2,31
        </coordinates></LineString></Placemark>
        <Placemark><name>JOIN→END</name><LineString><coordinates>
        131.2,31 131.3,31
        </coordinates></LineString></Placemark>
        </Folder></Document></kml>"""
    )

    selected = select_imported_connected_line(imported, 0)

    assert selected.coordinates == (
        (31.0, 131.0),
        (31.0, 131.1),
        (31.0, 131.2),
        (31.0, 131.3),
    )
    assert selected.waypoint_names == ("NEAR", "TURN", None, "END")
    assert selected.waypoint_sources == ("line", "line", None, "line")


def test_large_distant_point_set_does_not_scan_every_point_for_each_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_distance = kml_module._coordinate_separation_nm
    distance_call_count = 0

    def counted_distance(
        left: tuple[float, float],
        right: tuple[float, float],
    ) -> float:
        nonlocal distance_call_count
        distance_call_count += 1
        return original_distance(left, right)

    monkeypatch.setattr(kml_module, "_coordinate_separation_nm", counted_distance)
    distant_points = "".join(
        f"<Placemark><name>P{index}</name><Point><coordinates>"
        f"{-120 + (index % 100) * 0.001},{20 + (index // 100) * 0.001}"
        "</coordinates></Point></Placemark>"
        for index in range(5_000)
    )
    imported = import_kml_text(
        f"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
        <Folder><name>Indexed</name>{distant_points}
        <Placemark><name>A</name><LineString><coordinates>131,31 131.1,31</coordinates>
        </LineString></Placemark>
        <Placemark><name>B</name><LineString><coordinates>131.1,31 131.2,31</coordinates>
        </LineString></Placemark></Folder></Document></kml>"""
    )

    assert len(imported.points) == 5_000
    assert len(imported.connected_lines) == 1
    assert distance_call_count < 20


def test_oita_fixture_builds_four_expected_route_candidates() -> None:
    imported = import_kml_text(FIXTURE.read_text(encoding="utf-8"), filename=FIXTURE.name)

    assert len(imported.points) == 38
    assert len(imported.lines) == 23
    assert imported.warnings == ()
    assert [item.name for item in imported.connected_lines] == [
        "RJFM→RJFO①",
        "RJFM→RJFO②",
        "RJFO→RJFM①",
        "RJFO→RJFM②",
    ]
    assert [len(item.segment_names) for item in imported.connected_lines] == [7, 6, 5, 5]
    assert [len(item.coordinates) for item in imported.connected_lines] == [8, 7, 6, 6]
    assert [round(item.distance_nm, 2) for item in imported.connected_lines] == [
        124.66,
        151.42,
        112.03,
        129.01,
    ]
    assert all(
        item.container_path[:2] == ("大分経路", "RJFO")
        for item in imported.connected_lines
    )
    assert "C'K 佐田岬(TFE 125/17.6)" not in imported.connected_lines[1].waypoint_names


def test_issue_129_fixture_keeps_explicit_omaru_point_bound_to_one_coordinate() -> None:
    imported = import_kml_text(
        ISSUE_129_FIXTURE.read_text(encoding="utf-8"), filename=ISSUE_129_FIXTURE.name
    )
    assert len(imported.lines) == 3
    assert len(imported.points) == 1
    selected = select_imported_connected_line(imported, 0)
    assert selected.coordinates == (
        (31.877, 131.449),
        (31.985137767624444, 131.42429852046251),
        (32.16255070087476, 131.47036916946163),
        (33.479, 131.737),
    )
    assert selected.waypoint_names == ("RJFM", "UMK", "小丸", "RJFO")