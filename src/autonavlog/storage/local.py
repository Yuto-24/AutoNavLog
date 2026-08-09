from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from autonavlog.domain.project import Project
from autonavlog.domain.snapshot import CalculationSnapshot

from .repository import ProjectIndex, ProjectSummary, SaveResult
from .safe_json import (
    JsonStorageError,
    atomic_model_write,
    read_json_model,
)


class RevisionConflictError(RuntimeError):
    def __init__(self, message: str, conflict_copy: Path):
        super().__init__(message)
        self.conflict_copy = conflict_copy


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
        snapshot = read_json_model(path, CalculationSnapshot)
        if snapshot.project_id != project_id or snapshot.id != snapshot_id:
            raise JsonStorageError("snapshot identity does not match its storage path")
        return snapshot
