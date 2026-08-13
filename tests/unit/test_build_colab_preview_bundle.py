from __future__ import annotations

import csv
import hashlib
import json
import runpy
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from tests.reference_pack import write_reference_pack

SCRIPT_NAMESPACE = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts" / "build_colab_preview_bundle.py"),
    run_name="build_colab_preview_bundle_test",
)
PreviewBundleError = cast(
    type[RuntimeError],
    SCRIPT_NAMESPACE["PreviewBundleError"],
)
build_colab_preview_bundle = cast(
    Callable[..., dict[str, Any]],
    SCRIPT_NAMESPACE["build_colab_preview_bundle"],
)


def _write_wheel(
    path: Path,
    *,
    distribution: str,
    version: str,
    unsafe_member: str | None = None,
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
        if distribution == "autonavlog":
            for member in (
                "autonavlog/presentation/colab.py",
                "autonavlog/weather/msm_adapter.py",
                "autonavlog/weather/msm_metar_provider.py",
            ):
                archive.writestr(member, b"# required preview module\n")
        if distribution == "jma-msm-wind":
            for member in (
                "msm_wind/client.py",
                "msm_wind/core.py",
            ):
                archive.writestr(member, b"# required preview module\n")
        if unsafe_member is not None:
            archive.writestr(unsafe_member, b"unsafe")


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


def _write_runtime_data(root: Path) -> None:
    write_reference_pack(root / "reference" / "default")
    performance = root / "performance"
    climb = performance / "climb.csv"
    cruise = performance / "cruise.csv"
    _write_csv(
        climb,
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
                "pressure_altitude_ft": 0,
                "temperature_c": 15,
                "weight_lb": 3600,
                "cumulative_time_min": 0,
                "cumulative_fuel_gal": 0,
                "cumulative_distance_nm": 0,
                "source_page": "5-30",
            }
        ],
    )
    _write_csv(
        cruise,
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
                "pressure_altitude_ft": 4000,
                "isa_deviation_c": 0,
                "rpm": 2500,
                "map_in_hg": 21.4,
                "power_percent": 65,
                "ktas": 165,
                "gph": 15.5,
                "source_page": "5-32",
            }
        ],
    )
    (performance / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "aircraft": "SR22 G6",
                "source_document": "official fixture",
                "source_revision": "Reissue A",
                "verified_against": "independent fixture",
                "validation_status": "VERIFIED",
                "climb_temperature_policy": ("ISA_BASELINE_10_PERCENT_PER_10C_ABOVE"),
                "tables": [
                    {
                        "id": "climb_time_fuel_distance",
                        "file": climb.name,
                        "source_page": "5-30",
                        "sha256": hashlib.sha256(climb.read_bytes()).hexdigest(),
                    },
                    {
                        "id": "cruise_performance",
                        "file": cruise.name,
                        "source_page": "5-32",
                        "sha256": hashlib.sha256(cruise.read_bytes()).hexdigest(),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    terrain = root / "msm" / "terrain.npz"
    terrain.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(terrain, "w") as archive:
        for member in (
            "values_m.npy",
            "latitudes.npy",
            "longitudes.npy",
            "metadata.npy",
        ):
            archive.writestr(member, b"verified terrain fixture")


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    autonavlog = tmp_path / "autonavlog-0.2.1-py3-none-any.whl"
    msm = tmp_path / "jma_msm_wind-0.2.1-py3-none-any.whl"
    data = tmp_path / "data"
    _write_wheel(
        autonavlog,
        distribution="autonavlog",
        version="0.2.1",
    )
    _write_wheel(
        msm,
        distribution="jma-msm-wind",
        version="0.2.1",
    )
    _write_runtime_data(data)
    return autonavlog, msm, data


def test_builder_creates_deterministic_self_verifying_bundle(tmp_path: Path) -> None:
    autonavlog, msm, data = _inputs(tmp_path)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_report = build_colab_preview_bundle(
        autonavlog,
        msm,
        first,
        data_root=data,
    )
    second_report = build_colab_preview_bundle(
        autonavlog,
        msm,
        second,
        data_root=data,
    )

    assert first_report["sha256"] == second_report["sha256"]
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        infos = archive.infolist()
        assert len({info.filename for info in infos}) == len(infos)
        assert all(not stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF) for info in infos)
        manifest = json.loads(archive.read("bundle-manifest.json"))
        names = {info.filename for info in infos}
        declared = {entry["path"] for entry in manifest["files"]}
        assert names == declared | {"bundle-manifest.json"}
        assert manifest["bundle_version"] == "0.2.1"
        assert manifest["runtime_data_root"] == "data"
        assert manifest["weather"] == {
            "aloft_wind_temperature": "MSM",
            "qnh": "MSM_ESTIMATED_QNH",
            "pzs_terrain_included": True,
            "terrain_path": "data/msm/terrain.npz",
            "terrain_sha256": hashlib.sha256((data / "msm/terrain.npz").read_bytes()).hexdigest(),
        }
        for entry in manifest["files"]:
            content = archive.read(entry["path"])
            assert entry["size"] == len(content)
            assert entry["sha256"] == hashlib.sha256(content).hexdigest()
        assert manifest["distributions"]["autonavlog"]["version"] == "0.2.1"
        assert {
            "data/reference/default/reference-manifest.json",
            "data/msm/terrain.npz",
        } <= names
        assert manifest["distributions"]["jma-msm-wind"]["version"] == "0.2.1"


def test_builder_rejects_wrong_wheel_metadata_and_preserves_output(
    tmp_path: Path,
) -> None:
    autonavlog, msm, data = _inputs(tmp_path)
    _write_wheel(
        autonavlog,
        distribution="autonavlog",
        version="9.9.9",
    )
    output = tmp_path / "bundle.zip"
    output.write_bytes(b"existing output")

    with pytest.raises(PreviewBundleError, match="version mismatch"):
        build_colab_preview_bundle(
            autonavlog,
            msm,
            output,
            data_root=data,
        )

    assert output.read_bytes() == b"existing output"


def test_builder_rejects_wheel_path_traversal(tmp_path: Path) -> None:
    autonavlog, msm, data = _inputs(tmp_path)
    _write_wheel(
        msm,
        distribution="jma-msm-wind",
        version="0.2.1",
        unsafe_member="../escape",
    )

    with pytest.raises(PreviewBundleError, match="path traversal"):
        build_colab_preview_bundle(
            autonavlog,
            msm,
            tmp_path / "bundle.zip",
            data_root=data,
        )


def test_builder_accepts_autonavlog_wheel_without_metar_provider(
    tmp_path: Path,
) -> None:
    autonavlog, msm, data = _inputs(tmp_path)
    with zipfile.ZipFile(autonavlog, "w") as archive:
        archive.writestr(
            "autonavlog-0.2.1.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: autonavlog\nVersion: 0.2.1\n",
        )
        archive.writestr("autonavlog/presentation/colab.py", b"# present\n")
        archive.writestr("autonavlog/weather/msm_adapter.py", b"# present\n")

    output = tmp_path / "bundle.zip"
    report = build_colab_preview_bundle(
        autonavlog,
        msm,
        output,
        data_root=data,
    )
    assert output.is_file()
    assert report["manifest"]["weather"]["qnh"] == "MSM_ESTIMATED_QNH"


def test_builder_rejects_missing_terrain(tmp_path: Path) -> None:
    autonavlog, msm, data = _inputs(tmp_path)
    (data / "msm/terrain.npz").unlink()

    with pytest.raises(PreviewBundleError, match="terrain cache is absent"):
        build_colab_preview_bundle(
            autonavlog,
            msm,
            tmp_path / "bundle.zip",
            data_root=data,
        )


def test_builder_rejects_unverified_pattern_altitude(
    tmp_path: Path,
) -> None:
    autonavlog, msm, data = _inputs(tmp_path)
    airports = data / "reference" / "default" / "airports.csv"
    original = airports.read_text(encoding="utf-8")
    unverified = original.replace(",VERIFIED,", ",UNVERIFIED,")
    assert unverified != original
    airports.write_text(unverified, encoding="utf-8")

    with pytest.raises(
        PreviewBundleError,
        match="pattern altitude validation_status must be VERIFIED",
    ):
        build_colab_preview_bundle(
            autonavlog,
            msm,
            tmp_path / "bundle.zip",
            data_root=data,
        )


def test_builder_rejects_runtime_data_symlink(tmp_path: Path) -> None:
    autonavlog, msm, data = _inputs(tmp_path)
    target = data / "performance" / "extra.txt"
    target.write_text("extra", encoding="utf-8")
    link = data / "performance" / "unsafe-link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(PreviewBundleError, match="symbolic link"):
        build_colab_preview_bundle(
            autonavlog,
            msm,
            tmp_path / "bundle.zip",
            data_root=data,
        )
