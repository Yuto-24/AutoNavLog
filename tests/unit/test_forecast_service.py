from __future__ import annotations

from autonavlog.application.forecast_service import ForecastService
from autonavlog.domain.enums import FlightPhase


def test_initial_forecast_timing_uses_explicit_pre_segmentation_estimate(project) -> None:
    baseline = ForecastService().build_initial_requirement(project)
    changed = project.model_copy(deep=True)
    changed.sections[0].manual_tas_kt_by_phase = {
        FlightPhase.CLIMB: 40.0,
        FlightPhase.CRUISE: 240.0,
    }

    assert ForecastService().build_initial_requirement(changed) == baseline
