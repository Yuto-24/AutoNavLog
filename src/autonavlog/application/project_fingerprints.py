from __future__ import annotations

from typing import Any

from autonavlog.domain.enums import VisualReferenceRole
from autonavlog.domain.planning import (
    CP_PROJECTION_POLICY_VERSION,
    PersistedUiState,
    ReferenceDataSnapshot,
)
from autonavlog.domain.project import Project
from autonavlog.nav.variation import VARIATION_RULE_VERSION
from autonavlog.performance.repository import PerformanceRepository

from .fingerprints import make_fingerprint


def selected_reference_fingerprint(snapshot: ReferenceDataSnapshot) -> str:
    return make_fingerprint(
        kind="selected_reference",
        fields=snapshot.model_dump(mode="python"),
    )


def performance_content_fingerprint(
    performance: PerformanceRepository,
) -> str:
    explicit = getattr(performance, "content_fingerprint", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    return make_fingerprint(
        kind="performance_content",
        fields={
            "manifest": performance.manifest.model_dump(mode="python"),
            "climb_rows": [row.model_dump(mode="python") for row in performance.climb_rows],
            "cruise_rows": [row.model_dump(mode="python") for row in performance.cruise_rows],
        },
    )


def current_calculation_input_fingerprint(
    project: Project,
    *,
    ui_state: PersistedUiState,
    performance: PerformanceRepository,
    calculation_policy_version: str,
    performance_table_version: str | None,
    autonavlog_version: str,
    msm_package_version: str | None,
) -> str:
    route_nodes = [
        {
            "id": node.id,
            "name": node.name,
            "latitude_deg": node.latitude_deg,
            "longitude_deg": node.longitude_deg,
            "role": node.role,
            "sequence": node.sequence,
            "manual_true_course_deg": node.manual_true_course_deg,
            "manual_distance_nm": node.manual_distance_nm,
            "source": node.source,
        }
        for node in project.ordered_nodes()
    ]
    sections = [
        {
            "id": section.id,
            "sequence": section.sequence,
            "from_node_id": section.from_node_id,
            "to_node_id": section.to_node_id,
            "planned_altitude_ft_msl": section.planned_altitude_ft_msl,
            "phase": section.phase,
            "manual_wind_direction_deg": (section.manual_wind_direction_deg),
            "manual_wind_speed_kt": section.manual_wind_speed_kt,
            "manual_temperature_c": section.manual_temperature_c,
            "manual_temperature_c_by_phase": (
                section.manual_temperature_c_by_phase
            ),
            "manual_tas_kt": section.manual_tas_kt,
        }
        for section in project.ordered_sections()
    ]
    check_points = [
        {
            "id": reference.id,
            "name": reference.name,
            "latitude_deg": reference.latitude_deg,
            "longitude_deg": reference.longitude_deg,
            "source": reference.source,
            "linked_section_id": reference.linked_section_id,
            "role": reference.role,
        }
        for reference in sorted(
            (
                item
                for item in project.visual_references
                if item.role == VisualReferenceRole.CHECK_POINT
            ),
            key=lambda item: str(item.id),
        )
    ]
    reference_snapshot = ui_state.reference_data_snapshot
    return make_fingerprint(
        kind="calculation_input",
        fields={
            "flight_date": project.flight_date,
            "planned_departure_time_jst": (project.planned_departure_time_jst),
            "total_usable_fuel_gal": project.total_usable_fuel_gal,
            "tgl_count": project.tgl_count,
            "aircraft_profile_id": project.aircraft_profile_id,
            "manual_qnh_hpa": project.manual_qnh_hpa,
            "selected_forecast_run_id": project.selected_forecast_run_id,
            "route_nodes": route_nodes,
            "sections": sections,
            "arrival_plan": (
                None
                if ui_state.arrival_plan is None
                else ui_state.arrival_plan.model_dump(mode="python")
            ),
            "check_points": check_points,
            "selected_reference_snapshot": (
                None if reference_snapshot is None else reference_snapshot.model_dump(mode="python")
            ),
            "cp_projection_policy_version": CP_PROJECTION_POLICY_VERSION,
            "performance_content_fingerprint": (performance_content_fingerprint(performance)),
            "performance_manifest": {
                "source_document": performance.manifest.source_document,
                "source_revision": performance.manifest.source_revision,
                "validation_status": (performance.manifest.validation_status),
                "climb_temperature_policy": (performance.manifest.climb_temperature_policy),
            },
            "calculation_policy_version": calculation_policy_version,
            "variation_rule_version": VARIATION_RULE_VERSION,
            "performance_table_version": performance_table_version,
            "autonavlog_version": autonavlog_version,
            "msm_package_version": msm_package_version,
        },
    )


def defaults_review_fingerprint(
    project: Project,
    *,
    performance_table_version: str | None,
    policy_version: str,
    default_altitude_ft_msl: float = 5000.0,
) -> str:
    return make_fingerprint(
        kind="defaults_review",
        fields={
            "default_altitude_ft_msl": default_altitude_ft_msl,
            "sections": [
                {
                    "id": section.id,
                    "uses_default_altitude": (
                        section.planned_altitude_ft_msl == default_altitude_ft_msl
                    ),
                    "phase": section.phase,
                }
                for section in project.ordered_sections()
            ],
            "total_usable_fuel_gal": project.total_usable_fuel_gal,
            "tgl_count": project.tgl_count,
            "aircraft_profile_id": project.aircraft_profile_id,
            "performance_table_version": performance_table_version,
            "policy_version": policy_version,
        },
    )


def manual_qnh_fingerprint(
    project: Project,
    *,
    departure_coordinate_or_airport_id: Any,
) -> str:
    return make_fingerprint(
        kind="manual_qnh",
        fields={
            "flight_date": project.flight_date,
            "planned_departure_time_jst": (project.planned_departure_time_jst),
            "departure_coordinate_or_airport_id": (departure_coordinate_or_airport_id),
        },
    )
