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
    owner_storage_key,
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
    if payload.get("schema_version") != 5:
        payload["schema_version"] = 5
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
            legacy_tas = value.get("manual_tas_kt")
            if legacy_tas is not None and "manual_tas_kt_by_phase" not in value:
                # A historic scalar belonged to the section's saved phase.
                # Do not copy it into every phase a later segmentation may
                # create from this physical section.
                phase = value.get("phase")
                if isinstance(phase, str):
                    value["manual_tas_kt_by_phase"] = {phase: legacy_tas}
                    value["manual_tas_kt"] = None
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

    def _owner_invalid_state_path(self, owner_key: str) -> Path:
        return self._safe_path(
            self._owner_state_path(owner_key).with_name("state.invalid.json")
        )

    def _quarantine_owner_state_locked(self, owner_key: str) -> Path | None:
        """Neutralize unreadable owner state without interpreting its contents."""

        path = self._owner_state_path(owner_key)
        if not path.exists():
            return None
        quarantine = self._owner_invalid_state_path(owner_key)
        os.replace(path, quarantine)
        LOGGER.warning("Quarantined invalid owner state: owner_key=%s", owner_key)
        return quarantine

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
    def _project_owner_key(project: Project) -> str | None:
        owner_id = project.metadata.get("web_owner_id")
        return owner_storage_key(owner_id) if isinstance(owner_id, str) and owner_id else None

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
                self._project_path(project_id, "project.json"),
                self._project_path(project_id, "project.json.bak"),
                self._project_path(project_id, "autosave.json"),
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

    def _has_valid_checkpoint_locked(self, project_id: UUID) -> bool:
        """Return whether a Project has a trustworthy explicit checkpoint.

        ``project.json.bak`` is deliberately included: it is the last known
        explicit checkpoint after an interrupted/corrupt primary write.
        """

        return self._checkpoint_summary_locked(project_id) is not None

    def _checkpoint_summary_locked(self, project_id: UUID) -> ProjectSummary | None:
        """Return the latest trustworthy explicit checkpoint summary, if any."""

        for filename in ("project.json", "project.json.bak"):
            path = self._project_path(project_id, filename)
            if not path.exists():
                continue
            try:
                project = _read_migrated_project(path)
                self._validate_project_path_identity(project, project_id)
            except JsonStorageError:
                continue
            return self._summary(project)
        return None

    def _is_autosave_only_for_owner_locked(
        self,
        project_id: UUID,
        owner_key: str,
    ) -> bool:
        if self._has_valid_checkpoint_locked(project_id):
            return False
        path = self._project_path(project_id, "autosave.json")
        if not path.exists():
            return False
        try:
            project = _read_migrated_project(path)
            self._validate_project_path_identity(project, project_id)
        except JsonStorageError:
            return False
        return self._project_owner_key(project) == owner_key

    def _read_owner_state_locked(
        self,
        owner_key: str,
        *,
        migrate: bool = True,
    ) -> OwnerProjectState | None:
        path = self._owner_state_path(owner_key)
        if not path.exists():
            return None
        state = read_json_model(path, OwnerProjectState)
        if state.schema_version != 1 or not migrate:
            return state
        assert state.project_id is not None  # validated by OwnerProjectState
        project_id = state.project_id
        migrated = OwnerProjectState(
            schema_version=2,
            last_opened_project_id=project_id,
            latest_draft_project_id=(
                project_id
                if self._is_autosave_only_for_owner_locked(project_id, owner_key)
                else None
            ),
        )
        atomic_model_write(path, migrated, keep_backup=False)
        return migrated

    def _write_owner_state_locked(
        self,
        owner_key: str,
        state: OwnerProjectState,
    ) -> Path:
        path = self._owner_state_path(owner_key)
        atomic_model_write(path, state, keep_backup=False)
        return path

    def _iter_owner_keys_locked(self) -> list[str]:
        owners_root = self._safe_path(self.root / "owners")
        if not owners_root.exists():
            return []
        keys: list[str] = []
        for owner_dir in owners_root.iterdir():
            if not owner_dir.is_dir() or owner_dir.is_symlink():
                continue
            key = owner_dir.name
            if len(key) == 64 and all(character in "0123456789abcdef" for character in key):
                keys.append(key)
        return keys

    def _cleanup_pending_locked(self, owner_key: str, state: OwnerProjectState) -> None:
        """Best-effort cleanup of drafts named by a committed owner marker.

        A pending id is recorded in the same atomic marker write that selects
        the new Latest.  We never infer cleanup candidates by scanning, which
        preserves a draft written before a failed marker update.
        """

        remaining: list[UUID] = []
        for project_id in state.pending_cleanup_project_ids:
            try:
                if self._is_autosave_only_for_owner_locked(project_id, owner_key):
                    self._delete_locked(project_id)
            except FileNotFoundError:
                pass
            except Exception:
                LOGGER.exception("Unable to clean up stale autosave-only Project: %s", project_id)
                remaining.append(project_id)
        if remaining != state.pending_cleanup_project_ids:
            try:
                self._write_owner_state_locked(
                    owner_key,
                    state.model_copy(update={"pending_cleanup_project_ids": remaining}),
                )
            except Exception:
                # A successful deletion with an uncleared retry token is safe:
                # a later attempt sees the missing Project and removes it.
                LOGGER.exception("Unable to record Latest cleanup completion")

    def _retry_latest_cleanup_locked(self) -> None:
        for owner_key in self._iter_owner_keys_locked():
            try:
                state = self._read_owner_state_locked(owner_key)
            except (JsonStorageError, OSError, ValueError):
                LOGGER.exception("Unable to read owner state while retrying Latest cleanup")
                continue
            if state is not None:
                self._cleanup_pending_locked(owner_key, state)

    def list_projects(self) -> list[ProjectSummary]:
        with self._lock:
            self._retry_latest_cleanup_locked()
            latest_by_owner: dict[str, UUID] = {}
            for owner_key in self._iter_owner_keys_locked():
                try:
                    state = self._read_owner_state_locked(owner_key)
                except (JsonStorageError, OSError, ValueError):
                    LOGGER.exception("Ignoring invalid owner state while listing Projects")
                    continue
                if state is not None and state.latest_draft_project_id is not None:
                    latest_by_owner[owner_key] = state.latest_draft_project_id
            visible: list[ProjectSummary] = []
            for summary in self._read_or_rebuild_index():
                project_dir = self._project_dir(summary.id)
                if not project_dir.is_dir() or project_dir.is_symlink():
                    continue
                checkpoint_summary = self._checkpoint_summary_locked(summary.id)
                if checkpoint_summary is not None:
                    visible.append(checkpoint_summary.model_copy(update={"kind": "SAVED"}))
                    continue
                summary_owner_key = (
                    owner_storage_key(summary.web_owner_id)
                    if isinstance(summary.web_owner_id, str) and summary.web_owner_id
                    else None
                )
                if (
                    summary_owner_key is not None
                    and latest_by_owner.get(summary_owner_key) == summary.id
                ):
                    if self._is_autosave_only_for_owner_locked(summary.id, summary_owner_key):
                        visible.append(summary.model_copy(update={"kind": "LATEST"}))
            return self._sort_summaries(visible)

    @staticmethod
    def _validate_project_path_identity(project: Project, project_id: UUID) -> None:
        if project.id != project_id:
            raise JsonStorageError("project id does not match its storage path")

    def load_checkpoint(self, project_id: UUID) -> Project | None:
        """Read the explicit checkpoint separately from the latest working draft."""
        with self._lock:
            error: JsonStorageError | None = None
            for filename in ("project.json", "project.json.bak"):
                path = self._project_path(project_id, filename)
                if not path.exists():
                    continue
                try:
                    project = _read_migrated_project(path)
                    self._validate_project_path_identity(project, project_id)
                except UnsafeStoragePathError:
                    raise
                except JsonStorageError as exc:
                    error = exc
                    LOGGER.warning("Ignoring corrupt Project checkpoint: %s", path)
                    continue
                return project
            if error is not None:
                raise error
            return None

    def load(self, project_id: UUID) -> Project:
        return self.load_with_recovery(project_id).project

    def load_with_recovery(
        self, project_id: UUID, *, repair_index: bool = True
    ) -> ProjectLoadResult:
        with self._lock:
            return self._load_with_recovery_locked(project_id, repair_index=repair_index)

    def _load_with_recovery_locked(
        self, project_id: UUID, *, repair_index: bool = True
    ) -> ProjectLoadResult:
        autosave_path = self._project_path(project_id, "autosave.json")
        path = self._project_path(project_id, "project.json")
        backup_path = self._project_path(project_id, "project.json.bak")
        autosave_project = None
        autosave_invalid = False
        checkpoint_project = None
        checkpoint_recovered = False
        should_update_index = False
        recovered_from_fallback = False
        if autosave_path.exists():
            try:
                autosave_project = _read_migrated_project(autosave_path)
                self._validate_project_path_identity(autosave_project, project_id)
            except UnsafeStoragePathError:
                raise
            except JsonStorageError:
                autosave_project = None
                autosave_invalid = True
                LOGGER.warning("Ignoring corrupt Project autosave: %s", autosave_path)
        if path.exists():
            try:
                checkpoint_project = _read_migrated_project(path)
                self._validate_project_path_identity(checkpoint_project, project_id)
            except UnsafeStoragePathError:
                raise
            except JsonStorageError:
                checkpoint_project = None
                checkpoint_recovered = True
                LOGGER.warning("Ignoring corrupt Project checkpoint: %s", path)
        if checkpoint_project is None and backup_path.exists():
            checkpoint_project = _read_migrated_project(backup_path)
            self._validate_project_path_identity(checkpoint_project, project_id)
            checkpoint_recovered = True
        if (
            autosave_project is not None
            and checkpoint_project is not None
            and checkpoint_project.revision > autosave_project.revision
        ):
            # An explicit save may have committed project.json before a later
            # best-effort autosave/marker refresh failed.  In that case the
            # stale autosave must not shadow the newer checkpoint.
            project = checkpoint_project
            should_update_index = True
        elif autosave_project is not None:
            project = autosave_project
        elif checkpoint_project is not None:
            project = checkpoint_project
            recovered_from_fallback = autosave_invalid or checkpoint_recovered
            should_update_index = recovered_from_fallback
        else:
            raise FileNotFoundError(f"project not found: {project_id}")
        if should_update_index and repair_index:
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
        owner_key = self._project_owner_key(saved)
        if owner_key is not None:
            # ``project.json`` is the explicit-save commit point.  The owner
            # marker only controls Latest classification and cleanup; allowing
            # its failure to escape here would report a failed save after the
            # revision has already been durably advanced.
            try:
                state = self._read_owner_state_locked(owner_key)
                if state is not None and state.latest_draft_project_id == saved.id:
                    self._write_owner_state_locked(
                        owner_key,
                        state.model_copy(update={"latest_draft_project_id": None}),
                    )
                    state = self._read_owner_state_locked(owner_key)
                if state is not None:
                    self._cleanup_pending_locked(owner_key, state)
            except Exception:
                LOGGER.exception(
                    "Unable to update Latest marker after explicit Project save: %s",
                    saved.id,
                )
        return SaveResult(project=saved, path=path)

    def autosave(self, project: Project, *, set_last_opened: bool = False) -> Path:
        with self._lock:
            path = self._project_path(project.id, "autosave.json")
            atomic_model_write(path, project)
            self._update_index(project)
            owner_key = self._project_owner_key(project)
            if owner_key is None:
                return path
            try:
                state = self._read_owner_state_locked(owner_key)
            except JsonStorageError:
                if not set_last_opened:
                    raise
                # Route confirmation is an explicit, validated ownership
                # boundary.  It may replace an unreadable marker with a fresh
                # v2 state, but deliberately records no inferred cleanup ids.
                # Existing draft directories therefore remain hidden/orphaned
                # rather than being guessed at or deleted.
                LOGGER.warning(
                    "Replacing invalid owner state while committing new Latest: owner_key=%s",
                    owner_key,
                )
                state = OwnerProjectState()
            if state is None:
                state = OwnerProjectState()
            # A background calculation can finish after its session stopped
            # being the owner's active Project.  Its draft is still useful for
            # its own recovery/last-good record, but it must not steal Latest
            # or trigger cleanup for the newer active Project.
            if not set_last_opened and state.last_opened_project_id != project.id:
                return path
            latest_draft_project_id = (
                None if self._has_valid_checkpoint_locked(project.id) else project.id
            )
            pending_cleanup = list(state.pending_cleanup_project_ids)
            previous_latest = state.latest_draft_project_id
            if previous_latest is not None and previous_latest != project.id:
                if previous_latest not in pending_cleanup:
                    pending_cleanup.append(previous_latest)
            committed = state.model_copy(
                update={
                    "last_opened_project_id": (
                        project.id if set_last_opened else state.last_opened_project_id
                    ),
                    "latest_draft_project_id": latest_draft_project_id,
                    "pending_cleanup_project_ids": pending_cleanup,
                }
            )
            self._write_owner_state_locked(
                owner_key,
                committed,
            )
            # The write above is the commit point.  Cleanup is intentionally
            # best-effort so a failed deletion cannot hide the new draft.
            self._cleanup_pending_locked(owner_key, committed)
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
            state = self._read_owner_state_locked(owner_key)
            return None if state is None else state.last_opened_project_id

    def load_owner_state(self, owner_key: str) -> OwnerProjectState | None:
        with self._lock:
            return self._read_owner_state_locked(owner_key)

    def set_owner_project(self, owner_key: str, project_id: UUID) -> Path:
        with self._lock:
            state = self._read_owner_state_locked(owner_key)
            if state is None:
                state = OwnerProjectState()
            return self._write_owner_state_locked(
                owner_key,
                state.model_copy(update={"last_opened_project_id": project_id}),
            )

    def clear_owner_project(
        self,
        owner_key: str,
        project_id: UUID | None = None,
    ) -> None:
        with self._lock:
            path = self._owner_state_path(owner_key)
            if not path.exists():
                return
            try:
                state = self._read_owner_state_locked(owner_key)
            except JsonStorageError:
                # A reset has no trustworthy selection to preserve.  Do not
                # infer Latest or delete any Project directory from malformed
                # content; simply neutralize this owner's marker.
                self._quarantine_owner_state_locked(owner_key)
                return
            if state is None:
                return
            if project_id is None or state.last_opened_project_id == project_id:
                replacement = state.model_copy(update={"last_opened_project_id": None})
                if (
                    replacement.latest_draft_project_id is not None
                    or replacement.pending_cleanup_project_ids
                ):
                    self._write_owner_state_locked(owner_key, replacement)
                    return
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
            quarantined_marker: Path | None = None
            try:
                matching_marker = self._read_owner_state_locked(owner_key)
            except JsonStorageError:
                # Ownership was already verified from the Project itself by
                # the facade.  A corrupt marker cannot name a safe selection,
                # so neutralize it but preserve its exact bytes for rollback
                # if the Project-directory deletion fails.
                quarantined_marker = self._quarantine_owner_state_locked(owner_key)
                matching_marker = None
            marker_changed = False
            if matching_marker is not None and (
                matching_marker.last_opened_project_id == project_id
                or matching_marker.latest_draft_project_id == project_id
            ):
                replacement = matching_marker.model_copy(
                    update={
                        "last_opened_project_id": (
                            None
                            if matching_marker.last_opened_project_id == project_id
                            else matching_marker.last_opened_project_id
                        ),
                        "latest_draft_project_id": (
                            None
                            if matching_marker.latest_draft_project_id == project_id
                            else matching_marker.latest_draft_project_id
                        ),
                        "pending_cleanup_project_ids": [
                            pending_id
                            for pending_id in matching_marker.pending_cleanup_project_ids
                            if pending_id != project_id
                        ],
                    }
                )
                marker_changed = True
                if (
                    replacement.last_opened_project_id is None
                    and replacement.latest_draft_project_id is None
                    and not replacement.pending_cleanup_project_ids
                ):
                    marker_path.unlink()
                else:
                    self._write_owner_state_locked(owner_key, replacement)
            try:
                self._delete_locked(project_id)
            except Exception:
                if matching_marker is not None and marker_changed:
                    atomic_model_write(
                        marker_path,
                        matching_marker,
                        keep_backup=False,
                    )
                elif quarantined_marker is not None:
                    try:
                        os.replace(quarantined_marker, marker_path)
                    except OSError:
                        LOGGER.exception(
                            "Unable to restore quarantined owner state after failed Project delete"
                        )
                raise
