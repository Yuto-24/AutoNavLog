from __future__ import annotations

import pytest

from autonavlog.application.project_service import ProjectService
from autonavlog.domain.project import Project


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
