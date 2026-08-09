from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from autonavlog.release_validation import (
    RuntimeDataValidationError,
    validate_runtime_data,
)
from tests.reference_pack import write_reference_pack


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _runtime_data(
    tmp_path: Path,
    *,
    verified: bool = True,
    reference_verified: bool = True,
    include_airports: bool = True,
) -> Path:
    root = tmp_path / "data"
    write_reference_pack(
        root / "reference" / "default",
        verified=reference_verified,
        include_airports=include_airports,
    )
    climb_path = root / "performance" / "climb.csv"
    _write_csv(
        climb_path,
        [
            "pressure_altitude_ft",
            "temperature_c",
            "weight_lb",
            "cumulative_time_min",
            "cumulative_fuel_gal",
            "cumulative_distance_nm",
            "source_page",
        ],
        [
            {
                "pressure_altitude_ft": altitude,
                "temperature_c": temperature,
                "weight_lb": 3400,
                "cumulative_time_min": time,
                "cumulative_fuel_gal": fuel,
                "cumulative_distance_nm": distance,
                "source_page": "5-10",
            }
            for temperature in (0, 20)
            for altitude, time, fuel, distance in (
                (0, 0, 0, 0),
                (5000, 10, 4, 15),
                (6000, 12, 4.8, 18),
            )
        ],
    )
    cruise_path = root / "performance" / "cruise.csv"
    _write_csv(
        cruise_path,
        [
            "pressure_altitude_ft",
            "isa_deviation_c",
            "rpm",
            "map_in_hg",
            "power_percent",
            "ktas",
            "gph",
            "source_page",
        ],
        [
            {
                "pressure_altitude_ft": altitude,
                "isa_deviation_c": deviation,
                "rpm": 2300,
                "map_in_hg": 20,
                "power_percent": 65,
                "ktas": 150,
                "gph": 15,
                "source_page": "5-20",
            }
            for altitude in (4000, 6000)
            for deviation in (-15, 15)
        ],
    )
    manifest = {
        "schema_version": 1,
        "aircraft": "SR22 G6",
        "source_document": "approved fixture",
        "source_revision": "2026-01",
        "verified_against": "independent fixture",
        "validation_status": "VERIFIED" if verified else "UNVERIFIED",
        "tables": [
            {
                "id": "climb_time_fuel_distance",
                "file": climb_path.name,
                "source_page": "5-10",
                "sha256": hashlib.sha256(climb_path.read_bytes()).hexdigest(),
            },
            {
                "id": "cruise_performance",
                "file": cruise_path.name,
                "source_page": "5-20",
                "sha256": hashlib.sha256(cruise_path.read_bytes()).hexdigest(),
            },
        ],
    }
    (root / "performance" / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return root


def test_runtime_data_validation_accepts_complete_source_backed_fixture(
    tmp_path: Path,
) -> None:
    report = validate_runtime_data(_runtime_data(tmp_path))

    assert report["status"] == "PASS"
    assert report["airports"]["required"] == ["RJFM", "RJFO"]
    assert report["performance"]["climb_row_count"] == 6
    assert report["performance"]["cruise_row_count"] == 4
    assert report["performance"]["nominal_climb"]["time_min"] == pytest.approx(9.962)


def test_runtime_data_validation_rejects_unverified_performance(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeDataValidationError, match="source verification"):
        validate_runtime_data(_runtime_data(tmp_path, verified=False))


def test_runtime_data_validation_rejects_unverified_pattern_altitudes(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeDataValidationError, match="is not VERIFIED"):
        validate_runtime_data(_runtime_data(tmp_path, reference_verified=False))


def test_runtime_data_validation_rejects_header_only_repository(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeDataValidationError, match="no airport rows"):
        validate_runtime_data(_runtime_data(tmp_path, include_airports=False))
