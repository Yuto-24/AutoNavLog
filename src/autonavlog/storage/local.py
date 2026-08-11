from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from autonavlog.domain.enums import AdoptedSource
from autonavlog.domain.planning import ARRIVAL_ALTITUDE_RULE_VERSION
from autonavlog.domain.project import Project
from autonavlog.domain.snapshot import CalculationSnapshot

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


def _migrate_v3_arrival_snapshot(payload: Any) -> tuple[Any, bool]:
    if not isinstance(payload, dict):
        return payload, False
    calculation_results = payload.get("calculation_results")
    if not isinstance(calculation_results, dict):
        return payload, False
    arrival = calculation_results.get("arrival_altitude")
    if not isinstance(arrival, dict):
        return payload, False
    if arrival.get("rule_version") != "CAC_REV19_8_4_9_V3":
        return payload, False

    selected_pattern = arrival.get("derived_pattern_altitude_ft_msl")
    master_pattern = arrival.get("pattern_altitude_ft_msl")
    if (
        isinstance(selected_pattern, bool)
        or not isinstance(selected_pattern, int)
        or isinstance(master_pattern, bool)
        or not isinstance(master_pattern, (int, float))
    ):
        return payload, False
    selected_source = (
        AdoptedSource.AUTOMATIC if selected_pattern == master_pattern else AdoptedSource.MANUAL
    )
    arrival["selected_pattern_altitude_ft_msl"] = selected_pattern
    arrival["selected_pattern_altitude_source"] = selected_source.value
    arrival["rule_version"] = ARRIVAL_ALTITUDE_RULE_VERSION

    input_data = payload.get("input_data")
    if isinstance(input_data, dict):
        metadata = input_data.get("metadata")
        if isinstance(metadata, dict):
            ui_state = metadata.get("ui_state")
            if isinstance(ui_state, dict):
                arrival_plan = ui_state.get("arrival_plan")
                if isinstance(arrival_plan, dict):
                    arrival_plan["selected_pattern_altitude_ft_msl"] = selected_pattern
                    arrival_plan["selected_pattern_altitude_source"] = selected_source.value
    return payload, True


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
                project = read_json_model(path, Project)
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
            project = read_json_model(path, Project)
        except JsonStorageError:
            project = read_json_model(path.with_name("project.json.bak"), Project)
        if project.id != project_id:
            raise JsonStorageError("project id does not match its storage path")
        return project

    def save(self, project: Project, expected_revision: int) -> SaveResult:
        with self._lock:
            return self._save_locked(project, expected_revision)

    def _save_locked(self, project: Project, expected_revision: int) -> SaveResult:
        path = self._project_dir(project.id) / "project.json"
        if path.exists():
            existing = read_json_model(path, Project)
            if existing.revision != expected_revision:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
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
                "updated_at": datetime.now(timezone.utc),
            }
        )
        atomic_model_write(path, saved)
        self._update_index(saved)
        return SaveResult(project=saved, path=path)

    def autosave(self, project: Project) -> Path:
        path = self._project_dir(project.id) / "autosave.json"
        atomic_model_write(path, project)
        return path

    def create_snapshot(self, snapshot: CalculationSnapshot) -> Path:
        path = self.root / "snapshots" / str(snapshot.project_id) / f"{snapshot.id}.json"
        if path.exists():
            raise FileExistsError("snapshots are immutable")
        atomic_model_write(path, snapshot, keep_backup=False)
        return path

    def load_snapshot(self, project_id: UUID, snapshot_id: UUID) -> CalculationSnapshot:
        path = self.root / "snapshots" / str(project_id) / f"{snapshot_id}.json"
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise JsonStorageError(f"cannot read JSON file: {path}") from error
        payload = parse_json_bytes(raw)
        payload, migrated = _migrate_v3_arrival_snapshot(payload)
        if migrated:
            try:
                serialized = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, allow_nan=False
                )
            except (TypeError, ValueError) as error:
                raise JsonStorageError("cannot migrate legacy snapshot JSON") from error
            raw = (serialized + "\n").encode("utf-8")
        snapshot = validate_json_bytes(raw, CalculationSnapshot)
        if snapshot.project_id != project_id or snapshot.id != snapshot_id:
            raise JsonStorageError("snapshot identity does not match its storage path")
        return snapshot
