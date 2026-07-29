from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from autonavlog.domain.project import Project
from autonavlog.domain.snapshot import CalculationSnapshot

from .repository import SaveResult


class RevisionConflictError(RuntimeError):
    def __init__(self, message: str, conflict_copy: Path):
        super().__init__(message)
        self.conflict_copy = conflict_copy


def _atomic_json_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LocalProjectRepository:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _project_dir(self, project_id: UUID) -> Path:
        return self.root / "projects" / str(project_id)

    def load(self, project_id: UUID) -> Project:
        path = self._project_dir(project_id) / "project.json"
        return Project.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, project: Project, expected_revision: int) -> SaveResult:
        path = self._project_dir(project.id) / "project.json"
        if path.exists():
            existing = Project.model_validate_json(path.read_text(encoding="utf-8"))
            if existing.revision != expected_revision:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                conflict = path.with_name(f"project-conflict-{stamp}.json")
                _atomic_json_write(conflict, project.model_dump(mode="json"))
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
        _atomic_json_write(path, saved.model_dump(mode="json"))
        return SaveResult(project=saved, path=path)

    def autosave(self, project: Project) -> Path:
        path = self._project_dir(project.id) / "autosave.json"
        _atomic_json_write(path, project.model_dump(mode="json"))
        return path

    def create_snapshot(self, snapshot: CalculationSnapshot) -> Path:
        path = self.root / "snapshots" / str(snapshot.project_id) / f"{snapshot.id}.json"
        if path.exists():
            raise FileExistsError("snapshots are immutable")
        _atomic_json_write(path, snapshot.model_dump(mode="json"))
        return path

    def load_snapshot(self, project_id: UUID, snapshot_id: UUID) -> CalculationSnapshot:
        path = self.root / "snapshots" / str(project_id) / f"{snapshot_id}.json"
        return CalculationSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
