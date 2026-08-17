from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autonavlog.application.rjfm_departure_plan import (
    RjfmPlanReferences,
    apply_rjfm_departure_exception,
)
from autonavlog.domain.enums import FlightPhase
from autonavlog.domain.planning import RjfmCoordinate
from autonavlog.storage.rjfm_reference import RjfmReferencePack
from autonavlog.web.facade import AutoNavLogWebApplication

PACK_ROOT = Path(__file__).resolve().parents[2] / "data" / "reference" / "rjfm"


def _coordinate(latitude: float, longitude: float, source: str) -> RjfmCoordinate:
    return RjfmCoordinate(
        latitude_deg=latitude,
        longitude_deg=longitude,
        source=source,
        estimated_error_nm=0.2,
    )


def _rjfm_references(project, *, omaru_first: bool = False) -> RjfmPlanReferences:
    first = project.ordered_nodes()[1]
    return RjfmPlanReferences(
        revision="fixture-rjfm-input-v2",
        content_fingerprint="d" * 64,
        umk=(
            _coordinate(32.0, 131.50, "fixture:UMK")
            if omaru_first
            else _coordinate(first.latitude_deg, first.longitude_deg, "fixture:UMK")
        ),
        over_field=_coordinate(32.1, 131.53, "fixture:OVER_FIELD"),
        omaru=(
            _coordinate(first.latitude_deg, first.longitude_deg, "fixture:OMARU")
            if omaru_first
            else _coordinate(32.6, 131.62, "fixture:OMARU")
        ),
    )


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        (".", "route"),
        ("..", "route"),
        ("route. ", "route"),
        ("a" * 59 + ".x", "a" * 59),
        ("valid-name", "valid-name"),
    ],
)
def test_project_name_normalization_never_leaves_a_reserved_dot_suffix(
    raw_name: str,
    expected: str,
) -> None:
    assert AutoNavLogWebApplication._normalize_project_name(raw_name) == expected


def test_endpoint_airport_matching_uses_5_nm_boundary_and_deterministic_tie_break() -> None:
    airports = {
        "Z": SimpleNamespace(id="Z", icao="RJZZ", distance=5.0),
        "B": SimpleNamespace(id="B", icao="RJAA", distance=5.0),
        "A": SimpleNamespace(id="A", icao="RJAA", distance=5.0),
    }
    app = AutoNavLogWebApplication.__new__(AutoNavLogWebApplication)
    app.reference_catalog = SimpleNamespace(airports=airports)
    app._distance_to_airport = lambda coordinate, airport: airport.distance

    matched = app._nearest_airport_within_5_nm((0.0, 0.0))
    assert matched is not None
    assert matched[0].id == "A"
    assert matched[1] == 5.0

    for airport in airports.values():
        airport.distance = 5.000001
    assert app._nearest_airport_within_5_nm((0.0, 0.0)) is None


@pytest.mark.parametrize(
    "phase",
    [FlightPhase.CLIMB, FlightPhase.CRUISE, FlightPhase.DESCENT],
)
def test_cruising_altitude_guidance_applies_to_climb_cruise_and_descent(
    project,
    phase: FlightPhase,
) -> None:
    section = project.sections[0]
    section.phase = phase

    guidance = next(
        item
        for item in AutoNavLogWebApplication._section_guidance(project)
        if item["sectionId"] == str(section.id)
    )
    section.planned_altitude_ft_msl = guidance["candidateAltitudesFtMsl"][0]
    matching_guidance = next(
        item
        for item in AutoNavLogWebApplication._section_guidance(project)
        if item["sectionId"] == str(section.id)
    )

    assert guidance["appliesToCruisingAltitudeInput"] is True
    assert guidance["appliesToCruise"] is (phase == FlightPhase.CRUISE)
    assert matching_guidance["requiresReview"] is False

    section.planned_altitude_ft_msl = 3_000
    non_matching_guidance = next(
        item
        for item in AutoNavLogWebApplication._section_guidance(project)
        if item["sectionId"] == str(section.id)
    )
    assert non_matching_guidance["requiresReview"] is True


def test_cruising_altitude_guidance_does_not_apply_to_visual_arrival(project) -> None:
    section = project.sections[0]
    section.phase = FlightPhase.VISUAL_ARRIVAL
    section.planned_altitude_ft_msl = 3_000

    guidance = next(
        item
        for item in AutoNavLogWebApplication._section_guidance(project)
        if item["sectionId"] == str(section.id)
    )

    assert guidance["appliesToCruisingAltitudeInput"] is False
    assert guidance["appliesToCruise"] is False
    assert guidance["requiresReview"] is False


def test_rjfm_physical_umk_sections_expose_fixed_input_modes(project) -> None:
    assert apply_rjfm_departure_exception(project, _rjfm_references(project)) is not None

    guidance = AutoNavLogWebApplication._section_guidance(project)

    assert [item["inputMode"] for item in guidance[:2]] == [
        "RJFM_DEPARTURE_TO_UMK_FIXED",
        "RJFM_UMK_TO_OMARU_FIXED",
    ]
    assert [item["fixedAltitudeFtMsl"] for item in guidance[:2]] == [5500.0, 5500.0]
    assert all(not item["appliesToCruisingAltitudeInput"] for item in guidance[:2])
    assert guidance[2]["inputMode"] == "EDITABLE"
    assert guidance[2]["fixedAltitudeFtMsl"] is None


def test_rjfm_omaru_first_parent_exposes_one_fixed_input_mode(project) -> None:
    first = project.ordered_nodes()[1]
    first.latitude_deg = 32.6
    first.longitude_deg = 131.62
    assert (
        apply_rjfm_departure_exception(
            project,
            _rjfm_references(project, omaru_first=True),
        )
        is not None
    )

    guidance = AutoNavLogWebApplication._section_guidance(project)

    assert guidance[0]["inputMode"] == "RJFM_PARENT_CONTAINS_UMK_FIXED"
    assert guidance[0]["fixedAltitudeFtMsl"] == 5500.0
    assert guidance[1]["inputMode"] == "EDITABLE"


def test_rjfm_map_reference_exposes_source_geometry_and_live_display_layer(
    project,
) -> None:
    pack = RjfmReferencePack.from_directory(PACK_ROOT)

    reference = AutoNavLogWebApplication._rjfm_map_reference(project, pack)

    assert reference is not None
    assert reference["revision"] == pack.revision
    assert reference["contentFingerprint"] == pack.content_fingerprint
    assert reference["pca"]["polygonVertices"] == [
        {
            "latitudeDeg": point.latitude_deg,
            "longitudeDeg": point.longitude_deg,
        }
        for point in pack.pca.polygon_vertices
    ]
    assert reference["pca"]["exclusionCenter"] == {
        "latitudeDeg": pack.pca.exclusion_center.latitude_deg,
        "longitudeDeg": pack.pca.exclusion_center.longitude_deg,
    }
    assert reference["pca"]["exclusionRadiusKm"] == 9.0
    assert reference["pca"]["sourceAltitudeLowerM"] == 200.0
    assert reference["pca"]["sourceAltitudeUpperM"] == 800.0
    assert reference["pca"]["operationalAltitudeLowerFtMsl"] == 656.0
    assert reference["pca"]["operationalAltitudeUpperFtMsl"] == 2700.0
    assert reference["pca"]["operationalAltitudePolicyStatus"] == (
        "USER_APPROVED_NOT_EXACT_METRIC_CONVERSION"
    )
    civil_airspace = reference["civilTrainingTestAirspace"]
    assert civil_airspace["availability"] == "REMOTE_GSI_GEOJSON"
    assert civil_airspace["dataUse"] == "DISPLAY_ONLY_LIVE_REFERENCE"
    assert civil_airspace["contentFingerprintScope"] == (
        "CONFIGURATION_ONLY_LIVE_GEOJSON_EXCLUDED"
    )
    assert civil_airspace["sourcePageUrl"] == (
        "https://www.mlit.go.jp/koku/koku_tk10_000004.html"
    )
    assert civil_airspace["layerMetadataUrl"] == (
        "https://maps.gsi.go.jp/development/ichiran.html"
        "#kokuarea_minkankunren"
    )
    assert civil_airspace["tileUrls"] == [
        "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/103.geojson",
        "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/104.geojson",
    ]
    assert civil_airspace["tiles"] == [
        {
            "url": (
                "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/"
                "8/221/103.geojson"
            ),
            "expectedPolygonNames": ["KS4-1/4", "KS4-1", "KS4-3", "KS4-5"],
        },
        {
            "url": (
                "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/"
                "8/221/104.geojson"
            ),
            "expectedPolygonNames": [
                "KS4-2",
                "KS4-7",
                "KS4-6",
                "KS4-1/4",
                "KS4-1",
                "KS4-3",
                "KS4-5",
                "KS4-8",
            ],
        },
    ]
    assert civil_airspace["checkedAtUtc"] == "2026-08-17T04:16:51Z"
    assert "NAV LOG計算やPCA判定には使用しません" in civil_airspace["caution"]

    project.departure_airport_id = "RJFO"
    assert AutoNavLogWebApplication._rjfm_map_reference(project, pack) is None
