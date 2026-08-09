from __future__ import annotations

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.readiness_service import ReadinessService
from autonavlog.performance.repository import PerformanceRepository


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
