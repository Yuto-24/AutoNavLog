from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from autonavlog.domain.enums import RouteNodeRole
from autonavlog.domain.planning import (
    AirportSelection,
    CheckPointSelection,
    PatternAltitudeValidationStatus,
    PointSelection,
)
from autonavlog.storage.airports import AirportRepository
from autonavlog.storage.reference_data import (
    ReferenceCatalog,
    ReferenceDataCatalogRepository,
    ReferenceDataError,
)


def _airport(
    airport_id: str,
    *,
    verified: bool = True,
    latitude: float = 31.9,
) -> AirportSelection:
    return AirportSelection(
        id=airport_id,
        icao=airport_id,
        name=airport_id,
        latitude_deg=latitude,
        longitude_deg=131.45,
        elevation_ft_msl=20.0,
        pattern_altitude_ft_msl=1020.0,
        pattern_altitude_source="fixture source",
        pattern_altitude_source_revision="fixture-v1",
        pattern_altitude_validation_status=(
            PatternAltitudeValidationStatus.VERIFIED
            if verified
            else PatternAltitudeValidationStatus.UNVERIFIED
        ),
        source="fixture",
        source_revision="fixture-v1",
    )


def _point() -> PointSelection:
    return PointSelection(
        id="TP1",
        name="Turn Point 1",
        latitude_deg=32.0,
        longitude_deg=131.5,
        point_role=RouteNodeRole.TURN_POINT,
        source="fixture",
        source_revision="fixture-v1",
        notes="",
    )


def _check_point() -> CheckPointSelection:
    return CheckPointSelection(
        id="CP1",
        name="Check Point 1",
        latitude_deg=32.1,
        longitude_deg=131.6,
        source="fixture",
        source_revision="fixture-v1",
        notes="abeam",
    )


def _publish(
    repository: ReferenceDataCatalogRepository,
    revision: str = "r1",
    *,
    destination_verified: bool = True,
) -> ReferenceCatalog:
    return repository.publish_revision(
        dataset_id="fixture",
        revision=revision,
        airports=[
            _airport("RJFM"),
            _airport(
                "RJFO",
                verified=destination_verified,
                latitude=33.48,
            ),
        ],
        points=[_point()],
        check_points=[_check_point()],
    )


def test_publish_open_snapshot_and_origins_round_trip(tmp_path: Path) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    _publish(repository)

    catalog = repository.open_active()
    route_node_id = uuid4()
    reference_id = uuid4()
    snapshot = catalog.snapshot(
        departure_airport_id="RJFM",
        destination_airport_id="RJFO",
        route_points={route_node_id: "TP1"},
        check_points={reference_id: "CP1"},
    )

    assert catalog.manifest.dataset_id == "fixture"
    assert snapshot.route_points[route_node_id].name == "Turn Point 1"
    assert snapshot.check_points[reference_id].name == "Check Point 1"
    assert snapshot.destination_airport.origin is not None
    assert snapshot.destination_airport.origin.dataset_revision == "r1"
    assert len(snapshot.destination_airport.origin.row_fingerprint) == 64


def test_catalog_rows_are_parsed_from_the_hashed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    _publish(repository)
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path.suffix == ".csv":
            raise AssertionError("verified CSV must not be reopened")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    assert sorted(repository.open_active().airports) == ["RJFM", "RJFO"]


def test_airport_repository_drops_unverified_pattern_altitudes(
    tmp_path: Path,
) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    catalog = _publish(repository, destination_verified=False)

    airports = AirportRepository.from_reference_catalog(catalog)

    assert airports.get("RJFM").pattern_altitude_ft_msl == 1020
    assert airports.get("RJFO").pattern_altitude_ft_msl is None


def test_revision_is_immutable_and_rollback_restores_previous(tmp_path: Path) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    _publish(repository, "r1")
    _publish(repository, "r2")

    with pytest.raises(FileExistsError, match="immutable"):
        _publish(repository, "r2")

    restored = repository.rollback()
    assert restored.manifest.revision == "r1"
    assert repository.open_active().manifest.revision == "r1"


def test_tampered_csv_is_rejected(tmp_path: Path) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    catalog = _publish(repository)
    airports_path = catalog.root / "airports.csv"
    airports_path.write_text(
        airports_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ReferenceDataError, match="SHA-256 mismatch"):
        repository.open_active()


def test_duplicate_manifest_key_is_rejected(tmp_path: Path) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    catalog = _publish(repository)
    manifest_path = catalog.root / "reference-manifest.json"
    raw = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        raw.replace(
            '"dataset_id": "fixture",',
            '"dataset_id": "fixture",\n  "dataset_id": "other",',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReferenceDataError, match="invalid reference manifest"):
        repository.open_active()


def test_manifest_path_traversal_is_rejected(tmp_path: Path) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    catalog = _publish(repository)
    manifest_path = catalog.root / "reference-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tables"][0]["path"] = "../airports.csv"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReferenceDataError, match="unsafe reference table path"):
        repository.open_active()


def test_non_utc_manifest_timestamp_is_rejected(tmp_path: Path) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    catalog = _publish(repository)
    manifest_path = catalog.root / "reference-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at_utc"] = "2026-08-09T12:00:00+09:00"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReferenceDataError, match="invalid reference manifest"):
        repository.open_active()


def test_unsafe_dataset_and_revision_are_rejected(tmp_path: Path) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    with pytest.raises(ReferenceDataError, match="unsafe dataset id"):
        repository.publish_revision(
            dataset_id="../escape",
            revision="r1",
            airports=[],
            points=[],
            check_points=[],
        )
    with pytest.raises(ReferenceDataError, match="unsafe revision"):
        repository.activate("fixture", "../../escape")


def test_unverified_destination_cannot_be_snapshotted(tmp_path: Path) -> None:
    repository = ReferenceDataCatalogRepository(tmp_path)
    catalog = _publish(repository, destination_verified=False)

    with pytest.raises(ReferenceDataError, match="not verified"):
        catalog.snapshot(
            departure_airport_id="RJFM",
            destination_airport_id="RJFO",
        )


def test_bundled_default_pack_has_verified_training_airports(
    tmp_path: Path,
) -> None:
    bundled_default = Path(__file__).resolve().parents[2] / "data" / "reference" / "default"
    catalog = ReferenceDataCatalogRepository(
        tmp_path,
        bundled_default=bundled_default,
    ).open_active()

    expected_pattern_altitudes = {
        "RJFC": 1100,
        "RJFE": 1300,
        "RJFG": 1800,
        "RJFK": 1900,
        "RJFM": 1000,
        "RJFO": 1000,
        "RJFS": 1000,
        "RJFT": 1700,
        "RJFU": 1000,
        "RJOA": 2100,
        "RJOB": 1800,
        "RJOK": 1000,
        "RJOM": 1000,
        "RJOT": 1600,
    }
    assert {
        airport_id: airport.pattern_altitude_ft_msl
        for airport_id, airport in catalog.airports.items()
    } == expected_pattern_altitudes
    assert all(
        airport.pattern_altitude_validation_status == PatternAltitudeValidationStatus.VERIFIED
        for airport in catalog.airports.values()
    )
    assert (
        hashlib.sha256((bundled_default / "airports.csv").read_bytes()).hexdigest()
        == catalog.manifest.tables[0].sha256
    )
    snapshot = catalog.snapshot(
        departure_airport_id="RJFM",
        destination_airport_id="RJFO",
    )
    assert snapshot.destination_airport.pattern_altitude_ft_msl == 1000
