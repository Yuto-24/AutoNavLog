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


def test_explicit_omaru_point_consumes_the_matching_positional_line_label_once() -> None:
    application = _application()
    pack = application.rjfm_reference_pack
    umk = pack.points["UMK"].position
    omaru = pack.points["OMARU"].position
    entries = [
        _entry("RJFM", 31.877, 131.449, "REFERENCE", RouteNodeNameSource.GENERATED),
        _entry("小丸", umk.latitude_deg, umk.longitude_deg, "KML/KMZ LineString name"),
        _entry("日振島", omaru.latitude_deg, omaru.longitude_deg, "KML/KMZ LineString name"),
        _entry("祝島", 33.18, 132.29, "KML/KMZ LineString name"),
        _entry("NEXT", 33.30, 132.10, "KML/KMZ LineString name"),
        _entry("RJFO", 33.479, 131.737, "REFERENCE", RouteNodeNameSource.GENERATED),
    ]

    reconciled = application._reserve_rjfm_northbound_name_slots(
        entries, "RJFM", explicit_point_names={2: "小丸"}
    )

    assert [entry[0] for entry in reconciled] == ["RJFM", "UMK", "小丸", "日振島", "祝島", "RJFO"]
    assert reconciled[2][3:] == ("KML/KMZ Point", RouteNodeNameSource.IMPORTED)
    assert [(entry[1], entry[2]) for entry in reconciled] == [
        (entry[1], entry[2]) for entry in entries
    ]


def test_inbound_explicit_omaru_point_preserves_downstream_line_name() -> None:
    application = _application()
    omaru = application.rjfm_reference_pack.points["OMARU"].position
    entries = [
        _entry("RJFO", 33.479, 131.737, "REFERENCE", RouteNodeNameSource.GENERATED),
        _entry("ordinary", 33.18, 132.29, "KML/KMZ LineString name"),
        _entry("OMARU", omaru.latitude_deg, omaru.longitude_deg, "KML/KMZ LineString name"),
        _entry("DOWNSTREAM-B", 32.4, 131.6, "KML/KMZ LineString name"),
        _entry("RJFM", 31.877, 131.449, "REFERENCE", RouteNodeNameSource.GENERATED),
    ]

    reconciled = application._preserve_rjfm_inbound_point_slots(entries, "RJFM", {2: "小丸"})

    assert [entry[0] for entry in reconciled] == [
        "RJFO",
        "ordinary",
        "小丸",
        "DOWNSTREAM-B",
        "RJFM",
    ]
    assert reconciled[2][3:] == ("KML/KMZ Point", RouteNodeNameSource.IMPORTED)


def test_inbound_explicit_small_circle_keeps_omaru_coordinate_identity() -> None:
    application = _application()
    omaru = application.rjfm_reference_pack.points["OMARU"].position
    entries = [
        _entry("RJFO", 33.479, 131.737, "REFERENCE", RouteNodeNameSource.GENERATED),
        _entry("小丸", omaru.latitude_deg, omaru.longitude_deg, "KML/KMZ Point"),
        _entry("RJFM", 31.877, 131.449, "REFERENCE", RouteNodeNameSource.GENERATED),
    ]
    preserved = application._preserve_rjfm_inbound_point_slots(entries, "RJFM", {1: "小丸"})
    assert preserved[1][0] == "小丸"
    assert preserved[1][4] is RouteNodeNameSource.IMPORTED
    assert (preserved[1][1], preserved[1][2]) == (omaru.latitude_deg, omaru.longitude_deg)


def test_explicit_point_map_keeps_only_same_candidate_container() -> None:
    from autonavlog.importers.kml import import_kml_text
    from autonavlog.web.models import ConfirmRouteRequest

    kml = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
    <Folder><name>route</name><Placemark><name>RJFM→OMARU</name><LineString><coordinates>
    131.449,31.877 131.47036916946163,32.16255070087476 131.737,33.479
    </coordinates></LineString></Placemark></Folder>
    <Folder><name>other</name><Placemark><name>小丸</name><Point><coordinates>131.47036916946163,32.16255070087476</coordinates></Point></Placemark></Folder>
    </Document></kml>"""
    application = _application()
    result = import_kml_text(kml)
    request = ConfirmRouteRequest(
        candidate_kind="line",
        candidate_index=0,
        route_use_confirmed=True,
        flight_date="2099-08-10",
        departure_time_jst="09:00",
        total_usable_fuel_gal=90,
        default_variation_deg_east=8,
        all_leg_altitude_ft_msl=3500,
        defaults_confirmed=True,
    )
    entries = application._entries_from_candidate(result, request)
    assert application._explicit_point_names_for_candidate(result, request, entries) == {}


def test_deduplicated_coordinate_before_omaru_keeps_explicit_point_index_bound() -> None:
    application = _application()
    umk = application.rjfm_reference_pack.points["UMK"].position
    omaru = application.rjfm_reference_pack.points["OMARU"].position
    selected_entries = [
        _entry("RJFM", 31.877, 131.449, "REFERENCE", RouteNodeNameSource.GENERATED),
        _entry("UMK", umk.latitude_deg, umk.longitude_deg, "KML/KMZ LineString name"),
        _entry("duplicate", umk.latitude_deg, umk.longitude_deg, "KML/KMZ LineString"),
        _entry("OMARU", omaru.latitude_deg, omaru.longitude_deg, "KML/KMZ LineString name"),
        _entry("RJFO", 33.479, 131.737, "REFERENCE", RouteNodeNameSource.GENERATED),
    ]
    # Endpoint/route alignment collapses the adjacent duplicate before explicit Point indexing.
    aligned = [selected_entries[0], selected_entries[1], selected_entries[3], selected_entries[4]]
    reconciled = application._reserve_rjfm_northbound_name_slots(
        aligned, "RJFM", explicit_point_names={2: "小丸"}
    )
    assert [entry[0] for entry in reconciled] == ["RJFM", "UMK", "小丸", "RJFO"]
    assert (reconciled[2][1], reconciled[2][2]) == (omaru.latitude_deg, omaru.longitude_deg)
    assert reconciled[2][4] is RouteNodeNameSource.IMPORTED
