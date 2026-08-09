from __future__ import annotations

import stat
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

import autonavlog.importers.kml as kml_module
from autonavlog.importers.kml import (
    ImportLimits,
    KmlDocumentSelectionRequired,
    KmlImportError,
    import_kml_or_kmz,
    import_kml_text,
    named_waypoints_from_line,
    select_imported_line,
    select_imported_polygon_outer,
)


def _kml(name: str, coordinates: str) -> bytes:
    return f"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
    <Placemark><name>{name}</name><LineString><coordinates>
    {coordinates}
    </coordinates></LineString></Placemark></Document></kml>""".encode()


def _kmz(entries: list[tuple[str | ZipInfo, bytes]]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


class _ArchiveWithUntrustedSize:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.member = ZipInfo("doc.kml")
        self.member.file_size = 1
        self.open_count = 0

    def infolist(self) -> list[ZipInfo]:
        return [self.member]

    def open(self, member: ZipInfo) -> BytesIO:
        assert member is self.member
        self.open_count += 1
        return BytesIO(self.payload)


def _mark_first_entry_encrypted(raw: bytes) -> bytes:
    output = bytearray(raw)
    local = output.index(b"PK\x03\x04")
    local_flags = int.from_bytes(output[local + 6 : local + 8], "little") | 1
    output[local + 6 : local + 8] = local_flags.to_bytes(2, "little")
    central = output.index(b"PK\x01\x02")
    central_flags = int.from_bytes(output[central + 8 : central + 10], "little") | 1
    output[central + 8 : central + 10] = central_flags.to_bytes(2, "little")
    return bytes(output)


@pytest.mark.parametrize(
    "text",
    [
        "<!DOCTYPE kml><kml></kml>",
        """<!DOCTYPE kml [<!ENTITY x "expanded">]>
        <kml><Document><name>&x;</name></Document></kml>""",
        """<!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]>
        <kml><Document><name>&x;</name></Document></kml>""",
    ],
)
def test_doctype_entities_and_external_references_are_explicitly_rejected(
    text: str,
) -> None:
    with pytest.raises(KmlImportError, match="DOCTYPE|unsafe"):
        import_kml_text(text)


def test_kmz_rejects_unicode_normalized_and_casefolded_duplicates() -> None:
    unicode_duplicate = _kmz(
        [
            ("caf\u00e9.kml", _kml("A", "131,31 132,32")),
            ("cafe\u0301.kml", _kml("B", "133,33 134,34")),
        ]
    )
    with pytest.raises(KmlImportError, match="duplicate normalized"):
        import_kml_or_kmz(unicode_duplicate, filename="route.kmz")

    case_duplicate = _kmz(
        [
            ("doc.kml", _kml("A", "131,31 132,32")),
            ("DOC.KML", _kml("B", "133,33 134,34")),
        ]
    )
    with pytest.raises(KmlImportError, match="duplicate normalized"):
        import_kml_or_kmz(case_duplicate, filename="route.kmz")


def test_kmz_rejects_symlink_encryption_nested_archive_and_backslash() -> None:
    symlink = ZipInfo("link.kml")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(KmlImportError, match="symbolic link"):
        import_kml_or_kmz(
            _kmz([(symlink, b"doc.kml")]),
            filename="route.kmz",
        )

    encrypted = _mark_first_entry_encrypted(_kmz([("doc.kml", _kml("A", "131,31 132,32"))]))
    with pytest.raises(KmlImportError, match="encrypted"):
        import_kml_or_kmz(encrypted, filename="route.kmz")

    with pytest.raises(KmlImportError, match="nested archive"):
        import_kml_or_kmz(
            _kmz([("payload.bin", b"PK\x03\x04payload")]),
            filename="route.kmz",
        )

    with pytest.raises(KmlImportError, match="unsafe path"):
        import_kml_or_kmz(
            _kmz([("folder\\doc.kml", _kml("A", "131,31 132,32"))]),
            filename="route.kmz",
        )


def test_kmz_expanded_limit_uses_bytes_read_not_advertised_size() -> None:
    archive = _ArchiveWithUntrustedSize(b"x" * 11)

    with pytest.raises(KmlImportError, match="expanded size"):
        kml_module._safe_kml_members(
            archive,  # type: ignore[arg-type]
            ImportLimits(max_expanded_bytes=10),
        )

    assert archive.open_count == 1


def test_path_source_is_rejected_by_stat_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "oversized.kml"
    source.write_bytes(b"xx")

    def unexpected_read(_: Path) -> bytes:
        raise AssertionError("oversized source must not be read")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)

    with pytest.raises(KmlImportError, match="archive size"):
        import_kml_or_kmz(
            source,
            limits=ImportLimits(max_archive_bytes=1),
        )


def test_path_source_os_error_is_wrapped(tmp_path: Path) -> None:
    missing = tmp_path / "missing.kml"

    with pytest.raises(KmlImportError, match="cannot inspect") as captured:
        import_kml_or_kmz(missing)

    assert isinstance(captured.value.__cause__, OSError)


def test_kmz_document_selection_rules() -> None:
    multiple = _kmz(
        [
            ("a.kml", _kml("A", "131,31 132,32")),
            ("b.kml", _kml("B", "133,33 134,34")),
        ]
    )
    with pytest.raises(KmlDocumentSelectionRequired) as captured:
        import_kml_or_kmz(multiple, filename="route.kmz")
    assert captured.value.candidates == ("a.kml", "b.kml")

    selected = import_kml_or_kmz(
        multiple,
        filename="route.kmz",
        kmz_kml_filename="b.kml",
    )
    assert selected.source_files == ("b.kml",)
    assert selected.lines[0].name == "B"

    doc_wins = import_kml_or_kmz(
        _kmz(
            [
                ("other.kml", _kml("OTHER", "131,31 132,32")),
                ("folder/doc.kml", _kml("DOC", "133,33 134,34")),
            ]
        ),
        filename="route.kmz",
    )
    assert doc_wins.source_files == ("folder/doc.kml",)
    assert doc_wins.lines[0].name == "DOC"


def test_line_keeps_full_coordinates_then_enforces_selected_limit() -> None:
    coordinates = " ".join(f"{131 + index * 0.01},{31 + index * 0.01}" for index in range(6))
    result = import_kml_or_kmz(
        _kml("LONG", coordinates),
        filename="route.kml",
        limits=ImportLimits(max_display_vertices=4),
    )

    assert len(result.lines[0].coordinates) == 6
    assert len(result.lines[0].display_coordinates) <= 4
    with pytest.raises(KmlImportError, match="selected LineString coordinate"):
        select_imported_line(
            result,
            0,
            limits=ImportLimits(max_coordinates_in_selected_line=5),
        )


def test_selected_line_merges_adjacent_points_within_ten_meters_but_keeps_loop() -> None:
    result = import_kml_or_kmz(
        _kml(
            "LOOP",
            "131,31 131.00001,31.00001 131.1,31.1 131.2,31.2 131,31",
        ),
        filename="route.kml",
    )

    selected = select_imported_line(result, 0)

    assert selected.coordinates == (
        (31.0, 131.0),
        (31.1, 131.1),
        (31.2, 131.2),
        (31.0, 131.0),
    )


def test_selected_line_rejects_fewer_than_two_points_after_deduplication() -> None:
    result = import_kml_or_kmz(
        _kml("SHORT", "131,31 131.00001,31.00001"),
        filename="route.kml",
    )
    with pytest.raises(KmlImportError, match="fewer than two"):
        select_imported_line(result, 0)


def test_polygon_keeps_full_outer_ring_then_enforces_selected_limit() -> None:
    coordinates = " ".join(
        [
            "131,31",
            "131.1,31",
            "131.2,31.1",
            "131.2,31.2",
            "131.1,31.3",
            "131,31.2",
            "131,31",
        ]
    )
    result = import_kml_text(
        f"""<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark>
        <name>AREA</name><Polygon><outerBoundaryIs><LinearRing>
        <coordinates>{coordinates}</coordinates>
        </LinearRing></outerBoundaryIs></Polygon></Placemark></kml>""",
        limits=ImportLimits(max_display_vertices=4),
    )

    assert len(result.polygons[0].outer_boundary) == 7
    assert len(result.polygons[0].display_outer_boundary) <= 4
    with pytest.raises(KmlImportError, match="Polygon outer coordinate"):
        select_imported_polygon_outer(
            result,
            0,
            limits=ImportLimits(max_coordinates_in_selected_polygon_outer=6),
        )


def test_delimited_line_name_does_not_guess_simplified_vertex_names() -> None:
    result = import_kml_or_kmz(
        _kml(
            "小丸～日振島～祝島～ゴルフコース",
            (
                "131.4488055215004,31.87716585260077,0 "
                "131.4317398539069,31.98214589070221,0 "
                "131.4734489929498,32.16275095638636,0 "
                "132.2948734240414,33.1802236311398,0 "
                "131.9894319344609,33.78695544494976,0 "
                "131.67890296839,33.62999835453385,0 "
                "131.7371811724867,33.47957070171427,0"
            ),
        ),
        filename="named-route.kml",
    )

    assert named_waypoints_from_line(select_imported_line(result, 0)) == ()


def test_delimited_line_name_maps_only_one_name_per_original_coordinate() -> None:
    result = import_kml_or_kmz(
        _kml(
            "小丸～日振島～祝島～ゴルフコース",
            (
                "131.4488055215004,31.87716585260077,0 "
                "131.4734489929498,32.16275095638636,0 "
                "131.9894319344609,33.78695544494976,0 "
                "131.7371811724867,33.47957070171427,0"
            ),
        ),
        filename="named-route.kml",
    )

    named = named_waypoints_from_line(select_imported_line(result, 0))

    assert [point.name for point in named] == [
        "小丸",
        "日振島",
        "祝島",
        "ゴルフコース",
    ]
    assert [(point.latitude_deg, point.longitude_deg) for point in named] == [
        (31.87716585260077, 131.4488055215004),
        (32.16275095638636, 131.4734489929498),
        (33.78695544494976, 131.9894319344609),
        (33.47957070171427, 131.7371811724867),
    ]


def test_ambiguous_delimited_line_name_falls_back() -> None:
    result = import_kml_or_kmz(
        _kml("A～B～C～D～E", "131,31 131.1,31.1 131.2,31.2 131.3,31.3"),
        filename="ambiguous-route.kml",
    )

    assert named_waypoints_from_line(select_imported_line(result, 0)) == ()
