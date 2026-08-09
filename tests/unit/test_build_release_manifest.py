from __future__ import annotations

import csv
import hashlib
import json
import runpy
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from tests.reference_pack import write_reference_pack

SCRIPT_NAMESPACE = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts" / "build_release_manifest.py"),
    run_name="build_release_manifest_test",
)
ReleaseManifestError = cast(
    type[RuntimeError],
    SCRIPT_NAMESPACE["ReleaseManifestError"],
)
build_release_manifest = cast(
    Callable[..., dict[str, Any]],
    SCRIPT_NAMESPACE["build_release_manifest"],
)

VERSION = "0.2.0"
SOURCE_COMMIT = "a" * 40


def _write_csv(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_wheel(
    path: Path,
    *,
    distribution: str,
    version: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dist_info = f"{distribution.replace('-', '_')}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{dist_info}/METADATA",
            "\n".join(
                (
                    "Metadata-Version: 2.1",
                    f"Name: {distribution}",
                    f"Version: {version}",
                    "",
                )
            ),
        )
        archive.writestr(
            f"{distribution.replace('-', '_')}/__init__.py",
            f'__version__ = "{version}"\n',
        )


def _write_notebook(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": [
                            'manifest_path = "release-manifest.json"\n',
                            "from autonavlog.weather.msm_adapter import MsmWeatherProvider\n",
                            "app.render()\n",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_runtime_data(root: Path) -> tuple[int, int, int]:
    data_root = root / "data" / "autonavlog"
    write_reference_pack(data_root / "reference" / "default")
    performance_root = data_root / "performance"
    climb_path = performance_root / "climb.csv"
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
                "pressure_altitude_ft": 5000,
                "temperature_c": 15,
                "weight_lb": 3400,
                "cumulative_time_min": 10,
                "cumulative_fuel_gal": 4,
                "cumulative_distance_nm": 15,
                "source_page": "5-10",
            }
        ],
    )
    cruise_path = performance_root / "cruise.csv"
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
                "pressure_altitude_ft": 5000,
                "isa_deviation_c": 0,
                "rpm": 2300,
                "map_in_hg": 20,
                "power_percent": 65,
                "ktas": 150,
                "gph": 15,
                "source_page": "5-20",
            }
        ],
    )
    performance_manifest = {
        "schema_version": 1,
        "aircraft": "SR22 G6",
        "source_document": "approved fixture",
        "source_revision": "2026-01",
        "verified_against": "independent fixture",
        "validation_status": "VERIFIED",
        "climb_temperature_policy": "ISA_BASELINE_10_PERCENT_PER_10C_ABOVE",
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
    (performance_root / "manifest.json").write_text(
        json.dumps(performance_manifest),
        encoding="utf-8",
    )
    return 2, 1, 1


def _write_terrain(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for member in (
            "values_m.npy",
            "latitudes.npy",
            "longitudes.npy",
            "metadata.npy",
        ):
            archive.writestr(member, b"fixture")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provenance() -> dict[str, object]:
    source_url = "http://example.invalid/msm.bin"
    return {
        "source_urls": [source_url],
        "source_hashes": {source_url: "b" * 64},
        "initial_time_utc": "2026-07-30T00:00:00+00:00",
        "interpolation_method": "fixture interpolation",
        "trace": {"grid": "fixture"},
    }


def _write_acceptance_reports(
    root: Path,
    *,
    airport_rows: int,
    climb_rows: int,
    cruise_rows: int,
    terrain_sha256: str,
) -> None:
    acceptance_root = root / "acceptance"
    acceptance_root.mkdir(parents=True, exist_ok=True)
    (acceptance_root / "runtime-data-acceptance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "airports": {
                    "row_count": airport_rows,
                    "required": ["RJFM", "RJFO"],
                },
                "performance": {
                    "validation_status": "VERIFIED",
                    "climb_temperature_policy": ("ISA_BASELINE_10_PERCENT_PER_10C_ABOVE"),
                    "climb_row_count": climb_rows,
                    "cruise_row_count": cruise_rows,
                },
            }
        ),
        encoding="utf-8",
    )
    results = [
        {
            "request_id": f"{airport}-{kind}",
            "kind": "ALOFT" if kind == "aloft" else "ESTIMATED_QNH",
            "availability": "AVAILABLE",
            "provenance": _provenance(),
        }
        for airport in ("RJFM", "RJFO")
        for kind in ("aloft", "qnh")
    ]
    (acceptance_root / "real-msm-acceptance.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "mode": "LIVE",
                "live_executed": True,
                "package": {
                    "required_version": "0.2.1",
                    "observed_versions": {
                        "distribution": "0.2.1",
                        "module": "0.2.1",
                        "adapter": "0.2.1",
                    },
                },
                "terrain": {
                    "cache_sha256": terrain_sha256,
                    "source_sha256": "c" * 64,
                    "samples_m": {"RJFM": 123.0, "RJFO": 456.0},
                },
                "forecast": {
                    "result_count": len(results),
                    "results": results,
                },
            }
        ),
        encoding="utf-8",
    )


def _release_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "release"
    root.mkdir()
    _write_wheel(
        root / "wheels" / f"autonavlog-{VERSION}-py3-none-any.whl",
        distribution="autonavlog",
        version=VERSION,
    )
    _write_wheel(
        root / "wheels" / "jma_msm_wind-0.2.1-py3-none-any.whl",
        distribution="jma-msm-wind",
        version="0.2.1",
    )
    _write_notebook(root / "AutoNavLog.ipynb")
    row_counts = _write_runtime_data(root)
    terrain_sha256 = _write_terrain(root / "data" / "msm" / "terrain.npz")
    _write_acceptance_reports(
        root,
        airport_rows=row_counts[0],
        climb_rows=row_counts[1],
        cruise_rows=row_counts[2],
        terrain_sha256=terrain_sha256,
    )
    return root


def _build(root: Path) -> dict[str, Any]:
    return build_release_manifest(
        root,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        created_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )


def test_manifest_accepts_complete_release_and_preserves_bootstrap_shape(
    tmp_path: Path,
) -> None:
    root = _release_fixture(tmp_path)

    manifest = _build(root)

    assert manifest["schema_version"] == 1
    assert manifest["release_version"] == VERSION
    assert manifest["autonavlog_version"] == VERSION
    assert manifest["source_commit"] == SOURCE_COMMIT
    assert manifest["msm_package_version"] == "0.2.1"
    assert manifest["acceptance"]["real_msm"]["mode"] == "LIVE"
    paths = {item["path"] for item in manifest["files"]}
    assert {
        "AutoNavLog.ipynb",
        "wheels/autonavlog-0.2.0-py3-none-any.whl",
        "wheels/jma_msm_wind-0.2.1-py3-none-any.whl",
        "data/autonavlog/reference/default/reference-manifest.json",
        "data/msm/terrain.npz",
        "acceptance/runtime-data-acceptance.json",
        "acceptance/real-msm-acceptance.json",
    } <= paths
    assert "release-manifest.json" not in paths
    for item in manifest["files"]:
        assert item["size"] > 0
        assert len(item["sha256"]) == 64
        assert ".." not in Path(item["path"]).parts
    assert json.loads((root / "release-manifest.json").read_text()) == manifest


def test_manifest_rejects_missing_required_wheel(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    (root / "wheels" / "jma_msm_wind-0.2.1-py3-none-any.whl").unlink()

    with pytest.raises(ReleaseManifestError, match="exactly one jma-msm-wind"):
        _build(root)


def test_manifest_rejects_reference_pack_without_airports(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    reference_root = root / "data" / "autonavlog" / "reference" / "default"
    airport_path = reference_root / "airports.csv"
    header = airport_path.read_text(encoding="utf-8").splitlines()[0]
    airport_path.write_text(header + "\n", encoding="utf-8")
    manifest_path = reference_root / "reference-manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    airport_table = next(table for table in document["tables"] if table["kind"] == "AIRPORT")
    airport_table["row_count"] = 0
    airport_table["sha256"] = hashlib.sha256(airport_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ReleaseManifestError, match="missing required airports"):
        _build(root)


def test_manifest_rejects_invalid_declared_performance_hash(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    manifest_path = root / "data" / "autonavlog" / "performance" / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["tables"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ReleaseManifestError, match="SHA-256 mismatch"):
        _build(root)


def test_manifest_rejects_wrong_climb_temperature_policy(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    manifest_path = root / "data" / "autonavlog" / "performance" / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["climb_temperature_policy"] = "TABLE_GRID"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ReleaseManifestError, match="climb_temperature_policy"):
        _build(root)


def test_manifest_rejects_performance_path_traversal(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    manifest_path = root / "data" / "autonavlog" / "performance" / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["tables"][0]["file"] = "../climb.csv"
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ReleaseManifestError, match="path traversal"):
        _build(root)


def test_manifest_rejects_non_live_acceptance_report(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    report_path = root / "acceptance" / "real-msm-acceptance.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["mode"] = "OFFLINE_PREFLIGHT"
    report["live_executed"] = False
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ReleaseManifestError, match="was not LIVE"):
        _build(root)


def test_manifest_rejects_symlink_outside_release(tmp_path: Path) -> None:
    root = _release_fixture(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (root / "escaped.bin").symlink_to(outside)

    with pytest.raises(ReleaseManifestError, match="symbolic link"):
        _build(root)
