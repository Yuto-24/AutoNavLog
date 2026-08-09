from __future__ import annotations

from pathlib import Path

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.project_service import ProjectService
from autonavlog.domain.project import Project
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.weather.fake_provider import FakeWeatherProvider


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


def test_snapshot_rejects_corrupted_ui_state_without_writing(
    tmp_path: Path,
    project: Project,
    airports,
    performance_repository,
) -> None:
    calculation = CalculationService(airports, performance_repository)
    outcome = calculation.calculate(project, FakeWeatherProvider())
    corrupted, unserializable = _with_corrupted_ui_state(project)
    projects = ProjectService(LocalProjectRepository(tmp_path))

    with pytest.raises(ValueError, match="PROJECT_STATE_INVALID"):
        projects.snapshot(
            corrupted,
            outcome,
            calculation,
            msm_package_version=None,
        )

    assert corrupted.metadata["ui_state"]["unserializable"] is unserializable
    assert not (tmp_path / "snapshots").exists()
