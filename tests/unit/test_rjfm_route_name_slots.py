from __future__ import annotations

from pathlib import Path

from autonavlog.domain.enums import RouteNodeNameSource
from autonavlog.storage.rjfm_reference import RjfmReferencePack
from autonavlog.web.facade import AutoNavLogWebApplication, RouteEntry

ROOT = Path(__file__).resolve().parents[2]


def _application() -> AutoNavLogWebApplication:
    application = object.__new__(AutoNavLogWebApplication)
    application.rjfm_reference_pack = RjfmReferencePack.from_directory(
        ROOT / "data" / "reference" / "rjfm"
    )
    return application


def _entry(
    name: str,
    latitude: float,
    longitude: float,
    source: str,
    name_source: RouteNodeNameSource = RouteNodeNameSource.IMPORTED,
) -> RouteEntry:
    return (name, latitude, longitude, source, name_source)


def _rjfm_entries(*, names: tuple[str, ...]) -> list[RouteEntry]:
    pack = _application().rjfm_reference_pack
    umk = pack.points["UMK"].position
    omaru = pack.points["OMARU"].position
    return [
        _entry("RJFM", 31.877, 131.449, "REFERENCE", RouteNodeNameSource.GENERATED),
        _entry(names[0], umk.latitude_deg, umk.longitude_deg, "KML/KMZ LineString name"),
        _entry(names[1], omaru.latitude_deg, omaru.longitude_deg, "KML/KMZ LineString name"),
        _entry(names[2], 33.18, 132.29, "KML/KMZ LineString name"),
        _entry(names[3], 33.78, 131.99, "KML/KMZ LineString"),
        _entry("RJFO", 33.479, 131.737, "REFERENCE", RouteNodeNameSource.GENERATED),
    ]


def test_rjfm_slots_take_physical_names_and_shift_ordinary_line_names() -> None:
    entries = _rjfm_entries(names=("小丸", "日振島", "祝島", "WP5"))

    reconciled = _application()._reserve_rjfm_northbound_name_slots(entries, "RJFM")

    assert [entry[0] for entry in reconciled] == [
        "RJFM",
        "UMK",
        "OMARU",
        "小丸",
        "日振島",
        "RJFO",
    ]
    assert reconciled[3][3] == "KML/KMZ LineString name"
    assert reconciled[4][3] == "KML/KMZ LineString name"
    assert [entry[4] for entry in reconciled[1:5]] == [
        RouteNodeNameSource.GENERATED,
        RouteNodeNameSource.GENERATED,
        RouteNodeNameSource.IMPORTED,
        RouteNodeNameSource.IMPORTED,
    ]


def test_rjfm_slot_reservation_respects_matching_explicit_names_and_corrects_conflicts() -> None:
    explicit = _rjfm_entries(names=("UMK", "OMARU", "祝島", "WP5"))
    conflict = _rjfm_entries(names=("OMARU", "UMK", "祝島", "WP5"))

    explicit_result = _application()._reserve_rjfm_northbound_name_slots(explicit, "RJFM")
    conflict_result = _application()._reserve_rjfm_northbound_name_slots(conflict, "RJFM")

    assert [entry[0] for entry in explicit_result][1:3] == ["UMK", "OMARU"]
    assert [entry[4] for entry in explicit_result][1:3] == [
        RouteNodeNameSource.IMPORTED,
        RouteNodeNameSource.IMPORTED,
    ]
    assert [entry[0] for entry in conflict_result][1:3] == ["UMK", "OMARU"]
    assert [entry[4] for entry in conflict_result][1:3] == [
        RouteNodeNameSource.GENERATED,
        RouteNodeNameSource.GENERATED,
    ]


def test_omaru_first_reserves_no_physical_umk_and_non_rjfm_is_unchanged() -> None:
    application = _application()
    pack = application.rjfm_reference_pack
    omaru = pack.points["OMARU"].position
    omaru_first = [
        _entry("RJFM", 31.877, 131.449, "REFERENCE", RouteNodeNameSource.GENERATED),
        _entry("ordinary", omaru.latitude_deg, omaru.longitude_deg, "KML/KMZ LineString name"),
        _entry("NEXT", 33.18, 132.29, "KML/KMZ LineString name"),
        _entry("RJFO", 33.479, 131.737, "REFERENCE", RouteNodeNameSource.GENERATED),
    ]
    other = list(omaru_first)

    reconciled = application._reserve_rjfm_northbound_name_slots(omaru_first, "RJFM")
    unchanged = application._reserve_rjfm_northbound_name_slots(other, "RJFK")

    assert [entry[0] for entry in reconciled] == ["RJFM", "OMARU", "ordinary", "RJFO"]
    assert all(entry[0] != "UMK" for entry in reconciled[1:])
    assert unchanged is other
    assert unchanged == other


def test_rjfm_slot_reservation_never_inserts_coordinates_when_names_are_short() -> None:
    application = _application()
    pack = application.rjfm_reference_pack
    umk = pack.points["UMK"].position
    omaru = pack.points["OMARU"].position
    entries = [
        _entry("RJFM", 31.877, 131.449, "REFERENCE", RouteNodeNameSource.GENERATED),
        _entry("first", umk.latitude_deg, umk.longitude_deg, "KML/KMZ LineString name"),
        _entry("second", omaru.latitude_deg, omaru.longitude_deg, "KML/KMZ LineString name"),
        _entry("RJFO", 33.479, 131.737, "REFERENCE", RouteNodeNameSource.GENERATED),
    ]

    reconciled = application._reserve_rjfm_northbound_name_slots(entries, "RJFM")

    assert len(reconciled) == len(entries)
    assert [entry[0] for entry in reconciled] == ["RJFM", "UMK", "OMARU", "RJFO"]
    assert [(entry[1], entry[2]) for entry in reconciled] == [
        (entry[1], entry[2]) for entry in entries
    ]


def test_rjfm_slot_reservation_keeps_ordinary_names_in_order_when_names_are_long() -> None:
    application = _application()
    pack = application.rjfm_reference_pack
    umk = pack.points["UMK"].position
    omaru = pack.points["OMARU"].position
    entries = [
        _entry("RJFM", 31.877, 131.449, "REFERENCE", RouteNodeNameSource.GENERATED),
        _entry("one", umk.latitude_deg, umk.longitude_deg, "KML/KMZ LineString name"),
        _entry("two", omaru.latitude_deg, omaru.longitude_deg, "KML/KMZ LineString name"),
        _entry("three", 32.5, 131.7, "KML/KMZ LineString name"),
        _entry("four", 32.8, 131.8, "KML/KMZ LineString"),
        _entry("five", 33.1, 131.9, "KML/KMZ LineString"),
        _entry("RJFO", 33.479, 131.737, "REFERENCE", RouteNodeNameSource.GENERATED),
    ]

    reconciled = application._reserve_rjfm_northbound_name_slots(entries, "RJFM")

    assert [entry[0] for entry in reconciled] == [
        "RJFM",
        "UMK",
        "OMARU",
        "one",
        "two",
        "three",
        "RJFO",
    ]
    assert all(entry[4] is RouteNodeNameSource.IMPORTED for entry in reconciled[3:6])
