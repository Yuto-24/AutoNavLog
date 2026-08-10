from __future__ import annotations

from pathlib import Path

import pytest

from autonavlog.domain.project import Project
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.storage.repository import ProjectIndex
from autonavlog.storage.safe_json import (
    JsonStorageError,
    atomic_validated_write,
    read_json_model,
    validate_json_bytes,
)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":1e999}',
    ],
)
def test_strict_json_preflight_rejects_ambiguous_or_nonfinite_bytes(
    raw: bytes,
) -> None:
    with pytest.raises(JsonStorageError):
        validate_json_bytes(raw, ProjectIndex)


def test_project_save_keeps_one_generation_backup_and_loads_it_on_corruption(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    first = repository.save(project, expected_revision=0)
    second = repository.save(first.project, expected_revision=1)
    project_path = second.path
    backup_path = project_path.with_name("project.json.bak")

    assert backup_path.exists()
    assert read_json_model(backup_path, Project).revision == 1

    project_path.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )
    recovered = repository.load(project.id)
    assert recovered.revision == 1


def test_project_index_is_rebuilt_by_directory_scan_when_corrupt(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    saved = repository.save(project, expected_revision=0)
    repository.index_path.write_text('{"broken":', encoding="utf-8")

    summaries = repository.list_projects()

    assert [summary.id for summary in summaries] == [project.id]
    assert summaries[0].revision == saved.project.revision
    rebuilt = read_json_model(repository.index_path, ProjectIndex)
    assert rebuilt.projects == summaries


def test_project_index_v1_rebuilds_with_web_owner_metadata(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    owned = project.model_copy(deep=True)
    owned.metadata["web_owner_id"] = "pilot@example.com"
    repository.save(owned, expected_revision=0)
    repository.index_path.write_text(
        '{"schema_version":1,"projects":[]}',
        encoding="utf-8",
    )

    summaries = repository.list_projects()

    assert len(summaries) == 1
    assert summaries[0].web_owner_id == "pilot@example.com"
    migrated = read_json_model(repository.index_path, ProjectIndex)
    assert migrated.schema_version == 2


def test_project_save_updates_valid_index_without_rescanning_projects(
    tmp_path: Path,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    first = repository.save(project, expected_revision=0)

    def unexpected_scan() -> list[object]:
        pytest.fail("valid index update must not rescan every Project")

    monkeypatch.setattr(repository, "_scan_projects", unexpected_scan)
    renamed = first.project.model_copy(update={"name": "renamed"})

    second = repository.save(renamed, expected_revision=1)

    assert repository.list_projects()[0].name == "renamed"
    assert second.project.revision == 2


def test_failed_post_publish_validation_restores_previous_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b'{"value":1}\n')
    calls = 0

    def fail_after_publish(raw: bytes) -> object:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise JsonStorageError("injected read-back failure")
        return raw

    with pytest.raises(JsonStorageError, match="injected"):
        atomic_validated_write(
            path,
            b'{"value":2}\n',
            fail_after_publish,
        )

    assert path.read_bytes() == b'{"value":1}\n'
    assert path.with_name("state.json.bak").read_bytes() == b'{"value":1}\n'


def test_write_rejects_nonfinite_project_metadata_without_publishing(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    invalid = project.model_copy(update={"metadata": {"bad": float("nan")}})

    with pytest.raises(JsonStorageError, match="non-finite"):
        repository.autosave(invalid)

    assert not (tmp_path / "projects" / str(project.id) / "autosave.json").exists()
