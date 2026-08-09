from __future__ import annotations

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.readiness_service import ReadinessService
from autonavlog.domain.planning import load_persisted_ui_state
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.weather.fake_provider import FakeWeatherProvider


@pytest.mark.parametrize(
    ("validation_status", "expected_reason"),
    [
        ("UNVERIFIED", "UNVERIFIED"),
        ("PENDING", "PENDING"),
        ("REJECTED", "REJECTED"),
        ("FUTURE_STATE", "UNKNOWN_STATUS"),
        ("", "UNKNOWN_STATUS"),
    ],
)
def test_performance_validation_status_uses_closed_reason_set(
    airports,
    performance_repository,
    project,
    validation_status: str,
    expected_reason: str,
) -> None:
    performance = PerformanceRepository(
        performance_repository.manifest.model_copy(
            update={"validation_status": validation_status},
        ),
        performance_repository.climb_rows,
        performance_repository.cruise_rows,
    )
    evaluation = (
        ReadinessService(
            CalculationService(airports, performance),
            msm_package_version=None,
        )
        .evaluate(project, None)
        .evaluation
    )

    issue = next(
        item.issue
        for item in evaluation.effective_issues
        if item.ctx.code == "PERFORMANCE_DATA_UNVERIFIED"
    )
    assert issue.metadata["reason"] == expected_reason


def test_record_calculation_initializes_only_absent_ui_state(
    airports,
    performance_repository,
    project,
) -> None:
    calculation = CalculationService(airports, performance_repository)
    outcome = calculation.calculate(project, FakeWeatherProvider())
    service = ReadinessService(
        calculation,
        msm_package_version=None,
    )

    materialized = service.record_calculation(project, outcome)

    state = load_persisted_ui_state(materialized.project.metadata["ui_state"])
    assert state.calculated_against_fingerprint == (
        materialized.fingerprints.calculation_input
    )
    assert "ui_state" not in project.metadata


def test_record_calculation_rejects_corrupted_ui_state_without_replacing_it(
    airports,
    performance_repository,
    project,
) -> None:
    calculation = CalculationService(airports, performance_repository)
    outcome = calculation.calculate(project, FakeWeatherProvider())
    service = ReadinessService(
        calculation,
        msm_package_version=None,
    )
    unserializable = object()
    project.metadata["ui_state"] = {
        "state_schema_version": 4,
        "unserializable": unserializable,
    }

    with pytest.raises(ValueError, match="PROJECT_STATE_INVALID"):
        service.record_calculation(project, outcome)

    assert project.metadata["ui_state"]["unserializable"] is unserializable
