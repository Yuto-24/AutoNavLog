from __future__ import annotations

from typing import Any

import pytest

from autonavlog.application.project_fingerprints import (
    current_calculation_input_fingerprint,
    defaults_review_fingerprint,
    manual_qnh_fingerprint,
)
from autonavlog.domain.enums import FlightPhase
from autonavlog.domain.planning import PersistedUiState
from autonavlog.domain.project import Project


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


def test_display_and_legacy_sea_loss_fields_do_not_change_calculation_key(
    project: Project,
    performance_repository: Any,
) -> None:
    baseline = _calculation_fingerprint(project, performance_repository)
    changed = project.model_copy(deep=True)
    changed.name = "Renamed"
    changed.pilot_name = "OTHER"
    changed.ship_identifier = "JA99XX"
    changed.sections[0].safe_enroute_altitude_ft_msl = 9999
    changed.sections[0].loss_time_seconds = 3600
    assert _calculation_fingerprint(changed, performance_repository) == baseline


@pytest.mark.parametrize(
    "mutation",
    [
        lambda project: setattr(project, "total_usable_fuel_gal", 80.0),
        lambda project: setattr(project, "default_variation_deg_east", 7.0),
        lambda project: setattr(project, "tgl_count", 1),
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


def test_defaults_review_includes_phase_and_ignores_legacy_loss(
    project: Project,
) -> None:
    baseline = defaults_review_fingerprint(
        project,
        performance_table_version="fixture-v1",
        policy_version="nav2-v2",
    )
    loss_only = project.model_copy(deep=True)
    loss_only.sections[0].loss_time_seconds = 99
    assert (
        defaults_review_fingerprint(
            loss_only,
            performance_table_version="fixture-v1",
            policy_version="nav2-v2",
        )
        == baseline
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


def test_manual_qnh_confirmation_changes_with_date_time_or_departure(
    project: Project,
) -> None:
    baseline = manual_qnh_fingerprint(
        project,
        departure_coordinate_or_airport_id="RJFM",
    )
    assert (
        manual_qnh_fingerprint(
            project,
            departure_coordinate_or_airport_id="RJFK",
        )
        != baseline
    )
