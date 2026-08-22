#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from autonavlog.domain.planning import PatternAltitudeValidationStatus
from autonavlog.storage.reference_data import ReferenceDataCatalogRepository, ReferenceDataError

MANIFEST_SCHEMA_VERSION = 1
MSM_PACKAGE_VERSION = "0.2.1"
MSM_COMMIT = "52adad68fd39d377c77774265229ea3b320c2c92"
CLIMB_TEMPERATURE_POLICY = "ISA_BASELINE_10_PERCENT_PER_10C_ABOVE"
MANIFEST_NAME = "release-manifest.json"
RUNTIME_ACCEPTANCE_PATH = PurePosixPath("acceptance/runtime-data-acceptance.json")
REAL_MSM_ACCEPTANCE_PATH = PurePosixPath("acceptance/real-msm-acceptance.json")
REQUIRED_AIRPORT_IDS = frozenset({"RJFM", "RJFO"})
REQUIRED_PERFORMANCE_TABLES = {
    "climb_time_fuel_distance": {
        "pressure_altitude_ft",
        "temperature_c",
        "weight_lb",
        "cumulative_time_min",
        "cumulative_fuel_gal",
        "cumulative_distance_nm",
        "source_page",
    },
    "cruise_performance": {
        "pressure_altitude_ft",
        "isa_deviation_c",
        "rpm",
        "map_in_hg",
        "power_percent",
        "ktas",
        "gph",
        "source_page",
    },
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_PENDING_TEXT = {"", "PENDING", "UNKNOWN", "UNVERIFIED"}


class ReleaseManifestError(RuntimeError):
    """The release tree is incomplete, unsafe, or internally inconsistent."""


def _fail(message: str) -> NoReturn:
    raise ReleaseManifestError(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_sha256(value: Any, label: str) -> str:
    _require(
        isinstance(value, str) and _SHA256.fullmatch(value.lower()) is not None,
        f"{label} must be a 64-character SHA-256",
    )
    return value.lower()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    return value


def _require_sequence(value: Any, label: str) -> Sequence[Any]:
    _require(
        isinstance(value, Sequence) and not isinstance(value, (str, bytes)),
        f"{label} must be an array",
    )
    return value


def _positive_int(value: Any, label: str) -> int:
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value > 0,
        f"{label} must be a positive integer",
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


def _safe_posix_path(value: str, label: str) -> PurePosixPath:
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


def _safe_child(root: Path, relative: PurePosixPath, label: str) -> Path:
    candidate = root.joinpath(*relative.parts)
    root_resolved = root.resolve()
    try:
        candidate.resolve().relative_to(root_resolved)
    except ValueError:
        _fail(f"{label} resolves outside the release directory")
    return candidate


def _load_json(path: Path, label: str) -> Mapping[str, Any]:
    _require(path.is_file(), f"{label} is absent: {path}")
    _require(path.stat().st_size > 0, f"{label} is empty: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseManifestError(f"{label} is not valid UTF-8 JSON: {path}") from error
    return _require_mapping(value, label)


def _validate_archive_member(name: str, label: str) -> PurePosixPath:
    stripped = name[:-1] if name.endswith("/") else name
    _require(stripped != "", f"{label} contains an empty member name")
    return _safe_posix_path(stripped, f"{label} member {name!r}")


def _validate_zip(path: Path, label: str) -> list[zipfile.ZipInfo]:
    _require(path.is_file(), f"{label} is absent: {path}")
    _require(path.stat().st_size > 0, f"{label} is empty: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _require(infos, f"{label} contains no members: {path}")
            for info in infos:
                _validate_archive_member(info.filename, label)
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
        raise ReleaseManifestError(f"{label} is not a valid ZIP archive: {path}") from error


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _validate_wheel(
    path: Path,
    *,
    distribution: str,
    version: str,
) -> None:
    infos = _validate_zip(path, f"{distribution} wheel")
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
        raise ReleaseManifestError(
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


def _find_required_wheel(
    wheels_root: Path,
    *,
    filename_prefix: str,
    distribution: str,
    version: str,
) -> Path:
    matches = sorted(wheels_root.glob(f"{filename_prefix}-{version}-*.whl"))
    _require(
        len(matches) == 1,
        f"release must contain exactly one {distribution} {version} wheel; found {len(matches)}",
    )
    _validate_wheel(matches[0], distribution=distribution, version=version)
    return matches[0]


def _read_csv(path: Path, label: str) -> tuple[set[str], list[dict[str, str]]]:
    _require(path.is_file(), f"{label} is absent: {path}")
    _require(path.stat().st_size > 0, f"{label} is empty: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            rows = [
                dict(row) for row in reader if any((value or "").strip() for value in row.values())
            ]
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise ReleaseManifestError(f"{label} is not valid UTF-8 CSV: {path}") from error
    _require(fields, f"{label} has no header")
    _require(rows, f"{label} has no data rows")
    return fields, rows


def _validate_reference_pack(path: Path) -> int:
    try:
        catalog = ReferenceDataCatalogRepository(path.parent / ".reference-validation").open_pack(
            path
        )
    except (OSError, ReferenceDataError, ValueError) as error:
        raise ReleaseManifestError(f"reference pack is invalid: {error}") from error
    _require(
        REQUIRED_AIRPORT_IDS <= set(catalog.airports),
        "reference pack is missing required airports: "
        f"{sorted(REQUIRED_AIRPORT_IDS - set(catalog.airports))}",
    )
    for airport_id in sorted(REQUIRED_AIRPORT_IDS):
        row = catalog.airports[airport_id]
        _verified_text(row.source, f"{airport_id} source")
        _verified_text(row.source_revision, f"{airport_id} source_revision")
        _verified_text(
            row.pattern_altitude_source,
            f"{airport_id} pattern_altitude_source",
        )
        _verified_text(
            row.pattern_altitude_source_revision,
            f"{airport_id} pattern_altitude_source_revision",
        )
        _require(
            row.pattern_altitude_validation_status == PatternAltitudeValidationStatus.VERIFIED,
            f"{airport_id} pattern altitude source verification is not VERIFIED",
        )
    return len(catalog.airports)


def _validate_performance(performance_root: Path) -> tuple[int, int]:
    document = _load_json(
        performance_root / "manifest.json",
        "performance manifest",
    )
    _require(
        document.get("schema_version") == 1,
        "performance manifest must use schema_version 1",
    )
    _require(
        document.get("validation_status") == "VERIFIED",
        "performance manifest validation_status must be VERIFIED",
    )
    _require(
        document.get("aircraft") == "SR22 G6",
        "performance manifest aircraft must be SR22 G6",
    )
    _require(
        document.get("climb_temperature_policy") == CLIMB_TEMPERATURE_POLICY,
        f"performance manifest climb_temperature_policy must be {CLIMB_TEMPERATURE_POLICY}",
    )
    for key in ("source_document", "source_revision", "verified_against"):
        _verified_text(document.get(key), f"performance manifest {key}")
    tables = _require_sequence(document.get("tables"), "performance manifest tables")
    descriptors: dict[str, Mapping[str, Any]] = {}
    declared_files: set[PurePosixPath] = set()
    row_counts: dict[str, int] = {}
    for index, raw_descriptor in enumerate(tables):
        descriptor = _require_mapping(
            raw_descriptor,
            f"performance manifest table {index}",
        )
        table_id = descriptor.get("id")
        _require(isinstance(table_id, str), f"performance table {index} id is invalid")
        _require(
            table_id not in descriptors,
            f"performance manifest contains duplicate table id: {table_id}",
        )
        descriptors[table_id] = descriptor
    _require(
        set(descriptors) == set(REQUIRED_PERFORMANCE_TABLES),
        "performance manifest must declare exactly "
        f"{sorted(REQUIRED_PERFORMANCE_TABLES)}; found {sorted(descriptors)}",
    )
    for table_id, required_fields in REQUIRED_PERFORMANCE_TABLES.items():
        descriptor = descriptors[table_id]
        raw_file = descriptor.get("file")
        _require(isinstance(raw_file, str), f"{table_id} file must be text")
        relative = _safe_posix_path(raw_file, f"{table_id} file")
        _require(
            relative not in declared_files,
            f"performance manifest contains duplicate file: {relative}",
        )
        declared_files.add(relative)
        path = _safe_child(performance_root, relative, f"{table_id} file")
        _require(path.is_file(), f"{table_id} CSV is absent: {path}")
        expected_hash = _require_sha256(
            descriptor.get("sha256"),
            f"{table_id} sha256",
        )
        _require(
            _sha256(path) == expected_hash,
            f"{table_id} SHA-256 mismatch: {relative}",
        )
        _verified_text(descriptor.get("source_page"), f"{table_id} source_page")
        fields, rows = _read_csv(path, f"{table_id} CSV")
        _require(
            required_fields <= fields,
            f"{table_id} CSV is missing columns: {sorted(required_fields - fields)}",
        )
        row_counts[table_id] = len(rows)
    actual_csv_files = {
        PurePosixPath(path.relative_to(performance_root).as_posix())
        for path in performance_root.rglob("*.csv")
        if path.is_file()
    }
    _require(
        actual_csv_files == declared_files,
        "performance CSV files do not exactly match the manifest: "
        f"declared={sorted(map(str, declared_files))}, "
        f"actual={sorted(map(str, actual_csv_files))}",
    )
    return (
        row_counts["climb_time_fuel_distance"],
        row_counts["cruise_performance"],
    )


def _validate_runtime_acceptance(
    path: Path,
    *,
    airport_rows: int,
    climb_rows: int,
    cruise_rows: int,
) -> Mapping[str, Any]:
    report = _load_json(path, "runtime-data acceptance report")
    _require(report.get("schema_version") == 1, "runtime-data report schema mismatch")
    _require(report.get("status") == "PASS", "runtime-data acceptance did not PASS")
    airports = _require_mapping(report.get("airports"), "runtime-data airports")
    required_airports = {
        str(value)
        for value in _require_sequence(
            airports.get("required"),
            "runtime-data required airports",
        )
    }
    _require(
        REQUIRED_AIRPORT_IDS <= required_airports,
        "runtime-data report does not cover RJFM and RJFO",
    )
    _require(
        _positive_int(airports.get("row_count"), "runtime-data airport row_count") == airport_rows,
        "runtime-data airport row_count does not match the packaged CSV",
    )
    performance = _require_mapping(
        report.get("performance"),
        "runtime-data performance",
    )
    _require(
        performance.get("validation_status") == "VERIFIED",
        "runtime-data performance is not VERIFIED",
    )
    _require(
        performance.get("climb_temperature_policy") == CLIMB_TEMPERATURE_POLICY,
        "runtime-data climb_temperature_policy does not match the source-backed SR22 G6 policy",
    )
    _require(
        _positive_int(
            performance.get("climb_row_count"),
            "runtime-data climb_row_count",
        )
        == climb_rows,
        "runtime-data climb row count does not match the packaged CSV",
    )
    _require(
        _positive_int(
            performance.get("cruise_row_count"),
            "runtime-data cruise_row_count",
        )
        == cruise_rows,
        "runtime-data cruise row count does not match the packaged CSV",
    )
    return report


def _validate_provenance(value: Any, label: str) -> None:
    provenance = _require_mapping(value, f"{label} provenance")
    urls = _require_sequence(provenance.get("source_urls"), f"{label} source_urls")
    _require(urls, f"{label} source_urls is empty")
    _require(
        all(isinstance(url, str) and url.startswith(("http://", "https://")) for url in urls),
        f"{label} source_urls contains an invalid URL",
    )
    hashes = _require_mapping(
        provenance.get("source_hashes"),
        f"{label} source_hashes",
    )
    _require(
        set(map(str, urls)) == set(map(str, hashes)),
        f"{label} source URL/hash keys do not match",
    )
    for source_url, digest in hashes.items():
        _require_sha256(digest, f"{label} source hash for {source_url}")
    _verified_text(provenance.get("initial_time_utc"), f"{label} initial_time_utc")
    _verified_text(
        provenance.get("interpolation_method"),
        f"{label} interpolation_method",
    )
    _require(
        bool(_require_mapping(provenance.get("trace"), f"{label} trace")),
        f"{label} trace is empty",
    )


def _validate_real_msm_acceptance(path: Path) -> Mapping[str, Any]:
    report = _load_json(path, "real-MSM acceptance report")
    _require(report.get("schema_version") == 1, "real-MSM report schema mismatch")
    _require(report.get("status") == "PASS", "real-MSM acceptance did not PASS")
    _require(report.get("mode") == "LIVE", "real-MSM acceptance was not LIVE")
    _require(
        report.get("live_executed") is True,
        "real-MSM acceptance did not execute live retrieval",
    )
    package = _require_mapping(report.get("package"), "real-MSM package")
    _require(
        package.get("required_version") == MSM_PACKAGE_VERSION,
        "real-MSM required package version mismatch",
    )
    versions = _require_mapping(
        package.get("observed_versions"),
        "real-MSM observed versions",
    )
    for label in ("distribution", "module", "adapter"):
        _require(
            versions.get(label) == MSM_PACKAGE_VERSION,
            f"real-MSM observed {label} version mismatch",
        )
    forecast = _require_mapping(report.get("forecast"), "real-MSM forecast")
    results = _require_sequence(forecast.get("results"), "real-MSM forecast results")
    _require(
        _positive_int(forecast.get("result_count"), "real-MSM result_count") == len(results),
        "real-MSM result_count does not match results",
    )
    expected_request_ids = {
        f"{airport_id}-{kind}"
        for airport_id in REQUIRED_AIRPORT_IDS
        for kind in ("aloft", "surface-temperature")
    }
    observed_request_ids: set[str] = set()
    for index, raw_result in enumerate(results):
        result = _require_mapping(raw_result, f"real-MSM result {index}")
        request_id = result.get("request_id")
        _require(isinstance(request_id, str), f"real-MSM result {index} has no request_id")
        _require(
            request_id not in observed_request_ids,
            f"real-MSM report contains duplicate result: {request_id}",
        )
        observed_request_ids.add(request_id)
        _require(
            result.get("availability") == "AVAILABLE",
            f"real-MSM result is unavailable: {request_id}",
        )
        _validate_provenance(result.get("provenance"), request_id)
    _require(
        observed_request_ids == expected_request_ids,
        "real-MSM report result IDs do not match RJFM/RJFO wind and temperature probes",
    )
    return report


def _validate_release_tree(root: Path, version: str) -> dict[str, Any]:
    _require(_VERSION.fullmatch(version) is not None, "version must be X.Y.Z")
    _require(root.exists(), f"release directory is absent: {root}")
    _require(root.is_dir(), f"release path is not a directory: {root}")
    _require(not root.is_symlink(), "release directory must not be a symbolic link")
    for path in root.rglob("*"):
        _require(
            not path.is_symlink(),
            f"release tree contains a symbolic link: {path.relative_to(root)}",
        )
        if path.is_file() and path.name != MANIFEST_NAME:
            _require(
                path.stat().st_size > 0,
                f"release tree contains an empty file: {path.relative_to(root)}",
            )
            _safe_posix_path(
                path.relative_to(root).as_posix(),
                f"release path {path.relative_to(root)!s}",
            )

    wheels_root = root / "wheels"
    _require(wheels_root.is_dir(), f"wheel directory is absent: {wheels_root}")
    autonavlog_wheel = _find_required_wheel(
        wheels_root,
        filename_prefix="autonavlog",
        distribution="autonavlog",
        version=version,
    )
    msm_wheel = _find_required_wheel(
        wheels_root,
        filename_prefix="jma_msm_wind",
        distribution="jma-msm-wind",
        version=MSM_PACKAGE_VERSION,
    )
    all_wheels = sorted(wheels_root.glob("*.whl"))
    _require(
        all_wheels == sorted((autonavlog_wheel, msm_wheel)),
        "wheel directory must contain only the required AutoNavLog and jma-msm-wind wheels",
    )
    _require(
        sorted(root.rglob("*.whl")) == all_wheels,
        "wheel files must appear only at the top level of the wheel directory",
    )

    data_root = root / "data" / "autonavlog"
    airport_rows = _validate_reference_pack(data_root / "reference" / "default")
    climb_rows, cruise_rows = _validate_performance(data_root / "performance")
    runtime_report_path = _safe_child(
        root,
        RUNTIME_ACCEPTANCE_PATH,
        "runtime-data acceptance path",
    )
    real_msm_report_path = _safe_child(
        root,
        REAL_MSM_ACCEPTANCE_PATH,
        "real-MSM acceptance path",
    )
    _validate_runtime_acceptance(
        runtime_report_path,
        airport_rows=airport_rows,
        climb_rows=climb_rows,
        cruise_rows=cruise_rows,
    )
    _validate_real_msm_acceptance(real_msm_report_path)
    return {
        "runtime_data": {
            "path": str(RUNTIME_ACCEPTANCE_PATH),
            "sha256": _sha256(runtime_report_path),
            "status": "PASS",
        },
        "real_msm": {
            "path": str(REAL_MSM_ACCEPTANCE_PATH),
            "sha256": _sha256(real_msm_report_path),
            "status": "PASS",
            "mode": "LIVE",
        },
    }


def _detect_source_commit(explicit: str | None) -> str | None:
    candidates = [explicit, os.environ.get("GITHUB_SHA")]
    for candidate in candidates:
        if candidate:
            normalized = candidate.strip().lower()
            _require(
                _GIT_COMMIT.fullmatch(normalized) is not None,
                "source commit must be a 40- or 64-character hexadecimal Git object ID",
            )
            return normalized
    repository = Path(__file__).resolve().parents[1]
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode == 0:
        normalized = completed.stdout.strip().lower()
        if _GIT_COMMIT.fullmatch(normalized) is not None:
            return normalized
    return None


def build_release_manifest(
    release_directory: str | Path,
    *,
    version: str,
    source_commit: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    root = Path(release_directory)
    manifest_path = root / MANIFEST_NAME
    _require(
        not manifest_path.is_symlink(),
        f"{MANIFEST_NAME} must not be a symbolic link",
    )
    acceptance = _validate_release_tree(root, version)
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path == manifest_path:
            continue
        relative = _safe_posix_path(
            path.relative_to(root).as_posix(),
            f"manifest file path {path.relative_to(root)!s}",
        )
        files.append(
            {
                "path": str(relative),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    _require(files, "release contains no files")
    timestamp = created_at or datetime.now(timezone.utc)
    _require(timestamp.tzinfo is not None, "created_at must be timezone-aware")
    commit = _detect_source_commit(source_commit)
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "release_version": version,
        "autonavlog_version": version,
        "msm_package_version": MSM_PACKAGE_VERSION,
        "msm_commit": MSM_COMMIT,
        "created_at": timestamp.astimezone(timezone.utc).isoformat(),
        "acceptance": acceptance,
        "files": files,
    }
    if commit is not None:
        manifest["source_commit"] = commit
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{MANIFEST_NAME}.",
        suffix=".tmp",
        dir=root,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a complete release tree and write its SHA-256 manifest."
    )
    parser.add_argument("release_directory", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--source-commit",
        help="Git commit for this release; defaults to GITHUB_SHA or the checkout HEAD",
    )
    args = parser.parse_args()
    try:
        build_release_manifest(
            args.release_directory,
            version=args.version,
            source_commit=args.source_commit,
        )
    except ReleaseManifestError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
