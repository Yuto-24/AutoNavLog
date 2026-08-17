from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from autonavlog.storage.rjfm_reference import RjfmReferenceDataError, RjfmReferencePack

PACK_ROOT = Path(__file__).resolve().parents[2] / "data" / "reference" / "rjfm"


def _copied_pack(tmp_path: Path) -> Path:
    destination = tmp_path / "rjfm"
    shutil.copytree(PACK_ROOT, destination)
    return destination


def _rewrite_payload(root: Path, payload: dict[str, object]) -> None:
    payload_path = root / "rjfm-reference.json"
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["payload"]["sha256"] = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_bundled_rjfm_pack_loads_source_backed_values() -> None:
    pack = RjfmReferencePack.from_directory(PACK_ROOT)

    assert pack.revision == "2026-08-17-rjfm-umk-guidance-v3"
    assert pack.content_fingerprint == hashlib.sha256(
        (PACK_ROOT / "rjfm-reference.json").read_bytes()
    ).hexdigest()
    assert pack.airport.icao == "RJFM"
    assert pack.airport.elevation_ft_msl == 19.0
    assert pack.airport.traffic_pattern_altitude_ft_msl == 1000.0

    runway_09 = pack.runway.ends["09"]
    runway_27 = pack.runway.ends["27"]
    assert runway_09.threshold.latitude_deg == pytest.approx(31.876183333333334)
    assert runway_09.threshold.longitude_deg == pytest.approx(131.43528333333333)
    assert runway_27.threshold.latitude_deg == pytest.approx(31.878072222222222)
    assert runway_27.threshold.longitude_deg == pytest.approx(131.4616111111111)
    assert pack.runway.center.latitude_deg == pytest.approx(31.877128459304384)
    assert pack.runway.center.longitude_deg == pytest.approx(131.44844708793116)

    assert pack.mze.position.latitude_deg == pytest.approx(31.87872777777778)
    assert pack.mze.position.longitude_deg == pytest.approx(131.43746666666667)
    assert pack.mze.elevation_ft_msl == 54.0
    assert pack.mze.station_declination_deg == -6.0
    assert pack.mze.station_declination_epoch == 2013


def test_bundled_map_digitization_is_explicitly_unverified_and_within_fit_gate() -> None:
    pack = RjfmReferencePack.from_directory(PACK_ROOT)
    georeference = pack.data.georeferencing

    assert len(georeference.control_points) == 6
    assert georeference.rms_residual_nm == pytest.approx(0.032939048004967386)
    assert georeference.max_residual_nm == pytest.approx(0.04233510835434205)
    assert georeference.max_residual_nm <= 0.5
    assert set(pack.points) == {"UMK", "OVER_FIELD", "OMARU"}
    assert all(
        point.validation_status == "UNVERIFIED_MAP_DIGITIZATION"
        for point in pack.points.values()
    )
    assert all(point.estimated_error_nm == 0.35 for point in pack.points.values())
    assert pack.points["UMK"].position.latitude_deg == pytest.approx(31.985137767624444)
    assert pack.points["OVER_FIELD"].position.latitude_deg == pytest.approx(
        32.08521636344042
    )
    assert pack.points["OMARU"].position.latitude_deg == pytest.approx(32.16255070087476)


def test_bundled_pca_and_departure_policy_keep_source_and_operational_values_separate() -> None:
    pack = RjfmReferencePack.from_directory(PACK_ROOT)

    assert len(pack.pca.polygon_vertices) == 4
    assert pack.pca.exclusion_radius_km == 9.0
    assert pack.pca.source_altitude_lower_m == 200.0
    assert pack.pca.source_altitude_upper_m == 800.0
    assert pack.pca.operational_altitude_lower_ft_msl == 656.0
    assert pack.pca.operational_altitude_upper_ft_msl == 2700.0
    assert pack.pca.altitude_bounds_inclusive is True
    assert (
        pack.pca.operational_altitude_policy_status
        == "USER_APPROVED_NOT_EXACT_METRIC_CONVERSION"
    )

    assert pack.policy.target_altitude_ft_msl == 5500.0
    assert pack.policy.center_route_sequence == ["UMK", "OVER_FIELD", "OMARU"]
    assert pack.policy.trigger_radius_nm == 1.0
    assert pack.policy.turn_bank_angle_deg == 20.0
    assert pack.policy.minimum_turn_entry_dme_nm == 4.0
    assert pack.policy.minimum_turn_entry_dme_inclusive is True
    assert pack.policy.runways["09"].initial_straight_until_altitude_ft_msl == 1000.0
    assert pack.policy.runways["27"].initial_straight_distance_nm == 1.5
    assert pack.policy.runways["09"].extension_turn_direction == "LEFT"
    assert pack.policy.runways["27"].extension_turn_direction == "RIGHT"
    assert pack.policy.dme_constraint_scope == "FINAL_EXTENSION_TURN_ENTRY_POINT"
    turn_source = next(
        source
        for source in pack.data.sources
        if source.id == "user-approved-rjfm-rwy-turn-policy-2026-08-17"
    )
    assert turn_source.distribution == "USER_DECISION"
    assert "attached training document" in turn_source.notes


def test_bundled_live_airspace_reference_is_display_only_and_not_fingerprinted() -> None:
    pack = RjfmReferencePack.from_directory(PACK_ROOT)
    reference = pack.civil_training_test_airspace

    assert reference.data_use == "DISPLAY_ONLY_LIVE_REFERENCE"
    assert (
        reference.content_fingerprint_scope
        == "CONFIGURATION_ONLY_LIVE_GEOJSON_EXCLUDED"
    )
    assert reference.source_page_url == (
        "https://www.mlit.go.jp/koku/koku_tk10_000004.html"
    )
    assert reference.layer_metadata_url == (
        "https://maps.gsi.go.jp/development/ichiran.html"
        "#kokuarea_minkankunren"
    )
    assert reference.tile_url_template == (
        "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/"
        "{z}/{x}/{y}.geojson"
    )
    assert {
        (tile.zoom, tile.x, tile.y): tile.expected_polygon_names
        for tile in reference.tiles
    } == {
        (8, 221, 103): ["KS4-1/4", "KS4-1", "KS4-3", "KS4-5"],
        (8, 221, 104): [
            "KS4-2",
            "KS4-7",
            "KS4-6",
            "KS4-1/4",
            "KS4-1",
            "KS4-3",
            "KS4-5",
            "KS4-8",
        ],
    }
    assert reference.feature_name_prefix == "KS4-"
    assert "NAV LOG計算やPCA判定には使用しません" in reference.caution_jp


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data_use", "CALCULATION_INPUT"),
        (
            "content_fingerprint_scope",
            "LIVE_GEOJSON_INCLUDED",
        ),
        ("tile_url_template", "https://example.invalid/{z}/{x}/{y}.geojson"),
        ("source_ids", ["gsi-civil-training-test-airspace-geojson-2026-08-17"]),
        ("caution_jp", "表示できます。"),
    ],
)
def test_loader_rejects_unsafe_live_airspace_contract(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["civil_training_test_airspace"][field] = value
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


@pytest.mark.parametrize(
    "expected_polygon_names",
    [
        ["KS4-1/4", "KS4-1", "KS4-3"],
        ["KS4-1/4", "KS4-1", "KS4-3", "KS4-5", "KS4-5"],
        ["KS4-1/4", "KS4-1", "KS4-3", "KS4-5", "KS4-8"],
    ],
    ids=["missing", "duplicate", "additional"],
)
def test_loader_rejects_drifted_expected_polygon_name_contract(
    tmp_path: Path,
    expected_polygon_names: list[str],
) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["civil_training_test_airspace"]["tiles"][0][
        "expected_polygon_names"
    ] = expected_polygon_names
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


def test_loader_rejects_payload_hash_mismatch(tmp_path: Path) -> None:
    root = _copied_pack(tmp_path)
    payload_path = root / "rjfm-reference.json"
    payload_path.write_bytes(payload_path.read_bytes() + b"\n")

    with pytest.raises(RjfmReferenceDataError, match="SHA-256 mismatch"):
        RjfmReferencePack.from_directory(root)


def test_loader_rejects_payload_path_traversal(tmp_path: Path) -> None:
    root = _copied_pack(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["payload"]["path"] = "../rjfm-reference.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RjfmReferenceDataError, match="unsafe RJFM payload path"):
        RjfmReferencePack.from_directory(root)


def test_loader_recomputes_and_rejects_false_georeference_residuals(tmp_path: Path) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["georeferencing"]["control_points"][0]["residual_nm"] = 0.0
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


def test_loader_never_allows_a_georeference_gate_above_half_a_nautical_mile(
    tmp_path: Path,
) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["georeferencing"]["maximum_allowed_residual_nm"] = 0.51
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


def test_loader_rejects_digitized_coordinate_not_produced_by_recorded_transform(
    tmp_path: Path,
) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["points"]["UMK"]["position"]["latitude_deg"] += 0.01
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


def test_loader_rejects_digitized_error_below_declared_target_uncertainty(
    tmp_path: Path,
) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["points"]["UMK"]["estimated_error_nm"] = 0.05
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_altitude_ft_msl", 5400.0),
        ("trigger_radius_nm", 1.1),
        ("turn_bank_angle_deg", 19.0),
        ("minimum_turn_entry_dme_nm", 3.9),
        ("target_position_tolerance_nm", 0.02),
        ("target_altitude_tolerance_ft", 20.0),
        ("tangent_course_tolerance_deg", 0.2),
    ],
)
def test_loader_rejects_policy_values_that_solver_does_not_vary(
    tmp_path: Path,
    field: str,
    value: float,
) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["policy"][field] = value
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


def test_loader_rejects_policy_course_boundary_mismatch(tmp_path: Path) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["policy"]["post_cut_straight_allowed_magnetic_courses"][0][
        "upper_deg"
    ] = 93.0
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("policy", "minimum_turn_entry_dme_inclusive", False),
        ("pca", "altitude_bounds_inclusive", False),
    ],
)
def test_loader_rejects_inclusivity_that_runtime_cannot_vary(
    tmp_path: Path,
    section: str,
    field: str,
    value: bool,
) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload[section][field] = value
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_turn_angle_deg", 90.0),
        ("post_cut_magnetic_course_deg", 48.0),
    ],
)
def test_loader_rejects_runway_turn_policy_mismatch(
    tmp_path: Path,
    field: str,
    value: float,
) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["policy"]["runways"]["09"][field] = value
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)


def test_loader_rejects_wrong_runway_extension_turn_direction(tmp_path: Path) -> None:
    root = _copied_pack(tmp_path)
    payload = json.loads((root / "rjfm-reference.json").read_text(encoding="utf-8"))
    payload["policy"]["runways"]["27"]["extension_turn_direction"] = "LEFT"
    _rewrite_payload(root, payload)

    with pytest.raises(RjfmReferenceDataError, match="invalid RJFM JSON model"):
        RjfmReferencePack.from_directory(root)
