from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from autonavlog.domain.project import Project

from .repository import ProjectIndex, ProjectSummary, SaveResult
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
        self.root = Path(root)
        self._lock = RLock()

    def _project_dir(self, project_id: UUID) -> Path:
        return self.root / "projects" / str(project_id)

    @property
    def index_path(self) -> Path:
        return self.root / "projects" / "index.json"

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
        projects_root = self.root / "projects"
        summaries: list[ProjectSummary] = []
        if not projects_root.exists():
            return summaries
        for path in sorted(projects_root.glob("*/project.json")):
            try:
                project = _read_migrated_project(path)
            except JsonStorageError:
                continue
            if path.parent.name != str(project.id):
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
            return self._read_or_rebuild_index()

    def load(self, project_id: UUID) -> Project:
        path = self._project_dir(project_id) / "project.json"
        try:
            project = _read_migrated_project(path)
        except JsonStorageError:
            project = _read_migrated_project(path.with_name("project.json.bak"))
        if project.id != project_id:
            raise JsonStorageError("project id does not match its storage path")
        return project

    def save(self, project: Project, expected_revision: int) -> SaveResult:
        with self._lock:
            return self._save_locked(project, expected_revision)

    def _save_locked(self, project: Project, expected_revision: int) -> SaveResult:
        path = self._project_dir(project.id) / "project.json"
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
        path = self._project_dir(project.id) / "autosave.json"
        atomic_model_write(path, project)
        return path

    def delete(self, project_id: UUID) -> None:
        with self._lock:
            project_dir = self._project_dir(project_id)
            if not (project_dir / "project.json").exists():
                raise FileNotFoundError(f"project not found: {project_id}")
            shutil.rmtree(project_dir)
            projects = [
                summary
                for summary in self._read_or_rebuild_index()
                if summary.id != project_id
            ]
            self._write_index(self._sort_summaries(projects))
