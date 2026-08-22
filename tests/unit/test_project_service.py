from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from autonavlog.application.project_service import ProjectService
from autonavlog.domain.project import Project
from autonavlog.storage.local import LocalProjectRepository


def _with_corrupted_ui_state(project: Project) -> tuple[Project, object]:
    corrupted = project.model_copy(deep=True)
    unserializable = object()
    corrupted.metadata["ui_state"] = {
        "state_schema_version": 4,
        "unserializable": unserializable,
    }
    return corrupted, unserializable


def test_normalize_ui_state_wraps_type_error_as_value_error(project: Project) -> None:
    corrupted, _ = _with_corrupted_ui_state(project)

    with pytest.raises(
        ValueError,
        match="PersistedUiStateを安全に読み込めません",
    ):
        ProjectService._normalize_ui_state(corrupted)


def test_delete_expired_projects_deletes_routes_whose_departure_time_has_passed(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    expired = project.model_copy(
        update={"planned_departure_time_jst": datetime(2026, 8, 16, 9, tzinfo=timezone.utc)}
    )
    repository.save(expired, expected_revision=0)
    service = ProjectService(
        repository,
        clock=lambda: datetime(2026, 8, 16, 9, 0, 1, tzinfo=timezone.utc),
    )

    assert service.delete_expired_projects() == []
    assert not (tmp_path / "projects" / str(project.id)).exists()


def test_delete_expired_projects_keeps_routes_before_their_departure_time(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    upcoming = project.model_copy(
        update={"planned_departure_time_jst": datetime(2026, 8, 16, 9, tzinfo=timezone.utc)}
    )
    repository.save(upcoming, expected_revision=0)
    service = ProjectService(
        repository,
        clock=lambda: datetime(2026, 8, 16, 8, 59, 59, tzinfo=timezone.utc),
    )

    assert [summary.id for summary in service.delete_expired_projects()] == [project.id]
    assert (tmp_path / "projects" / str(project.id) / "project.json").exists()
