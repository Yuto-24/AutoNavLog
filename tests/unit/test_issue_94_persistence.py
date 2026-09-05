from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from autonavlog.domain.calculation import CalculationOutcome, FuelPlan
from autonavlog.domain.enums import ProjectStatus
from autonavlog.domain.planning import PersistedUiState
from autonavlog.domain.project import Project
from autonavlog.storage.local import LocalProjectRepository, UnsafeStoragePathError
from autonavlog.storage.repository import LastCalculationRecord, owner_storage_key
from autonavlog.storage.safe_json import JsonStorageError

OWNER = "pilot@example.com"
FINGERPRINT = "a" * 64


def _calculated_project(project: Project) -> Project:
    calculated = project.model_copy(deep=True)
    calculated.metadata["web_owner_id"] = OWNER
    calculated.metadata["ui_state"] = PersistedUiState(
        calculated_against_fingerprint=FINGERPRINT,
    ).model_dump(mode="json")
    return calculated


def _record(project: Project, *, saved_at: datetime | None = None) -> LastCalculationRecord:
    calculated = _calculated_project(project)
    outcome = CalculationOutcome(
        project_id=calculated.id,
        selected_forecast_run_id=None,
        fuel_plan=FuelPlan(total_usable_gal=calculated.total_usable_fuel_gal),
        converged=True,
        status=ProjectStatus.READY_FOR_COPY,
        policy_version="test-v1",
    )
    return LastCalculationRecord(
        project_id=calculated.id,
        owner_key=owner_storage_key(OWNER),
        project=calculated,
        outcome=outcome,
        calculation_fingerprint=FINGERPRINT,
        saved_at_utc=saved_at or datetime.now(UTC),
    )


def test_autosave_only_project_is_listed_loaded_and_deleted(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    draft = project.model_copy(update={"name": "autosave only"})

    repository.autosave(draft)

    assert [summary.id for summary in repository.list_projects()] == [project.id]
    assert repository.load(project.id).name == "autosave only"
    repository.delete(project.id)
    assert repository.list_projects() == []


def test_valid_autosave_wins_and_corrupt_autosave_falls_back_to_checkpoint(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    checkpoint = repository.save(project, expected_revision=0).project
    draft = checkpoint.model_copy(update={"name": "new draft"})
    autosave_path = repository.autosave(draft)

    assert repository.load(project.id).name == "new draft"

    autosave_path.write_text('{"broken":', encoding="utf-8")
    recovered = repository.load_with_recovery(project.id)
    assert recovered.project == checkpoint
    assert recovered.recovered_from_fallback is True


def test_wrong_project_id_autosave_falls_back_and_rebuilds_index(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    checkpoint = repository.save(project, expected_revision=0).project
    autosave_path = repository.autosave(
        checkpoint.model_copy(update={"name": "new draft"})
    )
    wrong_project = checkpoint.model_copy(
        update={"id": uuid4(), "name": "wrong identity"}
    )
    autosave_path.write_text(wrong_project.model_dump_json(), encoding="utf-8")
    repository.index_path.write_text('{"broken":', encoding="utf-8")

    summaries = repository.list_projects()
    recovered = repository.load_with_recovery(project.id)

    assert [(summary.id, summary.name) for summary in summaries] == [
        (project.id, checkpoint.name)
    ]
    assert recovered.project == checkpoint
    assert recovered.recovered_from_fallback is True
    rebuilt = json.loads(repository.index_path.read_text(encoding="utf-8"))
    assert rebuilt["projects"][0]["id"] == str(project.id)
    assert rebuilt["projects"][0]["name"] == checkpoint.name


def test_wrong_identity_autosave_and_checkpoint_fall_back_to_backup(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    first_checkpoint = repository.save(project, expected_revision=0).project
    second_checkpoint = repository.save(
        first_checkpoint.model_copy(update={"name": "second checkpoint"}),
        expected_revision=first_checkpoint.revision,
    ).project
    wrong_project = second_checkpoint.model_copy(update={"id": uuid4()})
    repository.autosave(second_checkpoint).write_text(
        wrong_project.model_dump_json(),
        encoding="utf-8",
    )
    (
        tmp_path / "projects" / str(project.id) / "project.json"
    ).write_text(wrong_project.model_dump_json(), encoding="utf-8")

    recovered = repository.load_with_recovery(project.id)

    assert recovered.project == first_checkpoint
    assert recovered.recovered_from_fallback is True


def test_owner_marker_uses_opaque_key_and_clear_can_be_conditional(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    owner_key = owner_storage_key(OWNER)

    path = repository.set_owner_project(owner_key, project.id)

    assert OWNER not in str(path)
    assert OWNER not in path.read_text(encoding="utf-8")
    assert repository.load_owner_project(owner_key) == project.id
    repository.clear_owner_project(owner_key, uuid4())
    assert repository.load_owner_project(owner_key) == project.id
    repository.clear_owner_project(owner_key, project.id)
    assert repository.load_owner_project(owner_key) is None
    assert not path.with_name("state.json.bak").exists()


def test_delete_with_owner_marker_clears_only_matching_marker(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    owner_key = owner_storage_key(OWNER)
    repository.autosave(project)
    repository.set_owner_project(owner_key, project.id)

    repository.delete_with_owner_marker(project.id, owner_key=owner_key)

    assert repository.load_owner_project(owner_key) is None
    assert not (tmp_path / "projects" / str(project.id)).exists()
    assert repository.list_projects() == []

    other_project = project.model_copy(update={"id": uuid4()})
    repository.autosave(other_project)
    other_marker = uuid4()
    repository.set_owner_project(owner_key, other_marker)
    repository.delete_with_owner_marker(other_project.id, owner_key=owner_key)
    assert repository.load_owner_project(owner_key) == other_marker


def test_delete_failure_restores_matching_owner_marker(
    tmp_path: Path,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    owner_key = owner_storage_key(OWNER)
    repository.autosave(project)
    repository.set_owner_project(owner_key, project.id)

    def fail_delete(_project_id: object) -> None:
        assert repository.load_owner_project(owner_key) is None
        raise OSError("injected Project delete failure")

    monkeypatch.setattr(repository, "_delete_locked", fail_delete)

    with pytest.raises(OSError, match="injected Project delete failure"):
        repository.delete_with_owner_marker(project.id, owner_key=owner_key)

    assert repository.load_owner_project(owner_key) == project.id
    assert repository.load(project.id) == project


def test_owner_marker_clear_failure_prevents_project_delete(
    tmp_path: Path,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    owner_key = owner_storage_key(OWNER)
    repository.autosave(project)
    marker_path = repository.set_owner_project(owner_key, project.id)
    original_unlink = Path.unlink

    def fail_marker_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == marker_path:
            raise OSError("injected owner marker clear failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_marker_unlink)

    with pytest.raises(OSError, match="injected owner marker clear failure"):
        repository.delete_with_owner_marker(project.id, owner_key=owner_key)

    assert repository.load_owner_project(owner_key) == project.id
    assert repository.load(project.id) == project


def test_last_calculation_is_one_atomic_record_and_invalid_owner_is_quarantined(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    first = _record(project, saved_at=datetime(2026, 9, 5, 0, 0, tzinfo=UTC))
    second = _record(project, saved_at=datetime(2026, 9, 5, 1, 0, tzinfo=UTC))

    path = repository.replace_last_calculation(first)
    repository.replace_last_calculation(second)

    assert repository.load_last_calculation(
        project.id,
        owner_key=owner_storage_key(OWNER),
    ) == second
    assert not path.with_name("last-calculation.json.bak").exists()

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["owner_key"] = "b" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(JsonStorageError):
        repository.load_last_calculation(
            project.id,
            owner_key=owner_storage_key(OWNER),
        )
    assert not path.exists()
    assert path.with_name("last-calculation.invalid.json").exists()


@pytest.mark.parametrize("mismatch", ["schema", "project_id", "fingerprint"])
def test_invalid_last_calculation_identity_is_quarantined(
    tmp_path: Path,
    project: Project,
    mismatch: str,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    path = repository.replace_last_calculation(_record(project))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if mismatch == "schema":
        payload["schema_version"] = 2
    elif mismatch == "project_id":
        payload["project_id"] = str(uuid4())
    elif mismatch == "fingerprint":
        payload["calculation_fingerprint"] = "b" * 64
    else:  # pragma: no cover - guards future parametrization edits.
        raise AssertionError(mismatch)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(JsonStorageError):
        repository.load_last_calculation(
            project.id,
            owner_key=owner_storage_key(OWNER),
        )

    assert not path.exists()
    assert path.with_name("last-calculation.invalid.json").exists()


def test_last_calculation_write_failure_preserves_previous_record(
    tmp_path: Path,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    first = _record(project)
    path = repository.replace_last_calculation(first)
    previous = path.read_bytes()

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected")

    monkeypatch.setattr("autonavlog.storage.local.atomic_model_write", fail_write)
    with pytest.raises(OSError, match="injected"):
        repository.replace_last_calculation(_record(project))
    assert path.read_bytes() == previous


def test_project_symlink_is_never_followed(
    tmp_path: Path,
    project: Project,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    project_dir = tmp_path / "projects" / str(project.id)
    project_dir.parent.mkdir()
    project_dir.symlink_to(outside, target_is_directory=True)
    repository = LocalProjectRepository(tmp_path)

    with pytest.raises(UnsafeStoragePathError):
        repository.autosave(project)
