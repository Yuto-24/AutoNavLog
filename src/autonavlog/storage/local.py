from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from autonavlog.domain.project import Project

from .repository import (
    LastCalculationRecord,
    OwnerProjectState,
    ProjectIndex,
    ProjectLoadResult,
    ProjectSummary,
    SaveResult,
)
from .safe_json import (
    JsonStorageError,
    atomic_model_write,
    parse_json_bytes,
    read_json_model,
    validate_json_bytes,
)


class RevisionConflictError(RuntimeError):
    def __init__(self, message: str, conflict_copy: Path):
        super().__init__(message)
        self.conflict_copy = conflict_copy


class UnsafeStoragePathError(JsonStorageError):
    """Raised before following a symlink or leaving the configured storage root."""


LOGGER = logging.getLogger(__name__)


def _normalize_legacy_wind_directions(project_payload: dict[str, Any]) -> bool:
    migrated = False
    ftd_weather = project_payload.get("ftd_weather")
    wind_records: list[Any] = []
    if isinstance(ftd_weather, dict):
        wind_records.extend(
            (ftd_weather.get("surface_wind"), ftd_weather.get("wind_at_5000_ft"))
        )
    sections = project_payload.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            if section.get("manual_wind_direction_deg") == 0:
                section["manual_wind_direction_deg"] = 360
                migrated = True
            by_phase = section.get("manual_wind_by_phase")
            if isinstance(by_phase, dict):
                wind_records.extend(by_phase.values())
    for wind in wind_records:
        if isinstance(wind, dict) and wind.get("direction_deg_from") == 0:
            wind["direction_deg_from"] = 360
            migrated = True
    return migrated


def _migrate_project_payload(payload: Any) -> tuple[Any, bool]:
    if not isinstance(payload, dict):
        return payload, False
    migrated = False
    if payload.pop("manual_qnh_hpa", None) is not None:
        migrated = True
    if payload.get("schema_version") != 4:
        payload["schema_version"] = 4
        migrated = True
    for field in ("run_up_included", "air_conditioning_enabled"):
        if field not in payload:
            payload[field] = True
            migrated = True
    if "nose_fairing_enabled" not in payload:
        payload["nose_fairing_enabled"] = False
        migrated = True
    if "descent_rate_fpm" not in payload:
        payload["descent_rate_fpm"] = 500
        migrated = True
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        if "snapshot_effective_issues" in metadata:
            metadata.pop("snapshot_effective_issues", None)
            migrated = True
        ui_state = metadata.get("ui_state")
        if isinstance(ui_state, dict) and "manual_qnh_fingerprint" in ui_state:
            ui_state.pop("manual_qnh_fingerprint", None)
            migrated = True
    for collection, obsolete_fields in (
        ("route_nodes", ("project_id",)),
        ("visual_references", ("project_id", "along_track_fraction")),
        (
            "sections",
            ("project_id", "safe_enroute_altitude_ft_msl", "loss_time_seconds", "notes"),
        ),
    ):
        values = payload.get(collection)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict):
                continue
            for field in obsolete_fields:
                if field in value:
                    value.pop(field, None)
                    migrated = True
    return payload, _normalize_legacy_wind_directions(payload) or migrated


def _read_migrated_project(path: Path) -> Project:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise JsonStorageError(f"cannot read JSON file: {path}") from error
    payload, _ = _migrate_project_payload(parse_json_bytes(raw))
    try:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise JsonStorageError("cannot migrate legacy project JSON") from error
    return validate_json_bytes((serialized + "\n").encode("utf-8"), Project)


class LocalProjectRepository:
    def __init__(self, root: str | Path):
        self.root = Path(root).absolute()
        self._lock = RLock()

    def _safe_path(self, path: Path) -> Path:
        candidate = path.absolute()
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as error:
            raise UnsafeStoragePathError("storage path leaves configured root") from error
        current = self.root
        if current.exists() and current.is_symlink():
            raise UnsafeStoragePathError("storage root must not be a symlink")
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise UnsafeStoragePathError(f"storage path must not contain symlinks: {current}")
        return candidate

    def _project_dir(self, project_id: UUID) -> Path:
        return self._safe_path(self.root / "projects" / str(project_id))

    @property
    def index_path(self) -> Path:
        return self._safe_path(self.root / "projects" / "index.json")

    def _project_path(self, project_id: UUID, filename: str) -> Path:
        return self._safe_path(self._project_dir(project_id) / filename)

    def _owner_state_path(self, owner_key: str) -> Path:
        if len(owner_key) != 64 or any(
            character not in "0123456789abcdef" for character in owner_key
        ):
            raise ValueError("owner key must be a lowercase SHA-256 digest")
        return self._safe_path(self.root / "owners" / owner_key / "state.json")

    @staticmethod
    def _summary(project: Project) -> ProjectSummary:
        stored_owner = project.metadata.get("web_owner_id")
        web_owner_id = stored_owner if isinstance(stored_owner, str) else None
        return ProjectSummary(
            id=project.id,
            name=project.name,
            updated_at=project.updated_at,
            status=project.status,
            revision=project.revision,
            web_owner_id=web_owner_id,
        )

    @staticmethod
    def _sort_summaries(summaries: list[ProjectSummary]) -> list[ProjectSummary]:
        return sorted(
            summaries,
            key=lambda item: (item.updated_at, str(item.id)),
            reverse=True,
        )

    def _scan_projects(self) -> list[ProjectSummary]:
        projects_root = self._safe_path(self.root / "projects")
        summaries: list[ProjectSummary] = []
        if not projects_root.exists():
            return summaries
        for project_dir in sorted(projects_root.iterdir()):
            if not project_dir.is_dir() or project_dir.is_symlink():
                continue
            try:
                project_id = UUID(project_dir.name)
            except ValueError:
                continue
            if str(project_id) != project_dir.name:
                continue
            paths = (
                self._project_path(project_id, "autosave.json"),
                self._project_path(project_id, "project.json"),
                self._project_path(project_id, "project.json.bak"),
            )
            project = None
            for path in paths:
                if not path.exists():
                    continue
                try:
                    project = _read_migrated_project(path)
                    self._validate_project_path_identity(project, project_id)
                except JsonStorageError:
                    project = None
                    continue
                break
            if project is None:
                continue
            summaries.append(self._summary(project))
        return self._sort_summaries(summaries)

    def _write_index(self, projects: list[ProjectSummary]) -> None:
        atomic_model_write(
            self.index_path,
            ProjectIndex(projects=projects),
        )

    def _rebuild_index(self) -> list[ProjectSummary]:
        projects = self._scan_projects()
        self._write_index(projects)
        return projects

    def _read_or_rebuild_index(self) -> list[ProjectSummary]:
        if self.index_path.exists():
            try:
                index = read_json_model(self.index_path, ProjectIndex)
                return index.projects
            except JsonStorageError:
                pass
        return self._rebuild_index()

    def _update_index(self, project: Project) -> None:
        projects = [
            summary for summary in self._read_or_rebuild_index() if summary.id != project.id
        ]
        projects.append(self._summary(project))
        self._write_index(self._sort_summaries(projects))

    def list_projects(self) -> list[ProjectSummary]:
        with self._lock:
            return [
                summary
                for summary in self._read_or_rebuild_index()
                if self._project_dir(summary.id).is_dir()
                and not self._project_dir(summary.id).is_symlink()
                and any(
                    self._project_path(summary.id, filename).is_file()
                    for filename in ("autosave.json", "project.json", "project.json.bak")
                )
            ]

    @staticmethod
    def _validate_project_path_identity(project: Project, project_id: UUID) -> None:
        if project.id != project_id:
            raise JsonStorageError("project id does not match its storage path")

    def load(self, project_id: UUID) -> Project:
        return self.load_with_recovery(project_id).project

    def load_with_recovery(self, project_id: UUID) -> ProjectLoadResult:
        with self._lock:
            return self._load_with_recovery_locked(project_id)

    def _load_with_recovery_locked(self, project_id: UUID) -> ProjectLoadResult:
        autosave_path = self._project_path(project_id, "autosave.json")
        path = self._project_path(project_id, "project.json")
        project = None
        recovered_from_fallback = False
        if autosave_path.exists():
            try:
                project = _read_migrated_project(autosave_path)
                self._validate_project_path_identity(project, project_id)
            except UnsafeStoragePathError:
                raise
            except JsonStorageError:
                project = None
                LOGGER.warning("Ignoring corrupt Project autosave: %s", autosave_path)
                recovered_from_fallback = True
        if project is None:
            try:
                project = _read_migrated_project(path)
                self._validate_project_path_identity(project, project_id)
            except UnsafeStoragePathError:
                raise
            except JsonStorageError:
                recovered_from_fallback = True
                project = _read_migrated_project(
                    self._project_path(project_id, "project.json.bak")
                )
                self._validate_project_path_identity(project, project_id)
        if recovered_from_fallback:
            self._update_index(project)
        return ProjectLoadResult(
            project=project,
            recovered_from_fallback=recovered_from_fallback,
        )

    def save(self, project: Project, expected_revision: int) -> SaveResult:
        with self._lock:
            return self._save_locked(project, expected_revision)

    def _save_locked(self, project: Project, expected_revision: int) -> SaveResult:
        path = self._project_path(project.id, "project.json")
        if path.exists():
            existing = _read_migrated_project(path)
            if existing.revision != expected_revision:
                stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
                while True:
                    token = uuid4().hex
                    conflict = path.with_name(f"project-conflict-{stamp}-{token}.json")
                    if not conflict.exists():
                        break
                atomic_model_write(conflict, project, keep_backup=False)
                raise RevisionConflictError(
                    f"expected revision {expected_revision}, found {existing.revision}",
                    conflict,
                )
        elif expected_revision != 0:
            raise ValueError("new project must be saved with expected revision 0")
        saved = project.model_copy(
            update={
                "revision": expected_revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        atomic_model_write(path, saved)
        self._update_index(saved)
        return SaveResult(project=saved, path=path)

    def autosave(self, project: Project) -> Path:
        with self._lock:
            path = self._project_path(project.id, "autosave.json")
            atomic_model_write(path, project)
            self._update_index(project)
            return path

    def replace_last_calculation(self, record: LastCalculationRecord) -> Path:
        with self._lock:
            self._validate_project_path_identity(record.project, record.project_id)
            path = self._project_path(record.project_id, "last-calculation.json")
            atomic_model_write(path, record, keep_backup=False)
            return path

    def load_last_calculation(
        self,
        project_id: UUID,
        *,
        owner_key: str,
    ) -> LastCalculationRecord | None:
        with self._lock:
            path = self._project_path(project_id, "last-calculation.json")
            if not path.exists():
                return None
            try:
                record = read_json_model(path, LastCalculationRecord)
                if record.project_id != project_id:
                    raise JsonStorageError("last calculation Project id mismatch")
                if record.owner_key != owner_key:
                    raise JsonStorageError("last calculation owner mismatch")
                return record
            except UnsafeStoragePathError:
                raise
            except JsonStorageError:
                quarantine = self._project_path(
                    project_id,
                    "last-calculation.invalid.json",
                )
                os.replace(path, quarantine)
                LOGGER.warning("Quarantined invalid last calculation: %s", path)
                raise

    def load_owner_project(self, owner_key: str) -> UUID | None:
        with self._lock:
            path = self._owner_state_path(owner_key)
            if not path.exists():
                return None
            return read_json_model(path, OwnerProjectState).project_id

    def set_owner_project(self, owner_key: str, project_id: UUID) -> Path:
        with self._lock:
            path = self._owner_state_path(owner_key)
            atomic_model_write(
                path,
                OwnerProjectState(project_id=project_id),
                keep_backup=False,
            )
            return path

    def clear_owner_project(
        self,
        owner_key: str,
        project_id: UUID | None = None,
    ) -> None:
        with self._lock:
            path = self._owner_state_path(owner_key)
            if not path.exists():
                return
            state = read_json_model(path, OwnerProjectState)
            if project_id is None or state.project_id == project_id:
                path.unlink()

    def _delete_locked(self, project_id: UUID) -> None:
        project_dir = self._project_dir(project_id)
        if not any(
            self._project_path(project_id, filename).exists()
            for filename in ("autosave.json", "project.json", "project.json.bak")
        ):
            raise FileNotFoundError(f"project not found: {project_id}")
        shutil.rmtree(project_dir)
        projects = [
            summary
            for summary in self._read_or_rebuild_index()
            if summary.id != project_id
        ]
        self._write_index(self._sort_summaries(projects))

    def delete(self, project_id: UUID) -> None:
        with self._lock:
            self._delete_locked(project_id)

    def delete_with_owner_marker(self, project_id: UUID, *, owner_key: str) -> None:
        """Delete a Project without leaving its owner's marker stale on failure."""

        with self._lock:
            marker_path = self._owner_state_path(owner_key)
            matching_marker = None
            if marker_path.exists():
                marker = read_json_model(marker_path, OwnerProjectState)
                if marker.project_id == project_id:
                    matching_marker = marker
                    marker_path.unlink()
            try:
                self._delete_locked(project_id)
            except Exception:
                if matching_marker is not None:
                    atomic_model_write(
                        marker_path,
                        matching_marker,
                        keep_backup=False,
                    )
                raise
