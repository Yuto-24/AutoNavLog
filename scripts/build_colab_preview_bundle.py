#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from autonavlog.storage.reference_data import ReferenceDataCatalogRepository, ReferenceDataError

BUNDLE_SCHEMA_VERSION = 1
BUNDLE_VERSION = "0.2.1"
AUTONAVLOG_DISTRIBUTION = "autonavlog"
AUTONAVLOG_VERSION = "0.2.1"
MSM_DISTRIBUTION = "jma-msm-wind"
MSM_VERSION = "0.2.1"
BUNDLE_FILENAME = f"autonavlog-colab-preview-{BUNDLE_VERSION}.zip"
MANIFEST_PATH = PurePosixPath("bundle-manifest.json")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = REPOSITORY_ROOT / "data"
REQUIRED_AIRPORT_IDS = frozenset({"RJFM", "RJFO"})
REQUIRED_PERFORMANCE_TABLE_IDS = frozenset({"climb_time_fuel_distance", "cruise_performance"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PENDING_TEXT = {"", "PENDING", "UNKNOWN", "UNVERIFIED"}
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class PreviewBundleError(RuntimeError):
    """The requested Colab preview bundle is incomplete or unsafe."""


def _fail(message: str) -> NoReturn:
    raise PreviewBundleError(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    return value


def _require_sequence(value: Any, label: str) -> Sequence[Any]:
    _require(
        isinstance(value, Sequence) and not isinstance(value, (str, bytes)),
        f"{label} must be an array",
    )
    return value


def _verified_text(value: Any, label: str) -> str:
    _require(isinstance(value, str), f"{label} must be text")
    stripped = value.strip()
    _require(
        stripped.upper() not in _PENDING_TEXT,
        f"{label} is missing or pending",
    )
    return stripped


def _safe_member_path(value: str, label: str) -> PurePosixPath:
    _require(value != "", f"{label} is empty")
    _require("\\" not in value, f"{label} must use POSIX separators")
    _require(
        not any(ord(character) < 32 for character in value),
        f"{label} contains a control character",
    )
    path = PurePosixPath(value)
    _require(not path.is_absolute(), f"{label} must be relative")
    _require(
        all(part not in {"", ".", ".."} for part in path.parts),
        f"{label} contains path traversal",
    )
    _require(
        not any(":" in part for part in path.parts),
        f"{label} contains a drive or URI component",
    )
    return path


def _require_regular_file(path: Path, label: str) -> None:
    _require(path.exists(), f"{label} is absent: {path}")
    _require(not path.is_symlink(), f"{label} must not be a symbolic link: {path}")
    _require(path.is_file(), f"{label} is not a regular file: {path}")
    _require(path.stat().st_size > 0, f"{label} is empty: {path}")


def _validate_zip_members(path: Path, label: str) -> list[zipfile.ZipInfo]:
    _require_regular_file(path, label)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _require(infos, f"{label} contains no members")
            observed_names: set[str] = set()
            for info in infos:
                stripped = info.filename[:-1] if info.filename.endswith("/") else info.filename
                _safe_member_path(stripped, f"{label} member {info.filename!r}")
                _require(
                    info.filename not in observed_names,
                    f"{label} contains a duplicate member: {info.filename}",
                )
                observed_names.add(info.filename)
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                _require(
                    not stat.S_ISLNK(unix_mode),
                    f"{label} contains a symbolic link: {info.filename}",
                )
            corrupt_member = archive.testzip()
            _require(
                corrupt_member is None,
                f"{label} has a corrupt member: {corrupt_member}",
            )
            return infos
    except zipfile.BadZipFile as error:
        raise PreviewBundleError(f"{label} is not a valid ZIP archive: {path}") from error


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _validate_wheel(
    path: Path,
    *,
    distribution: str,
    version: str,
    required_members: Sequence[str] = (),
) -> None:
    infos = _validate_zip_members(path, f"{distribution} wheel")
    member_names = {info.filename for info in infos}
    for member in required_members:
        _require(
            member in member_names,
            f"{distribution} wheel is missing required runtime module: {member}",
        )
    metadata_members = [
        info.filename for info in infos if info.filename.endswith(".dist-info/METADATA")
    ]
    _require(
        len(metadata_members) == 1,
        f"{distribution} wheel must contain exactly one dist-info/METADATA",
    )
    try:
        with zipfile.ZipFile(path) as archive:
            metadata_text = archive.read(metadata_members[0]).decode("utf-8")
    except (KeyError, UnicodeDecodeError) as error:
        raise PreviewBundleError(
            f"{distribution} wheel METADATA is missing or invalid UTF-8"
        ) from error
    metadata = Parser().parsestr(metadata_text)
    observed_name = metadata.get("Name", "")
    observed_version = metadata.get("Version", "")
    _require(
        _normalized_distribution_name(observed_name) == _normalized_distribution_name(distribution),
        f"{distribution} wheel metadata name mismatch: {observed_name!r}",
    )
    _require(
        observed_version == version,
        f"{distribution} wheel metadata version mismatch: {observed_version!r}",
    )


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    _require_regular_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PreviewBundleError(f"{label} is not valid UTF-8 JSON: {path}") from error
    return _require_mapping(value, label)


def _validate_airport_data(path: Path) -> None:
    _require_regular_file(path, "airport CSV")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise PreviewBundleError(f"airport CSV is invalid: {path}") from error
    _require(rows, "airport CSV has no data rows")
    by_id = {
        str(row.get("id", "")).strip().upper(): row
        for row in rows
        if str(row.get("id", "")).strip()
    }
    _require(
        REQUIRED_AIRPORT_IDS <= set(by_id),
        f"airport CSV is missing required airports: {sorted(REQUIRED_AIRPORT_IDS - set(by_id))}",
    )
    for airport_id in sorted(REQUIRED_AIRPORT_IDS):
        row = by_id[airport_id]
        _verified_text(row.get("source"), f"{airport_id} source")
        _verified_text(row.get("source_revision"), f"{airport_id} source_revision")
        _verified_text(
            row.get("pattern_altitude_source"),
            f"{airport_id} pattern altitude source",
        )
        _verified_text(
            row.get("pattern_altitude_source_revision"),
            f"{airport_id} pattern altitude source_revision",
        )
        _require(
            row.get("pattern_altitude_validation_status") == "VERIFIED",
            f"{airport_id} pattern altitude validation_status must be VERIFIED",
        )


def _validate_performance_data(root: Path) -> None:
    manifest = _read_json(root / "manifest.json", "performance manifest")
    _require(
        manifest.get("validation_status") == "VERIFIED",
        "performance manifest validation_status must be VERIFIED",
    )
    _require(
        manifest.get("aircraft") == "SR22 G6",
        "performance manifest aircraft must be SR22 G6",
    )
    for key in ("source_document", "source_revision", "verified_against"):
        _verified_text(manifest.get(key), f"performance manifest {key}")
    tables = _require_sequence(
        manifest.get("tables"),
        "performance manifest tables",
    )
    observed_ids: set[str] = set()
    for index, raw_table in enumerate(tables):
        table = _require_mapping(raw_table, f"performance table {index}")
        table_id = table.get("id")
        _require(isinstance(table_id, str), f"performance table {index} id is invalid")
        _require(
            table_id not in observed_ids,
            f"performance manifest contains duplicate table id: {table_id}",
        )
        observed_ids.add(table_id)
        raw_file = table.get("file")
        _require(isinstance(raw_file, str), f"{table_id} file must be text")
        relative = _safe_member_path(raw_file, f"{table_id} file")
        _require(
            len(relative.parts) == 1,
            f"{table_id} file must be directly under data/performance",
        )
        table_path = root / relative.name
        _require_regular_file(table_path, f"{table_id} CSV")
        expected_sha256 = table.get("sha256")
        _require(
            isinstance(expected_sha256, str)
            and _SHA256.fullmatch(expected_sha256.lower()) is not None,
            f"{table_id} must declare a 64-character SHA-256",
        )
        _require(
            _sha256_file(table_path) == expected_sha256.lower(),
            f"{table_id} SHA-256 mismatch",
        )
        _verified_text(table.get("source_page"), f"{table_id} source_page")
    _require(
        observed_ids == REQUIRED_PERFORMANCE_TABLE_IDS,
        "performance manifest must declare exactly "
        f"{sorted(REQUIRED_PERFORMANCE_TABLE_IDS)}; found {sorted(observed_ids)}",
    )


def _validate_terrain(path: Path) -> str:
    infos = _validate_zip_members(path, "Pzs terrain cache")
    member_names = {info.filename for info in infos}
    required_members = {
        "values_m.npy",
        "latitudes.npy",
        "longitudes.npy",
        "metadata.npy",
    }
    _require(
        required_members <= member_names,
        f"Pzs terrain cache is missing NPZ members: {sorted(required_members - member_names)}",
    )
    return _sha256_file(path)


def _collect_data_payloads(
    data_root: Path,
    terrain_path: Path,
) -> dict[PurePosixPath, bytes]:
    _require(data_root.is_dir(), f"data root is absent: {data_root}")
    _require(not data_root.is_symlink(), f"data root must not be a symbolic link: {data_root}")
    performance_root = data_root / "performance"
    reference_root = data_root / "reference" / "default"
    _require(
        performance_root.is_dir() and not performance_root.is_symlink(),
        f"performance data directory is absent or unsafe: {performance_root}",
    )
    _require(
        reference_root.is_dir() and not reference_root.is_symlink(),
        f"reference data directory is absent or unsafe: {reference_root}",
    )
    _validate_performance_data(performance_root)
    _validate_terrain(terrain_path)
    _validate_airport_data(reference_root / "airports.csv")
    try:
        ReferenceDataCatalogRepository(data_root / "reference-data-validation").open_pack(
            reference_root
        )
    except (OSError, ReferenceDataError, ValueError) as error:
        raise PreviewBundleError(f"reference data pack is invalid: {error}") from error

    payloads: dict[PurePosixPath, bytes] = {}
    for source_root in (performance_root, reference_root):
        for path in sorted(source_root.rglob("*")):
            _require(
                not path.is_symlink(),
                f"runtime data contains a symbolic link: {path}",
            )
            if path.is_dir():
                continue
            _require_regular_file(path, "runtime data file")
            relative = PurePosixPath("data") / PurePosixPath(path.relative_to(data_root).as_posix())
            _safe_member_path(str(relative), f"runtime data path {relative}")
            _require(
                relative not in payloads,
                f"runtime data contains a duplicate path: {relative}",
            )
            payloads[relative] = path.read_bytes()
    terrain_member = PurePosixPath("data/msm/terrain.npz")
    _require(
        terrain_member not in payloads,
        f"runtime data contains a duplicate path: {terrain_member}",
    )
    payloads[terrain_member] = terrain_path.read_bytes()
    return payloads


def _write_regular_member(
    archive: zipfile.ZipFile,
    path: PurePosixPath,
    content: bytes,
) -> None:
    info = zipfile.ZipInfo(str(path), date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, content, compresslevel=9)


def _bundle_manifest(
    payloads: Mapping[PurePosixPath, bytes],
    *,
    autonavlog_path: PurePosixPath,
    msm_path: PurePosixPath,
) -> dict[str, Any]:
    files = [
        {
            "path": str(path),
            "size": len(content),
            "sha256": _sha256_bytes(content),
        }
        for path, content in sorted(payloads.items(), key=lambda item: str(item[0]))
    ]
    by_path = {item["path"]: item for item in files}
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_version": BUNDLE_VERSION,
        "files": files,
        "distributions": {
            AUTONAVLOG_DISTRIBUTION: {
                "version": AUTONAVLOG_VERSION,
                **by_path[str(autonavlog_path)],
            },
            MSM_DISTRIBUTION: {
                "version": MSM_VERSION,
                **by_path[str(msm_path)],
            },
        },
        "runtime_data_root": "data",
        "weather": {
            "aloft_wind_temperature": "MSM",
            "qnh": "MSM_ESTIMATED_QNH",
            "pzs_terrain_included": True,
            "terrain_path": "data/msm/terrain.npz",
            "terrain_sha256": _sha256_bytes(payloads[PurePosixPath("data/msm/terrain.npz")]),
        },
    }


def _validate_built_bundle(path: Path) -> dict[str, Any]:
    infos = _validate_zip_members(path, "Colab preview bundle")
    regular_infos = [info for info in infos if not info.is_dir()]
    names = {info.filename for info in regular_infos}
    _require(
        str(MANIFEST_PATH) in names,
        f"Colab preview bundle is missing {MANIFEST_PATH}",
    )
    with zipfile.ZipFile(path) as archive:
        try:
            manifest = json.loads(archive.read(str(MANIFEST_PATH)).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PreviewBundleError("bundle manifest is not valid UTF-8 JSON") from error
        document = _require_mapping(manifest, "bundle manifest")
        _require(
            document.get("schema_version") == BUNDLE_SCHEMA_VERSION,
            "bundle manifest schema mismatch",
        )
        _require(
            document.get("bundle_version") == BUNDLE_VERSION,
            "bundle manifest version mismatch",
        )
        entries = _require_sequence(document.get("files"), "bundle manifest files")
        expected_names = {str(MANIFEST_PATH)}
        observed_paths: set[str] = set()
        for index, raw_entry in enumerate(entries):
            entry = _require_mapping(raw_entry, f"bundle file {index}")
            raw_member = entry.get("path")
            _require(isinstance(raw_member, str), f"bundle file {index} path is invalid")
            member = str(_safe_member_path(raw_member, f"bundle file {index} path"))
            _require(
                member != str(MANIFEST_PATH),
                "bundle manifest must not hash itself",
            )
            _require(
                member not in observed_paths,
                f"bundle manifest contains a duplicate file: {member}",
            )
            observed_paths.add(member)
            expected_names.add(member)
            content = archive.read(member)
            size = entry.get("size")
            _require(
                isinstance(size, int) and not isinstance(size, bool) and size >= 0,
                f"bundle file {member} size is invalid",
            )
            _require(len(content) == size, f"bundle file size mismatch: {member}")
            digest = entry.get("sha256")
            _require(
                isinstance(digest, str) and _SHA256.fullmatch(digest.lower()) is not None,
                f"bundle file {member} SHA-256 is invalid",
            )
            _require(
                _sha256_bytes(content) == digest.lower(),
                f"bundle file SHA-256 mismatch: {member}",
            )
        _require(
            names == expected_names,
            "bundle members do not exactly match the manifest: "
            f"extra={sorted(names - expected_names)}, "
            f"missing={sorted(expected_names - names)}",
        )
        return dict(document)


def build_colab_preview_bundle(
    autonavlog_wheel: str | Path,
    msm_wheel: str | Path,
    output: str | Path,
    *,
    data_root: str | Path = DEFAULT_DATA_ROOT,
    terrain_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build and atomically publish one self-verifying Colab preview ZIP."""

    autonavlog_path = Path(autonavlog_wheel)
    msm_path = Path(msm_wheel)
    output_path = Path(output)
    data_path = Path(data_root)
    _validate_wheel(
        autonavlog_path,
        distribution=AUTONAVLOG_DISTRIBUTION,
        version=AUTONAVLOG_VERSION,
        required_members=(
            "autonavlog/presentation/colab.py",
            "autonavlog/weather/msm_adapter.py",
        ),
    )
    _validate_wheel(
        msm_path,
        distribution=MSM_DISTRIBUTION,
        version=MSM_VERSION,
        required_members=(
            "msm_wind/client.py",
            "msm_wind/core.py",
        ),
    )
    terrain = data_path / "msm" / "terrain.npz" if terrain_path is None else Path(terrain_path)
    _require(
        autonavlog_path.name != msm_path.name,
        "wheel filenames must be distinct",
    )
    _safe_member_path(autonavlog_path.name, "AutoNavLog wheel filename")
    _safe_member_path(msm_path.name, "MSM wheel filename")

    payloads = _collect_data_payloads(data_path, terrain)
    bundled_autonavlog_path = PurePosixPath("wheels") / autonavlog_path.name
    bundled_msm_path = PurePosixPath("wheels") / msm_path.name
    payloads[bundled_autonavlog_path] = autonavlog_path.read_bytes()
    payloads[bundled_msm_path] = msm_path.read_bytes()
    manifest = _bundle_manifest(
        payloads,
        autonavlog_path=bundled_autonavlog_path,
        msm_path=bundled_msm_path,
    )
    manifest_bytes = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _require(
        not output_path.is_symlink(),
        f"output must not be a symbolic link: {output_path}",
    )
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            _write_regular_member(archive, MANIFEST_PATH, manifest_bytes)
            for member, content in sorted(
                payloads.items(),
                key=lambda item: str(item[0]),
            ):
                _write_regular_member(archive, member, content)
        checked_manifest = _validate_built_bundle(temporary_path)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {
        "path": str(output_path),
        "size": output_path.stat().st_size,
        "sha256": _sha256_file(output_path),
        "manifest": checked_manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build one verified AutoNavLog Colab preview runtime ZIP containing "
            "both pinned wheels and the source-backed airport/performance data."
        )
    )
    parser.add_argument("autonavlog_wheel", type=Path)
    parser.add_argument("msm_wheel", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help="source-backed data directory (default: repository data/)",
    )
    parser.add_argument(
        "--terrain",
        type=Path,
        default=None,
        help="verified Pzs terrain.npz (default: <data-root>/msm/terrain.npz)",
    )
    args = parser.parse_args()
    report = build_colab_preview_bundle(
        args.autonavlog_wheel,
        args.msm_wheel,
        args.output,
        data_root=args.data_root,
        terrain_path=args.terrain,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
