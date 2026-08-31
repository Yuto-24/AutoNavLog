import hashlib
import json
from pathlib import Path

import pytest

from autonavlog.storage.rjfm_inbound_reference import (
    RjfmInboundGuidanceReference,
    RjfmInboundReferenceError,
)

ROOT = Path(__file__).parents[2]


def _point(latitude: float, longitude: float) -> dict[str, float]:
    return {"latitude_deg": latitude, "longitude_deg": longitude}


def _available_payload(artifact: bytes = b"synthetic primary horizontal boundary") -> dict:
    checksum = hashlib.sha256(artifact).hexdigest()
    boundary_source = {
        "id": "mlit-ks4-3-horizontal-primary-synthetic",
        "distribution": "OFFICIAL_PRIMARY",
        "source_category": "PRIMARY_HORIZONTAL_BOUNDARY",
        "publisher": "Synthetic test primary publisher",
        "document_id": "KS4-3-HORIZONTAL-SYNTHETIC",
        "url": "https://example.invalid/ks4-3-horizontal.pdf",
        "revision": "2026-08-31",
        "effective_date": "2026-08-31",
        "retrieved_date": "2026-08-31",
        "sha256": checksum,
        "artifact_path": "artifacts/ks4-3-horizontal.bin",
        "artifact_sha256": checksum,
        "coordinate_validation": {
            "finite": True,
            "in_bounds": True,
            "non_collinear": True,
            "no_duplicate_vertices": True,
            "no_self_intersections": True,
            "nonzero_area": True,
            "ring_closed": True,
            "vertex_count": 4,
        },
    }
    return {
        "schema_version": 1,
        "status": "AVAILABLE",
        "revision": "2026-08-31-rjfm-inbound-west-guidance-synthetic",
        "policy": {
            "source_id": "user-decision-rjfm-inbound-west-sector-synthetic",
            "magnetic_bearing_min_deg": 250.0,
            "magnetic_bearing_max_deg": 290.0,
            "coarse_bearing_step_deg": 5.0,
            "dme_rounding_increment_nm": 0.5,
        },
        "mze": {
            "source_id": "aip-rjfm-mze-synthetic",
            "latitude_deg": 31.8787,
            "longitude_deg": 131.4374,
            "elevation_ft_msl": 54.0,
        },
        "sources": [
            {
                "id": "user-decision-rjfm-inbound-west-sector-synthetic",
                "distribution": "USER_DECISION",
                "title": "Synthetic inbound policy",
            },
            {
                "id": "aip-rjfm-mze-synthetic",
                "distribution": "PUBLIC_AIP_MIRROR",
                "title": "Synthetic MZE source",
                "publisher": "Synthetic test publisher",
                "document_id": "SYNTHETIC-MZE-DOCUMENT",
                "revision": "2026-08-31",
                "url": "https://example.invalid/rjfm-mze.pdf",
                "effective_date": "2026-08-31",
                "retrieved_date": "2026-08-31",
                "sha256": hashlib.sha256(b"synthetic MZE document").hexdigest(),
                "position": _point(31.8787, 131.4374),
                "elevation_ft_msl": 54.0,
            },
            boundary_source,
        ],
        "boundary": {
            "source_id": boundary_source["id"],
            "revision": boundary_source["revision"],
            "checksum_sha256": checksum,
            "polygon_vertices": [
                _point(32.0, 131.0),
                _point(32.0, 131.2),
                _point(32.2, 131.2),
                _point(32.2, 131.0),
                _point(32.0, 131.0),
            ],
        },
    }


def _write_pack(
    root: Path,
    payload: dict,
    artifact: bytes = b"synthetic primary horizontal boundary",
    *,
    artifact_path: str = "artifacts/ks4-3-horizontal.bin",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload_path = root / "rjfm-inbound-guidance-reference.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    source_path = root / Path(*artifact_path.split("/"))
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(artifact)
    manifest = {
        "format_version": 1,
        "payload": {
            "path": payload_path.name,
            "sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _invalid_pack(tmp_path: Path, mutate) -> None:
    payload = _available_payload()
    mutate(payload)
    _write_pack(tmp_path, payload)
    with pytest.raises(RjfmInboundReferenceError):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


def test_reference_pack_is_fail_closed_and_unavailable() -> None:
    ref = RjfmInboundGuidanceReference.from_directory(ROOT / "data/reference/rjfm-inbound-guidance")
    assert ref.status == "UNAVAILABLE"
    assert not ref.available_for_solver
    assert ref.boundary is None
    assert ref.reason_code == "KS43_HORIZONTAL_BOUNDARY_UNVERIFIED"


def test_valid_synthetic_available_pack_requires_and_verifies_primary_artifact(
    tmp_path: Path,
) -> None:
    artifact = b"synthetic primary horizontal boundary"
    _write_pack(tmp_path, _available_payload(artifact), artifact)
    ref = RjfmInboundGuidanceReference.from_directory(tmp_path)
    assert ref.status == "AVAILABLE"
    assert ref.available_for_solver
    assert ref.boundary is not None
    assert len(ref.boundary.polygon_vertices) == 4
    assert ref.boundary.polygon_vertices[0] != ref.boundary.polygon_vertices[-1]


def test_reference_rejects_payload_checksum_mismatch(tmp_path: Path) -> None:
    source = ROOT / "data/reference/rjfm-inbound-guidance"
    (tmp_path / "rjfm-inbound-guidance-reference.json").write_bytes(
        (source / "rjfm-inbound-guidance-reference.json").read_bytes()
    )
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["payload"]["sha256"] = "0" * 64
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RjfmInboundReferenceError, match="SHA-256 mismatch"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


@pytest.mark.parametrize("missing", ["manifest", "payload"])
def test_reference_wraps_missing_files(tmp_path: Path, missing: str) -> None:
    if missing == "manifest":
        with pytest.raises(RjfmInboundReferenceError, match="manifest is missing"):
            RjfmInboundGuidanceReference.from_directory(tmp_path)
        return
    payload = _available_payload()
    _write_pack(tmp_path, payload)
    (tmp_path / "rjfm-inbound-guidance-reference.json").unlink()
    with pytest.raises(RjfmInboundReferenceError, match="payload is missing"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


@pytest.mark.parametrize("bad_path", ["../outside.json", "/tmp/outside.json", "..\\outside.json"])
def test_reference_rejects_unsafe_manifest_paths(tmp_path: Path, bad_path: str) -> None:
    payload = _available_payload()
    _write_pack(tmp_path, payload)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["payload"]["path"] = bad_path
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RjfmInboundReferenceError, match="path is unsafe"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


def test_reference_rejects_symlink_escape_for_manifest(tmp_path: Path) -> None:
    payload = _available_payload()
    _write_pack(tmp_path, payload)
    outside = tmp_path.parent / "outside-manifest.json"
    outside.write_bytes((tmp_path / "manifest.json").read_bytes())
    (tmp_path / "manifest.json").unlink()
    (tmp_path / "manifest.json").symlink_to(outside)
    with pytest.raises(RjfmInboundReferenceError, match="manifest"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


def test_reference_rejects_symlink_escape_for_payload(tmp_path: Path) -> None:
    payload = _available_payload()
    _write_pack(tmp_path, payload)
    outside = tmp_path.parent / "outside-payload.json"
    outside.write_bytes((tmp_path / "rjfm-inbound-guidance-reference.json").read_bytes())
    (tmp_path / "rjfm-inbound-guidance-reference.json").unlink()
    (tmp_path / "rjfm-inbound-guidance-reference.json").symlink_to(outside)
    with pytest.raises(RjfmInboundReferenceError, match="payload"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


def test_reference_rejects_symlink_escape_for_vendored_artifact(tmp_path: Path) -> None:
    payload = _available_payload()
    _write_pack(tmp_path, payload)
    outside = tmp_path.parent / "outside-primary-artifact.bin"
    outside.write_bytes(b"outside")
    artifact = tmp_path / "artifacts/ks4-3-horizontal.bin"
    artifact.unlink()
    artifact.symlink_to(outside)
    with pytest.raises(RjfmInboundReferenceError, match="artifact path is unsafe"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["sources"][2].update({"distribution": "USER_DECISION"}),
        lambda data: data["sources"][2].update({"distribution": "PUBLIC_AIP_MIRROR"}),
        lambda data: data["sources"][2].pop("source_category"),
        lambda data: data["sources"][2].pop("publisher"),
        lambda data: data["sources"][2].pop("document_id"),
        lambda data: data["sources"][2].pop("url"),
        lambda data: data["sources"][2].pop("revision"),
        lambda data: data["sources"][2].pop("effective_date"),
        lambda data: data["sources"][2].pop("retrieved_date"),
        lambda data: data["sources"][2].update({"display_only": True}),
        lambda data: data["sources"][2].update({"availability": "UNAVAILABLE"}),
        lambda data: data["sources"][2].update({"sha256": "0" * 64}),
        lambda data: data["sources"][2].update({"artifact_sha256": "0" * 64}),
        lambda data: data["sources"][2].pop("artifact_path"),
        lambda data: data["boundary"].update({"source_id": "other-source"}),
        lambda data: data["boundary"].update({"revision": "other-revision"}),
        lambda data: data["boundary"].update({"checksum_sha256": "0" * 64}),
        lambda data: data["sources"][2].pop("coordinate_validation"),
    ],
)
def test_available_reference_rejects_unverified_or_mismatched_provenance(
    tmp_path: Path, mutate
) -> None:
    _invalid_pack(tmp_path, mutate)


@pytest.mark.parametrize(
    "vertices",
    [
        [_point(32.0, 131.0), _point(32.0, 131.1)],
        [_point(32.0, 131.0), _point(32.1, 131.1), _point(32.2, 131.2)],
        [_point(32.0, 131.0), _point(32.0, 131.2), _point(32.2, 131.2), _point(32.0, 131.0)],
        [
            _point(32.0, 131.0),
            _point(32.2, 131.2),
            _point(32.0, 131.2),
            _point(32.2, 131.0),
            _point(32.0, 131.0),
        ],
        [
            _point(32.0, 131.0),
            _point(32.0, 131.2),
            _point(32.2, 131.2),
            _point(32.2, 131.0),
            _point(32.0, 131.0),
            _point(32.0, 131.0),
        ],
        [
            _point(32.0, 131.0),
            _point(32.0, 131.2),
            _point(32.2, 131.2),
            _point(32.2, 131.0),
            _point(32.1, 131.0),
            _point(32.0, 131.0),
        ],
    ],
    ids=["too_few", "collinear", "duplicate_vertex", "crossing", "duplicate_closure", "overlap"],
)
def test_available_reference_rejects_invalid_polygon(tmp_path: Path, vertices) -> None:
    payload = _available_payload()
    payload["boundary"]["polygon_vertices"] = vertices
    _write_pack(tmp_path, payload)
    with pytest.raises(RjfmInboundReferenceError):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


def test_available_reference_rejects_coordinate_metadata_drift(tmp_path: Path) -> None:
    payload = _available_payload()
    payload["sources"][2]["coordinate_validation"]["vertex_count"] = 5
    _write_pack(tmp_path, payload)
    with pytest.raises(RjfmInboundReferenceError, match="coordinate validation"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


def test_unavailable_payload_rejects_any_solver_polygon() -> None:
    payload = json.loads(
        (
            ROOT / "data/reference/rjfm-inbound-guidance/rjfm-inbound-guidance-reference.json"
        ).read_text()
    )
    payload["boundary"] = {"polygon_vertices": [_point(0, 0), _point(0, 1), _point(1, 1)]}
    with pytest.raises(RjfmInboundReferenceError, match="unavailable reference"):
        from autonavlog.storage.rjfm_inbound_reference import _parse

        _parse(payload, "0" * 64)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest.update({"format_version": 2}),
        lambda manifest: manifest["payload"].pop("path"),
        lambda manifest: manifest["payload"].update({"extra": True}),
    ],
    ids=["unsupported_version", "missing_payload_path", "unknown_payload_field"],
)
def test_reference_rejects_invalid_manifest_schema(tmp_path: Path, mutate) -> None:
    _write_pack(tmp_path, _available_payload())
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    mutate(manifest)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RjfmInboundReferenceError, match="manifest schema"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


@pytest.mark.parametrize("artifact_path", ["../outside.bin", "/tmp/outside.bin", "..\\outside.bin"])
def test_reference_rejects_unsafe_vendored_artifact_paths(
    tmp_path: Path, artifact_path: str
) -> None:
    payload = _available_payload()
    payload["sources"][2]["artifact_path"] = artifact_path
    _write_pack(tmp_path, payload)
    with pytest.raises(RjfmInboundReferenceError, match="artifact path is unsafe"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


def test_reference_accepts_valid_concave_angled_polygon(tmp_path: Path) -> None:
    payload = _available_payload()
    payload["boundary"]["polygon_vertices"] = [
        _point(32.0, 131.0),
        _point(32.0, 131.3),
        _point(32.15, 131.15),
        _point(32.3, 131.3),
        _point(32.3, 131.0),
        _point(32.0, 131.0),
    ]
    payload["sources"][2]["coordinate_validation"]["vertex_count"] = 5
    _write_pack(tmp_path, payload)
    ref = RjfmInboundGuidanceReference.from_directory(tmp_path)
    assert ref.available_for_solver
    assert ref.boundary is not None
    assert len(ref.boundary.polygon_vertices) == 5


def test_reference_rejects_adjacent_collinear_overlap(tmp_path: Path) -> None:
    payload = _available_payload()
    payload["boundary"]["polygon_vertices"] = [
        _point(32.0, 131.0),
        _point(32.0, 131.2),
        _point(32.0, 131.1),
        _point(32.2, 131.2),
        _point(32.2, 131.0),
        _point(32.0, 131.0),
    ]
    payload["sources"][2]["coordinate_validation"]["vertex_count"] = 5
    _write_pack(tmp_path, payload)
    with pytest.raises(RjfmInboundReferenceError, match="self-intersects"):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


def test_reference_rejects_non_adjacent_touching_edges(tmp_path: Path) -> None:
    payload = _available_payload()
    payload["boundary"]["polygon_vertices"] = [
        _point(32.0, 131.0),
        _point(32.0, 131.3),
        _point(32.3, 131.3),
        _point(32.3, 131.0),
        _point(32.0, 131.1),
        _point(32.1, 131.1),
        _point(32.0, 131.0),
    ]
    _write_pack(tmp_path, payload)
    with pytest.raises(RjfmInboundReferenceError):
        RjfmInboundGuidanceReference.from_directory(tmp_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["sources"][1].pop("url"),
        lambda data: data["sources"][1].update({"sha256": "invalid"}),
        lambda data: data["sources"][1].update({"retrieved_date": "not-a-date"}),
        lambda data: data["sources"][1].update({"position": _point(31.9, 131.4)}),
        lambda data: data["sources"][1].update({"elevation_ft_msl": 55.0}),
        lambda data: data["sources"][1].pop("publisher"),
        lambda data: data["sources"][1].pop("document_id"),
        lambda data: data["sources"][1].pop("revision"),
        lambda data: data["sources"][1].pop("position"),
        lambda data: data["sources"][1].pop("elevation_ft_msl"),
    ],
)
def test_available_reference_rejects_invalid_mze_provenance(tmp_path: Path, mutate) -> None:
    payload = _available_payload()
    mutate(payload)
    _write_pack(tmp_path, payload)
    with pytest.raises(RjfmInboundReferenceError):
        RjfmInboundGuidanceReference.from_directory(tmp_path)
