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
    draft.metadata["web_owner_id"] = OWNER

    repository.autosave(draft, set_last_opened=True)

    summaries = repository.list_projects()
    assert [summary.id for summary in summaries] == [project.id]
    assert summaries[0].kind == "LATEST"
    assert repository.load(project.id).name == "autosave only"
    repository.delete(project.id)
    assert repository.list_projects() == []


def test_new_owner_latest_replaces_only_its_old_autosave_draft(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    first = project.model_copy(deep=True)
    first.metadata["web_owner_id"] = OWNER
    other = project.model_copy(update={"id": uuid4()}, deep=True)
    other.metadata["web_owner_id"] = "other@example.com"
    second = project.model_copy(update={"id": uuid4()}, deep=True)
    second.metadata["web_owner_id"] = OWNER

    repository.autosave(first, set_last_opened=True)
    repository.autosave(other, set_last_opened=True)
    repository.autosave(second, set_last_opened=True)

    assert not (tmp_path / "projects" / str(first.id)).exists()
    assert (tmp_path / "projects" / str(other.id)).is_dir()
    summaries = {summary.id: summary.kind for summary in repository.list_projects()}
    assert summaries == {other.id: "LATEST", second.id: "LATEST"}


def test_stale_owner_autosave_cannot_replace_the_active_latest(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    first = project.model_copy(deep=True)
    first.metadata["web_owner_id"] = OWNER
    second = project.model_copy(update={"id": uuid4()}, deep=True)
    second.metadata["web_owner_id"] = OWNER

    repository.autosave(first, set_last_opened=True)
    repository.autosave(second, set_last_opened=True)
    # A delayed calculation from the no-longer-active Project may recreate
    # its own autosave, but cannot change the owner's Latest marker.
    repository.autosave(first)

    state = repository.load_owner_state(owner_storage_key(OWNER))
    assert state is not None
    assert state.last_opened_project_id == second.id
    assert state.latest_draft_project_id == second.id
    assert (tmp_path / "projects" / str(second.id)).is_dir()
    assert {summary.id: summary.kind for summary in repository.list_projects()} == {
        second.id: "LATEST"
    }


def test_editing_explicitly_loaded_checkpoint_cleans_the_prior_latest(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    checkpoint = project.model_copy(deep=True)
    checkpoint.metadata["web_owner_id"] = OWNER
    saved = repository.save(checkpoint, expected_revision=0).project
    latest = project.model_copy(update={"id": uuid4()}, deep=True)
    latest.metadata["web_owner_id"] = OWNER
    repository.autosave(latest, set_last_opened=True)

    # Explicit load changes last-opened only.  The following real edit is the
    # promotion point that retires the otherwise independent Latest draft.
    owner_key = owner_storage_key(OWNER)
    repository.set_owner_project(owner_key, saved.id)
    repository.autosave(saved.model_copy(update={"pilot_name": "edited"}))

    state = repository.load_owner_state(owner_key)
    assert state is not None
    assert state.last_opened_project_id == saved.id
    assert state.latest_draft_project_id is None
    assert not (tmp_path / "projects" / str(latest.id)).exists()
    assert {summary.id: summary.kind for summary in repository.list_projects()} == {
        saved.id: "SAVED"
    }


def test_checkpoint_promotes_latest_and_keeps_saved_projects_visible(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    latest = project.model_copy(deep=True)
    latest.metadata["web_owner_id"] = OWNER
    repository.autosave(latest, set_last_opened=True)

    saved = repository.save(latest, expected_revision=0).project
    assert repository.list_projects()[0].kind == "SAVED"
    assert repository.list_projects()[0].revision == 1

    draft = project.model_copy(update={"id": uuid4()}, deep=True)
    draft.metadata["web_owner_id"] = OWNER
    repository.autosave(draft, set_last_opened=True)
    summaries = {summary.id: summary.kind for summary in repository.list_projects()}
    assert summaries == {saved.id: "SAVED", draft.id: "LATEST"}


def test_checkpoint_succeeds_when_latest_marker_clear_fails_and_can_be_resaved(
    tmp_path: Path,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    latest = project.model_copy(deep=True)
    latest.metadata["web_owner_id"] = OWNER
    repository.autosave(latest, set_last_opened=True)
    original_write = repository._write_owner_state_locked  # noqa: SLF001
    failures = 0

    def fail_once(*_args: object, **_kwargs: object) -> Path:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise OSError("injected marker clear failure")
        return original_write(*_args, **_kwargs)

    monkeypatch.setattr(repository, "_write_owner_state_locked", fail_once)
    saved = repository.save(latest, expected_revision=0).project

    assert saved.revision == 1
    assert repository.list_projects()[0].kind == "SAVED"
    resaved = repository.save(saved, expected_revision=1).project
    assert resaved.revision == 2
    assert repository.list_projects()[0].revision == 2


def test_saved_project_summary_uses_checkpoint_not_unsaved_draft(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    checkpoint = project.model_copy(update={"name": "checkpoint"}, deep=True)
    checkpoint.metadata["web_owner_id"] = OWNER
    saved = repository.save(checkpoint, expected_revision=0).project
    unsaved_draft = saved.model_copy(
        update={
            "name": "unsaved rename",
            "updated_at": datetime(2100, 1, 1, tzinfo=UTC),
        },
        deep=True,
    )
    repository.autosave(unsaved_draft)

    summaries = repository.list_projects()
    assert [(summary.name, summary.kind, summary.revision) for summary in summaries] == [
        ("checkpoint", "SAVED", 1)
    ]

    repository.index_path.write_text('{"broken":', encoding="utf-8")
    rebuilt = repository.list_projects()
    assert [(summary.name, summary.kind, summary.revision) for summary in rebuilt] == [
        ("checkpoint", "SAVED", 1)
    ]
    assert repository.load(project.id).name == "unsaved rename"


def test_explicit_save_marker_failure_keeps_checkpoint_authoritative(
    tmp_path: Path,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    latest = project.model_copy(update={"name": "latest"}, deep=True)
    latest.metadata["web_owner_id"] = OWNER
    repository.autosave(latest)

    def fail_marker(*_args: object, **_kwargs: object) -> Path:
        raise OSError("injected marker failure")

    monkeypatch.setattr(repository, "_write_owner_state_locked", fail_marker)

    saved = repository.save(latest.model_copy(update={"name": "checkpoint"}), 0).project

    assert saved.revision == 1
    assert repository.list_projects()[0].kind == "SAVED"
    assert repository.list_projects()[0].name == "checkpoint"
    assert repository.load(project.id).name == "checkpoint"


def test_v1_owner_state_migrates_autosave_only_project_to_latest(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    draft = project.model_copy(deep=True)
    draft.metadata["web_owner_id"] = OWNER
    owner_key = owner_storage_key(OWNER)
    path = repository.autosave(draft, set_last_opened=True)
    marker_path = repository._owner_state_path(owner_key)  # noqa: SLF001 - migration fixture
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps({"schema_version": 1, "project_id": str(project.id)}),
        encoding="utf-8",
    )

    assert repository.load_owner_project(owner_key) == project.id
    migrated = json.loads(
        repository._owner_state_path(owner_key).read_text(encoding="utf-8")  # noqa: SLF001
    )
    assert migrated == {
        "schema_version": 2,
        "last_opened_project_id": str(project.id),
        "latest_draft_project_id": str(project.id),
        "pending_cleanup_project_ids": [],
    }
    assert repository.list_projects()[0].kind == "LATEST"
    assert path.exists()


def test_marker_write_failure_keeps_old_latest_and_cleanup_failure_retries(
    tmp_path: Path,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    first = project.model_copy(deep=True)
    first.metadata["web_owner_id"] = OWNER
    repository.autosave(first, set_last_opened=True)
    second = project.model_copy(update={"id": uuid4()}, deep=True)
    second.metadata["web_owner_id"] = OWNER
    original_write = repository._write_owner_state_locked  # noqa: SLF001

    def fail_marker(*_args: object, **_kwargs: object) -> Path:
        raise OSError("injected marker failure")

    monkeypatch.setattr(repository, "_write_owner_state_locked", fail_marker)
    with pytest.raises(OSError, match="marker failure"):
        repository.autosave(second, set_last_opened=True)
    assert repository.list_projects()[0].id == first.id

    monkeypatch.setattr(repository, "_write_owner_state_locked", original_write)
    original_delete = repository._delete_locked  # noqa: SLF001
    failed = False

    def fail_once(project_id: object) -> None:
        nonlocal failed
        if project_id == first.id and not failed:
            failed = True
            raise OSError("injected cleanup failure")
        original_delete(project_id)  # type: ignore[arg-type]

    monkeypatch.setattr(repository, "_delete_locked", fail_once)
    repository.autosave(second, set_last_opened=True)
    assert (tmp_path / "projects" / str(first.id)).is_dir()

    monkeypatch.setattr(repository, "_delete_locked", original_delete)
    assert repository.list_projects()[0].id == second.id
    assert not (tmp_path / "projects" / str(first.id)).exists()


def test_route_confirm_promotion_replaces_corrupt_marker_without_cleaning_orphan(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    orphan = project.model_copy(deep=True)
    orphan.metadata["web_owner_id"] = OWNER
    replacement = project.model_copy(update={"id": uuid4()}, deep=True)
    replacement.metadata["web_owner_id"] = OWNER
    owner_key = owner_storage_key(OWNER)

    repository.autosave(orphan, set_last_opened=True)
    repository._owner_state_path(owner_key).write_text(  # noqa: SLF001 - corruption fixture
        '{"broken":',
        encoding="utf-8",
    )

    repository.autosave(replacement, set_last_opened=True)

    state = repository.load_owner_state(owner_key)
    assert state is not None
    assert state.last_opened_project_id == replacement.id
    assert state.latest_draft_project_id == replacement.id
    assert state.pending_cleanup_project_ids == []
    assert (tmp_path / "projects" / str(orphan.id)).is_dir()
    assert {summary.id: summary.kind for summary in repository.list_projects()} == {
        replacement.id: "LATEST"
    }


def test_reset_neutralizes_corrupt_owner_marker_without_touching_orphan(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    orphan = project.model_copy(deep=True)
    orphan.metadata["web_owner_id"] = OWNER
    repository.autosave(orphan, set_last_opened=True)
    owner_key = owner_storage_key(OWNER)
    marker_path = repository._owner_state_path(owner_key)  # noqa: SLF001 - corruption fixture
    marker_path.write_text('{"broken":', encoding="utf-8")

    repository.clear_owner_project(owner_key)

    assert not marker_path.exists()
    assert marker_path.with_name("state.invalid.json").exists()
    assert (tmp_path / "projects" / str(orphan.id)).is_dir()


def test_delete_checkpoint_succeeds_with_corrupt_marker_and_quarantines_it(
    tmp_path: Path,
    project: Project,
) -> None:
    repository = LocalProjectRepository(tmp_path)
    checkpoint = project.model_copy(deep=True)
    checkpoint.metadata["web_owner_id"] = OWNER
    saved = repository.save(checkpoint, expected_revision=0).project
    owner_key = owner_storage_key(OWNER)
    marker_path = repository._owner_state_path(owner_key)  # noqa: SLF001 - corruption fixture
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text('{"broken":', encoding="utf-8")

    repository.delete_with_owner_marker(saved.id, owner_key=owner_key)

    assert not (tmp_path / "projects" / str(saved.id)).exists()
    assert not marker_path.exists()
    assert marker_path.with_name("state.invalid.json").exists()


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
