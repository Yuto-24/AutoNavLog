from __future__ import annotations

from uuid import UUID

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.project_service import ProjectService
from autonavlog.domain.enums import ProjectStatus
from autonavlog.presentation.clearcopy import render_clearcopy_html
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.weather.fake_provider import FakeWeatherProvider


def test_full_calculation_iteration_and_clearcopy(
    airports,
    performance_repository,
    project,
) -> None:
    service = CalculationService(airports, performance_repository)
    provider = FakeWeatherProvider()
    outcome = service.calculate(project, provider)
    assert outcome.converged
    assert outcome.selected_forecast_run_id == "20260728000000"
    assert len(outcome.sections) == 2
    assert not outcome.blockers
    assert outcome.status == ProjectStatus.READY_FOR_COPY
    assert outcome.derived_points[0].type.value == "RCA"
    assert outcome.sections[-1].remaining_fuel_gal.adopted() is not None
    assert len(provider.query_history) >= 2
    assert all(run_id == "20260728000000" for run_id, _ in provider.query_history)
    html = render_clearcopy_html(project, outcome)
    assert "PILOT" in html
    assert "ZONE DIST" in html
    assert "MSM推定QNH" in html
    assert "1013 hPa" in html


def test_outcome_adoption_and_snapshot_round_trip(
    airports,
    performance_repository,
    project,
    tmp_path,
) -> None:
    calculation = CalculationService(airports, performance_repository)
    outcome = calculation.calculate(project, FakeWeatherProvider())
    assert project.selected_forecast_run_id is None
    repository = LocalProjectRepository(tmp_path)
    projects = ProjectService(repository)
    adopted = projects.apply_calculation_outcome(project, outcome)
    assert adopted.selected_forecast_run_id == outcome.selected_forecast_run_id
    saved = projects.save(adopted).project
    snapshot_path = projects.snapshot(
        saved,
        outcome,
        calculation,
        msm_package_version=None,
    )
    restored = repository.load_snapshot(saved.id, UUID(snapshot_path.stem))
    assert restored.calculation_results.model_dump(
        mode="json"
    ) == outcome.model_dump(mode="json")
    assert restored.input_data.revision == saved.revision


def test_unverified_performance_is_blocking(airports, project) -> None:
    from autonavlog.performance.repository import PerformanceRepository
    from autonavlog.performance.schemas import PerformanceManifest

    unverified = PerformanceRepository(
        PerformanceManifest(
            aircraft="SR22 G6",
            source_document="pending",
            source_revision="pending",
            verified_against="pending",
        ),
        [],
        [],
    )
    outcome = CalculationService(airports, unverified).calculate(
        project,
        FakeWeatherProvider(),
    )
    assert any(issue.code == "PERFORMANCE_DATA_UNAVAILABLE" for issue in outcome.blockers)
    assert outcome.status == ProjectStatus.MANUAL_INPUT_REQUIRED
