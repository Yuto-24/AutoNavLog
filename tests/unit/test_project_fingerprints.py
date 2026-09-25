from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

import autonavlog.application.project_fingerprints as project_fingerprints
from autonavlog.application.project_fingerprints import (
    current_calculation_input_fingerprint,
    defaults_review_fingerprint,
    forecast_selection_fingerprint,
)
from autonavlog.domain.enums import FlightPhase
from autonavlog.domain.planning import PersistedUiState
from autonavlog.domain.project import FtdWeatherSettings, ManualWind, Project


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: setattr(p, "flight_date", p.flight_date + timedelta(days=1)),
        lambda p: setattr(p, "planned_departure_time_jst",
                          p.planned_departure_time_jst + timedelta(minutes=1)),
        lambda p: setattr(p, "departure_airport_id", "RJFO"),
        lambda p: setattr(p, "destination_airport_id", "RJFT"),
        lambda p: setattr(p, "tgl_count", p.tgl_count + 1),
        lambda p: setattr(p, "descent_rate_fpm", 1000),
        lambda p: setattr(p.sections[0], "planned_altitude_ft_msl", 6500),
        lambda p: setattr(p.sections[0], "manual_tas_kt", 110),
        lambda p: setattr(p.route_nodes[0], "longitude_deg",
                          p.route_nodes[0].longitude_deg + 0.1),
        lambda p: p.metadata.update(ui_state={"arrival_plan": {"altitude": 2000}}),
    ],
)
def test_forecast_refresh_intent_changes_with_requirement_inputs(project, mutation):
    baseline = forecast_selection_fingerprint(project)
    mutation(project)
    assert forecast_selection_fingerprint(project) != baseline


def test_forecast_refresh_intent_survives_selection_and_save_bookkeeping(project):
    baseline = forecast_selection_fingerprint(project)
    project.selected_forecast_run_id = "20260915000000"
    project.revision += 1
    project.name = "Saved flight"
    project.pilot_name = "Pilot"
    project.acknowledged_warning_codes.add("FORECAST_UPDATE_AVAILABLE")
    project.metadata["ui_state"] = {"calculated_against_fingerprint": "0" * 64}
    assert forecast_selection_fingerprint(project) == baseline


def _calculation_fingerprint(
    project: Project,
    performance_repository: Any,
) -> str:
    return current_calculation_input_fingerprint(
        project,
        ui_state=PersistedUiState(),
        performance=performance_repository,
        calculation_policy_version="nav2-v2",
        performance_table_version="fixture-v1",
        autonavlog_version="0.2.0",
        msm_package_version="0.2.1",
    )

def test_display_only_fields_do_not_change_calculation_key(
    project: Project,
    performance_repository: Any,
) -> None:
    baseline = _calculation_fingerprint(project, performance_repository)
    changed = project.model_copy(deep=True)
    changed.name = "Renamed"
    changed.pilot_name = "OTHER"
    changed.ship_identifier = "JA99XX"
    changed.route_nodes[1].name = "Renamed point"
    assert _calculation_fingerprint(changed, performance_repository) == baseline


@pytest.mark.parametrize(
    "mutation",
    [
        lambda project: setattr(project, "total_usable_fuel_gal", 80.0),
        lambda project: setattr(project, "tgl_count", 1),
        lambda project: setattr(project, "run_up_included", False),
        lambda project: setattr(project, "nose_fairing_enabled", True),
        lambda project: setattr(project, "air_conditioning_enabled", False),
        lambda project: setattr(project, "descent_rate_fpm", 1000),
        lambda project: setattr(
            project.sections[0],
            "planned_altitude_ft_msl",
            5500,
        ),
        lambda project: setattr(
            project.sections[0],
            "phase",
            FlightPhase.CRUISE,
        ),
        lambda project: setattr(
            project.route_nodes[1],
            "latitude_deg",
            project.route_nodes[1].latitude_deg + 0.01,
        ),
    ],
)
def test_nav_fuel_heading_inputs_change_calculation_key(
    project: Project,
    performance_repository: Any,
    mutation: Any,
) -> None:
    baseline = _calculation_fingerprint(project, performance_repository)
    changed = project.model_copy(deep=True)
    mutation(changed)
    assert _calculation_fingerprint(changed, performance_repository) != baseline


def test_legacy_default_variation_does_not_change_calculation_key(
    project: Project,
    performance_repository: Any,
) -> None:
    baseline = _calculation_fingerprint(project, performance_repository)
    changed = project.model_copy(deep=True)
    changed.default_variation_deg_east = -12.5

    assert _calculation_fingerprint(changed, performance_repository) == baseline


def test_phase_specific_manual_tas_changes_calculation_key(
    project: Project,
    performance_repository: Any,
) -> None:
    baseline = _calculation_fingerprint(project, performance_repository)
    changed = project.model_copy(deep=True)
    changed.sections[0].manual_tas_kt_by_phase = {FlightPhase.CRUISE: 140.0}

    assert _calculation_fingerprint(changed, performance_repository) != baseline


def test_variation_rule_version_changes_calculation_key(
    project: Project,
    performance_repository: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _calculation_fingerprint(project, performance_repository)
    monkeypatch.setattr(
        project_fingerprints,
        "VARIATION_RULE_VERSION",
        "DEPARTURE_LATITUDE_32N_V2",
    )
    assert _calculation_fingerprint(project, performance_repository) != baseline


def test_ftd_weather_and_policy_change_calculation_key_without_msm_dependency(
    project: Project,
    performance_repository: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ftd_project = project.model_copy(
        update={
            "weather_mode": "FTD",
            "ftd_weather": FtdWeatherSettings(
                surface_wind=ManualWind(direction_deg_from=350, speed_kt=10),
                wind_at_5000_ft=ManualWind(direction_deg_from=10, speed_kt=20),
            ),
        }
    )
    baseline = _calculation_fingerprint(ftd_project, performance_repository)
    without_msm_version = current_calculation_input_fingerprint(
        ftd_project,
        ui_state=PersistedUiState(),
        performance=performance_repository,
        calculation_policy_version="nav2-v2",
        performance_table_version="fixture-v1",
        autonavlog_version="0.2.0",
        msm_package_version=None,
    )
    assert without_msm_version == baseline

    changed_wind = ftd_project.model_copy(deep=True)
    assert changed_wind.ftd_weather is not None
    changed_wind.ftd_weather.wind_at_5000_ft.speed_kt = 21
    assert _calculation_fingerprint(changed_wind, performance_repository) != baseline

    monkeypatch.setattr(
        project_fingerprints,
        "FTD_WEATHER_POLICY_VERSION",
        "FTD_VECTOR_ISA_V2",
    )
    assert _calculation_fingerprint(ftd_project, performance_repository) != baseline


def test_defaults_review_includes_phase(
    project: Project,
) -> None:
    baseline = defaults_review_fingerprint(
        project,
        performance_table_version="fixture-v1",
        policy_version="nav2-v2",
    )
    changed_phase = project.model_copy(deep=True)
    changed_phase.sections[0].phase = FlightPhase.CRUISE
    assert (
        defaults_review_fingerprint(
            changed_phase,
            performance_table_version="fixture-v1",
            policy_version="nav2-v2",
        )
        != baseline
    )


def test_defaults_review_includes_descent_rate(project: Project) -> None:
    baseline = defaults_review_fingerprint(
        project,
        performance_table_version="fixture-v1",
        policy_version="nav2-v2",
    )
    changed = project.model_copy(deep=True)
    changed.descent_rate_fpm = 1000

    assert (
        defaults_review_fingerprint(
            changed,
            performance_table_version="fixture-v1",
            policy_version="nav2-v2",
        )
        != baseline
    )
