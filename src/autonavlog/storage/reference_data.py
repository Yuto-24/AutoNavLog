from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from autonavlog.application.fingerprints import make_fingerprint
from autonavlog.domain.planning import (
    AirportSelection,
    CheckPointSelection,
    MasterReference,
    PatternAltitudeValidationStatus,
    PointSelection,
    ReferenceDataSnapshot,
    Sha256Hex,
)


class ReferenceDataError(ValueError):
    pass


class ReferenceModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )


ReferenceKind = Literal["AIRPORT", "POINT", "CHECK_POINT"]
ReferenceRow = AirportSelection | PointSelection | CheckPointSelection
ReferenceModelT = TypeVar("ReferenceModelT", bound=BaseModel)


class ReferenceTableManifest(ReferenceModel):
    kind: ReferenceKind
    path: str = Field(min_length=1)
    sha256: Sha256Hex
    row_count: int = Field(ge=0)


class ReferenceManifest(ReferenceModel):
    schema_version: Literal[1] = 1
    dataset_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    created_at_utc: datetime
    tables: list[ReferenceTableManifest]

    @field_validator("created_at_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at_utc must be timezone-aware UTC")
        return value


class ActiveReferencePointer(ReferenceModel):
    schema_version: Literal[1] = 1
    dataset_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    previous_dataset_id: str | None = None
    previous_revision: str | None = None


@dataclass(frozen=True)
class ReferenceCatalog:
    manifest: ReferenceManifest
    root: Path
    airports: dict[str, AirportSelection]
    points: dict[str, PointSelection]
    check_points: dict[str, CheckPointSelection]

    def snapshot(
        self,
        *,
        departure_airport_id: str,
        destination_airport_id: str,
        route_points: dict[UUID, str] | None = None,
        check_points: dict[UUID, str] | None = None,
    ) -> ReferenceDataSnapshot:
        try:
            departure = self.airports[departure_airport_id]
            destination = self.airports[destination_airport_id]
            selected_points = {
                node_id: self.points[row_id] for node_id, row_id in (route_points or {}).items()
            }
            selected_check_points = {
                reference_id: self.check_points[row_id]
                for reference_id, row_id in (check_points or {}).items()
            }
        except KeyError as error:
            raise ReferenceDataError(
                f"selected reference row is unavailable: {error.args[0]}"
            ) from error
        if (
            destination.pattern_altitude_validation_status
            != PatternAltitudeValidationStatus.VERIFIED
        ):
            raise ReferenceDataError("destination pattern altitude is not verified")
        return ReferenceDataSnapshot(
            departure_airport=departure,
            destination_airport=destination,
            route_points=selected_points,
            check_points=selected_check_points,
        )


@dataclass(frozen=True)
class ReferenceCatalogDiff:
    added: dict[ReferenceKind, tuple[str, ...]]
    updated: dict[ReferenceKind, tuple[str, ...]]
    deleted: dict[ReferenceKind, tuple[str, ...]]

    @property
    def is_empty(self) -> bool:
        return not any((*self.added.values(), *self.updated.values(), *self.deleted.values()))


def diff_reference_catalogs(
    before: ReferenceCatalog,
    after: ReferenceCatalog,
) -> ReferenceCatalogDiff:
    before_tables: dict[ReferenceKind, Mapping[str, ReferenceRow]] = {
        "AIRPORT": before.airports,
        "POINT": before.points,
        "CHECK_POINT": before.check_points,
    }
    after_tables: dict[ReferenceKind, Mapping[str, ReferenceRow]] = {
        "AIRPORT": after.airports,
        "POINT": after.points,
        "CHECK_POINT": after.check_points,
    }
    added: dict[ReferenceKind, tuple[str, ...]] = {}
    updated: dict[ReferenceKind, tuple[str, ...]] = {}
    deleted: dict[ReferenceKind, tuple[str, ...]] = {}
    for kind in _EXPECTED_PATHS:
        old = before_tables[kind]
        new = after_tables[kind]
        added[kind] = tuple(sorted(set(new) - set(old)))
        deleted[kind] = tuple(sorted(set(old) - set(new)))
        updated[kind] = tuple(
            sorted(
                row_id
                for row_id in set(old) & set(new)
                if old[row_id].canonical_row() != new[row_id].canonical_row()
            )
        )
    return ReferenceCatalogDiff(added=added, updated=updated, deleted=deleted)


_EXPECTED_PATHS: dict[ReferenceKind, str] = {
    "AIRPORT": "airports.csv",
    "POINT": "points.csv",
    "CHECK_POINT": "checkpoints.csv",
}

_AIRPORT_FIELDS = [
    "id",
    "icao",
    "name",
    "latitude_deg",
    "longitude_deg",
    "elevation_ft_msl",
    "pattern_altitude_ft_msl",
    "pattern_altitude_source",
    "pattern_altitude_source_revision",
    "pattern_altitude_validation_status",
    "source",
    "source_revision",
]
_POINT_FIELDS = [
    "id",
    "name",
    "latitude_deg",
    "longitude_deg",
    "point_role",
    "source",
    "source_revision",
    "notes",
]
_CHECK_POINT_FIELDS = [
    "id",
    "name",
    "latitude_deg",
    "longitude_deg",
    "source",
    "source_revision",
    "notes",
]


def _reject_constant(value: str) -> Any:
    raise ReferenceDataError(f"non-finite JSON value is not allowed: {value}")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ReferenceDataError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _strict_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReferenceDataError(f"invalid JSON file: {path}") from error


def _validate_json_model(
    model: type[ReferenceModelT],
    path: Path,
) -> ReferenceModelT:
    payload = _strict_json(path)
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return model.model_validate_json(serialized)
    except (TypeError, ValueError, ValidationError) as error:
        raise ReferenceDataError(f"invalid JSON model: {path}") from error


def _safe_component(value: str, label: str) -> str:
    if (
        not value
        or value != value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ReferenceDataError(f"unsafe {label}: {value!r}")
    return value


def _safe_table_path(root: Path, relative: str, expected: str) -> Path:
    normalized = PurePosixPath(relative)
    if (
        normalized.is_absolute()
        or ".." in normalized.parts
        or "\\" in relative
        or normalized.as_posix() != expected
    ):
        raise ReferenceDataError(f"unsafe reference table path: {relative}")
    path = root / expected
    if os.path.commonpath((root.resolve(), path.resolve())) != str(root.resolve()):
        raise ReferenceDataError(f"reference table escapes pack root: {relative}")
    return path


def _csv_rows(
    content: bytes,
    path: Path,
    expected_fields: list[str],
) -> list[dict[str, str]]:
    try:
        text = content.decode("utf-8")
    except UnicodeError as error:
        raise ReferenceDataError(f"cannot decode reference table: {path}") from error
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != expected_fields:
        raise ReferenceDataError(
            f"reference table header mismatch for {path.name}: {reader.fieldnames}"
        )
    rows = list(reader)
    if any(None in row for row in rows):
        raise ReferenceDataError(f"reference table has extra columns: {path.name}")
    return rows


def _validate_row(
    model: type[ReferenceModelT],
    row: dict[str, str],
) -> ReferenceModelT:
    try:
        return model.model_validate_strings(row, strict=True)
    except ValidationError as error:
        raise ReferenceDataError(f"invalid reference row: {error}") from error


def _origin(
    manifest: ReferenceManifest,
    *,
    kind: ReferenceKind,
    row: ReferenceRow,
) -> MasterReference:
    canonical = row.model_dump(mode="python", exclude={"origin"})
    return MasterReference(
        dataset_id=manifest.dataset_id,
        dataset_revision=manifest.revision,
        entity_kind=kind,
        entity_id=row.id,
        row_fingerprint=make_fingerprint(
            kind="reference_row",
            fields={"entity_kind": kind, "row": canonical},
        ),
    )


def _index_unique(
    rows: Sequence[ReferenceModelT],
    label: str,
) -> dict[str, ReferenceModelT]:
    output: dict[str, ReferenceModelT] = {}
    for row in rows:
        row_id = getattr(row, "id", None)
        if not isinstance(row_id, str) or not row_id:
            raise ReferenceDataError(f"{label} row has no id")
        if row_id in output:
            raise ReferenceDataError(f"duplicate {label} id: {row_id}")
        output[row_id] = row
    return output


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _csv_bytes(fields: list[str], rows: Sequence[BaseModel]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=fields,
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        payload = row.model_dump(mode="json", exclude={"origin"})
        writer.writerow({field: payload[field] for field in fields})
    return buffer.getvalue().encode("utf-8")


class ReferenceDataCatalogRepository:
    def __init__(
        self,
        root: str | Path,
        *,
        bundled_default: str | Path | None = None,
    ):
        self.root = Path(root)
        self.bundled_default = None if bundled_default is None else Path(bundled_default)

    @property
    def active_path(self) -> Path:
        return self.root / "active.json"

    def open_active(self) -> ReferenceCatalog:
        if self.active_path.exists():
            try:
                pointer = _validate_json_model(
                    ActiveReferencePointer,
                    self.active_path,
                )
            except ReferenceDataError as error:
                raise ReferenceDataError("invalid active reference pointer") from error
            dataset_id = _safe_component(pointer.dataset_id, "dataset id")
            revision = _safe_component(pointer.revision, "revision")
            directory = self.root / "catalogs" / dataset_id / revision
            return self.open_pack(directory)
        if self.bundled_default is None:
            raise ReferenceDataError("no active or bundled reference pack")
        return self.open_pack(self.bundled_default)

    def open_pack(self, directory: str | Path) -> ReferenceCatalog:
        root = Path(directory)
        try:
            manifest = _validate_json_model(
                ReferenceManifest,
                root / "reference-manifest.json",
            )
        except ReferenceDataError as error:
            raise ReferenceDataError("invalid reference manifest") from error
        tables: dict[str, ReferenceTableManifest] = {}
        for table in manifest.tables:
            if table.kind in tables:
                raise ReferenceDataError(f"duplicate reference table kind: {table.kind}")
            tables[table.kind] = table
        if set(tables) != set(_EXPECTED_PATHS):
            raise ReferenceDataError("manifest must declare all reference tables")

        parsed: dict[ReferenceKind, list[BaseModel]] = {}
        definitions: dict[
            ReferenceKind,
            tuple[type[BaseModel], list[str]],
        ] = {
            "AIRPORT": (AirportSelection, _AIRPORT_FIELDS),
            "POINT": (PointSelection, _POINT_FIELDS),
            "CHECK_POINT": (CheckPointSelection, _CHECK_POINT_FIELDS),
        }
        for kind, (model, fields) in definitions.items():
            table = tables[kind]
            path = _safe_table_path(root, table.path, _EXPECTED_PATHS[kind])
            try:
                content = path.read_bytes()
            except OSError as error:
                raise ReferenceDataError(f"cannot read reference table: {path}") from error
            if hashlib.sha256(content).hexdigest() != table.sha256:
                raise ReferenceDataError(f"SHA-256 mismatch for {path.name}")
            rows = [
                _validate_row(model, row)
                for row in _csv_rows(content, path, fields)
            ]
            if len(rows) != table.row_count:
                raise ReferenceDataError(f"row count mismatch for {path.name}")
            parsed[kind] = rows

        raw_airports = _index_unique(parsed["AIRPORT"], "airport")
        raw_points = _index_unique(parsed["POINT"], "point")
        raw_check_points = _index_unique(parsed["CHECK_POINT"], "check point")
        airports = {
            row_id: row.model_copy(update={"origin": _origin(manifest, kind="AIRPORT", row=row)})
            for row_id, row in raw_airports.items()
            if isinstance(row, AirportSelection)
        }
        points = {
            row_id: row.model_copy(update={"origin": _origin(manifest, kind="POINT", row=row)})
            for row_id, row in raw_points.items()
            if isinstance(row, PointSelection)
        }
        check_points = {
            row_id: row.model_copy(
                update={
                    "origin": _origin(
                        manifest,
                        kind="CHECK_POINT",
                        row=row,
                    )
                }
            )
            for row_id, row in raw_check_points.items()
            if isinstance(row, CheckPointSelection)
        }
        return ReferenceCatalog(
            manifest=manifest,
            root=root,
            airports=airports,
            points=points,
            check_points=check_points,
        )

    def publish_revision(
        self,
        *,
        dataset_id: str,
        revision: str,
        airports: list[AirportSelection],
        points: list[PointSelection],
        check_points: list[CheckPointSelection],
        activate: bool = True,
    ) -> ReferenceCatalog:
        dataset_id = _safe_component(dataset_id, "dataset id")
        revision = _safe_component(revision, "revision")
        target = self.root / "catalogs" / dataset_id / revision
        if target.exists():
            raise FileExistsError("reference revisions are immutable")
        target.mkdir(parents=True, exist_ok=False)
        try:
            contents = {
                "AIRPORT": _csv_bytes(_AIRPORT_FIELDS, airports),
                "POINT": _csv_bytes(_POINT_FIELDS, points),
                "CHECK_POINT": _csv_bytes(
                    _CHECK_POINT_FIELDS,
                    check_points,
                ),
            }
            tables: list[ReferenceTableManifest] = []
            for kind, relative in _EXPECTED_PATHS.items():
                data = contents[kind]
                _atomic_write(target / relative, data)
                tables.append(
                    ReferenceTableManifest(
                        kind=kind,
                        path=relative,
                        sha256=hashlib.sha256(data).hexdigest(),
                        row_count={
                            "AIRPORT": len(airports),
                            "POINT": len(points),
                            "CHECK_POINT": len(check_points),
                        }[kind],
                    )
                )
            manifest = ReferenceManifest(
                dataset_id=dataset_id,
                revision=revision,
                created_at_utc=datetime.now(UTC),
                tables=tables,
            )
            manifest_bytes = (
                json.dumps(
                    manifest.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            _atomic_write(target / "reference-manifest.json", manifest_bytes)
            catalog = self.open_pack(target)
            if activate:
                self.activate(dataset_id, revision)
            return catalog
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    def activate(self, dataset_id: str, revision: str) -> ReferenceCatalog:
        dataset_id = _safe_component(dataset_id, "dataset id")
        revision = _safe_component(revision, "revision")
        target = self.root / "catalogs" / dataset_id / revision
        catalog = self.open_pack(target)
        previous_dataset: str | None = None
        previous_revision: str | None = None
        if self.active_path.exists():
            try:
                previous = _validate_json_model(
                    ActiveReferencePointer,
                    self.active_path,
                )
            except ReferenceDataError as error:
                raise ReferenceDataError("invalid active reference pointer") from error
            previous_dataset = previous.dataset_id
            previous_revision = previous.revision
        pointer = ActiveReferencePointer(
            dataset_id=dataset_id,
            revision=revision,
            previous_dataset_id=previous_dataset,
            previous_revision=previous_revision,
        )
        _atomic_write(
            self.active_path,
            (
                json.dumps(
                    pointer.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8"),
        )
        return catalog

    def rollback(self) -> ReferenceCatalog:
        try:
            active = _validate_json_model(
                ActiveReferencePointer,
                self.active_path,
            )
        except ReferenceDataError as error:
            raise ReferenceDataError("active reference pointer is unavailable") from error
        if active.previous_dataset_id is None or active.previous_revision is None:
            raise ReferenceDataError("no previous reference revision is available")
        return self.activate(
            active.previous_dataset_id,
            active.previous_revision,
        )

    def import_pack(
        self,
        directory: str | Path,
        *,
        activate: bool = False,
    ) -> ReferenceCatalog:
        source = self.open_pack(directory)
        return self.publish_revision(
            dataset_id=source.manifest.dataset_id,
            revision=source.manifest.revision,
            airports=list(source.airports.values()),
            points=list(source.points.values()),
            check_points=list(source.check_points.values()),
            activate=activate,
        )

    def list_revisions(self) -> tuple[tuple[str, str], ...]:
        catalogs_root = self.root / "catalogs"
        if not catalogs_root.exists():
            return ()
        revisions: list[tuple[str, str]] = []
        for dataset_directory in sorted(catalogs_root.iterdir(), key=lambda path: path.name):
            if dataset_directory.is_symlink() or not dataset_directory.is_dir():
                raise ReferenceDataError("unsafe entry in reference catalog root")
            dataset_id = _safe_component(dataset_directory.name, "dataset id")
            for revision_directory in sorted(
                dataset_directory.iterdir(), key=lambda path: path.name
            ):
                if revision_directory.is_symlink() or not revision_directory.is_dir():
                    raise ReferenceDataError("unsafe entry in reference revision root")
                revision = _safe_component(revision_directory.name, "revision")
                catalog = self.open_pack(revision_directory)
                if (
                    catalog.manifest.dataset_id != dataset_id
                    or catalog.manifest.revision != revision
                ):
                    raise ReferenceDataError("reference directory and manifest identity differ")
                revisions.append((dataset_id, revision))
        return tuple(revisions)

    def open_revision(self, dataset_id: str, revision: str) -> ReferenceCatalog:
        dataset_id = _safe_component(dataset_id, "dataset id")
        revision = _safe_component(revision, "revision")
        return self.open_pack(self.root / "catalogs" / dataset_id / revision)

    def export_pack(
        self,
        destination: str | Path,
        *,
        dataset_id: str | None = None,
        revision: str | None = None,
    ) -> Path:
        if (dataset_id is None) != (revision is None):
            raise ReferenceDataError("dataset id and revision must be supplied together")
        catalog = (
            self.open_active()
            if dataset_id is None
            else self.open_revision(dataset_id, revision or "")
        )
        target = Path(destination)
        if target.exists():
            raise FileExistsError("reference export destination already exists")
        target.mkdir(parents=True, exist_ok=False)
        try:
            for relative in (*_EXPECTED_PATHS.values(), "reference-manifest.json"):
                source = catalog.root / relative
                if source.is_symlink() or not source.is_file():
                    raise ReferenceDataError("unsafe file in reference pack")
                _atomic_write(target / relative, source.read_bytes())
            self.open_pack(target)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return target

    def revise_active(
        self,
        *,
        revision: str,
        dataset_id: str | None = None,
        upsert_airports: Sequence[AirportSelection] = (),
        upsert_points: Sequence[PointSelection] = (),
        upsert_check_points: Sequence[CheckPointSelection] = (),
        delete_airport_ids: Sequence[str] = (),
        delete_point_ids: Sequence[str] = (),
        delete_check_point_ids: Sequence[str] = (),
        activate: bool = True,
    ) -> tuple[ReferenceCatalog, ReferenceCatalogDiff]:
        active = self.open_active()
        target_dataset = dataset_id or active.manifest.dataset_id
        airports = dict(active.airports)
        points = dict(active.points)
        check_points = dict(active.check_points)

        def delete_rows(table: dict[str, ReferenceModelT], row_ids: Sequence[str]) -> None:
            for row_id in row_ids:
                if row_id not in table:
                    raise ReferenceDataError(f"reference row is unavailable: {row_id}")
                del table[row_id]

        delete_rows(airports, delete_airport_ids)
        delete_rows(points, delete_point_ids)
        delete_rows(check_points, delete_check_point_ids)
        airports.update({row.id: row for row in upsert_airports})
        points.update({row.id: row for row in upsert_points})
        check_points.update({row.id: row for row in upsert_check_points})
        revised = self.publish_revision(
            dataset_id=target_dataset,
            revision=revision,
            airports=list(airports.values()),
            points=list(points.values()),
            check_points=list(check_points.values()),
            activate=activate,
        )
        return revised, diff_reference_catalogs(active, revised)
