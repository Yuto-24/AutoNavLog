from __future__ import annotations

from dataclasses import dataclass
from math import floor

from autonavlog.domain.calculation import Issue
from autonavlog.domain.enums import (
    AdoptedSource,
    IssueSeverity,
    RouteNodeRole,
)
from autonavlog.domain.planning import (
    AirportSelection,
    ArrivalAltitudeMode,
    ArrivalAltitudeResult,
    ArrivalPlan,
    PatternAltitudeValidationStatus,
    PersistedUiState,
)
from autonavlog.domain.project import Project, RouteNode
from autonavlog.nav.geodesy import geodesic_leg

from .project_fingerprints import selected_reference_fingerprint


@dataclass(frozen=True)
class ArrivalAltitudeComputation:
    result: ArrivalAltitudeResult | None
    issues: tuple[Issue, ...]


def _blocker(code: str, message: str) -> Issue:
    return Issue(
        code=code,
        severity=IssueSeverity.BLOCKER,
        message=message,
    )


def _round_half_up_nonnegative(value: float) -> int:
    if value < 0:
        raise ValueError("arrival altitude input must be non-negative")
    return floor(value + 0.5)


def standard_vrep_altitude_ft_msl(
    distance_nm: float,
    selected_pattern_altitude_ft_msl: float,
) -> int:
    """Return the NAV2 standard VREP altitude for a route preview or calculation."""
    if distance_nm < 0:
        raise ValueError("VREP distance must be non-negative")
    effective_distance = 5.0 if abs(distance_nm - 5.0) * 1852.0 <= 1.0 + 1e-9 else distance_nm
    excess_rounded = _round_half_up_nonnegative(max(0.0, effective_distance - 5.0))
    return int(selected_pattern_altitude_ft_msl) + 500 + 200 * excess_rounded


def _validate_route_vrep(
    project: Project,
    plan: ArrivalPlan,
) -> tuple[RouteNode | None, Issue | None]:
    ordered = project.ordered_nodes()
    by_id = {node.id: node for node in ordered}
    vrep = by_id.get(plan.visual_reporting_point_node_id)
    if vrep is None:
        return None, _blocker(
            "VISUAL_REPORTING_POINT_REQUIRED",
            "VREPを選択してください。",
        )
    if vrep.role != RouteNodeRole.VISUAL_REPORTING_POINT:
        return None, _blocker(
            "VISUAL_REPORTING_POINT_ROUTE_INVALID",
            "選択したVREPのRoute上の役割を確認してください。",
        )
    if len(ordered) < 2 or ordered[-2].id != vrep.id:
        return None, _blocker(
            "VISUAL_REPORTING_POINT_ROUTE_INVALID",
            "VREPは目的空港直前のRoute点にしてください。",
        )
    if ordered[-1].role != RouteNodeRole.DESTINATION:
        return None, _blocker(
            "VISUAL_REPORTING_POINT_ROUTE_INVALID",
            "VREP直後に目的空港を配置してください。",
        )
    return vrep, None


def calculate_arrival_altitude(
    project: Project,
    ui_state: PersistedUiState,
) -> ArrivalAltitudeComputation:
    plan = ui_state.arrival_plan
    snapshot = ui_state.reference_data_snapshot
    if plan is None:
        return ArrivalAltitudeComputation(
            None,
            (
                _blocker(
                    "VISUAL_REPORTING_POINT_REQUIRED",
                    "目的空港直前のVREPを確認してください。",
                ),
            ),
        )
    if snapshot is None:
        return ArrivalAltitudeComputation(
            None,
            (
                _blocker(
                    "AIRPORT_DATA_UNAVAILABLE",
                    "選択した空港参照データをProjectへ保存してください。",
                ),
            ),
        )
    destination: AirportSelection = snapshot.destination_airport
    if destination.id != project.destination_airport_id:
        return ArrivalAltitudeComputation(
            None,
            (
                _blocker(
                    "PROJECT_STATE_INVALID",
                    "目的空港IDと保存済み参照データが一致しません。",
                ),
            ),
        )
    if destination.pattern_altitude_validation_status != PatternAltitudeValidationStatus.VERIFIED:
        return ArrivalAltitudeComputation(
            None,
            (
                _blocker(
                    "PATTERN_ALTITUDE_REQUIRED",
                    "目的空港の実運用場周経路高度と出典を確認してください。",
                ),
            ),
        )
    selected_pattern = plan.selected_pattern_altitude_ft_msl
    selected_pattern_source = plan.selected_pattern_altitude_source
    if selected_pattern is None or selected_pattern_source is None:
        return ArrivalAltitudeComputation(
            None,
            (
                _blocker(
                    "PATTERN_ALTITUDE_REQUIRED",
                    "今回採用する目的空港の場周経路高度を確定してください。",
                ),
            ),
        )
    expected_pattern_source = (
        AdoptedSource.AUTOMATIC
        if selected_pattern == destination.pattern_altitude_ft_msl
        else AdoptedSource.MANUAL
    )
    if selected_pattern_source != expected_pattern_source:
        return ArrivalAltitudeComputation(
            None,
            (
                _blocker(
                    "PROJECT_STATE_INVALID",
                    "採用場周経路高度と採用元が一致しません。再確定してください。",
                ),
            ),
        )
    if selected_pattern <= destination.elevation_ft_msl:
        return ArrivalAltitudeComputation(
            None,
            (
                _blocker(
                    "PATTERN_ALTITUDE_REQUIRED",
                    "採用場周経路高度は目的空港標高より高くしてください。",
                ),
            ),
        )
    vrep, route_issue = _validate_route_vrep(project, plan)
    if route_issue is not None or vrep is None:
        return ArrivalAltitudeComputation(
            None,
            (route_issue,) if route_issue is not None else (),
        )

    distance = geodesic_leg(
        vrep.latitude_deg,
        vrep.longitude_deg,
        destination.latitude_deg,
        destination.longitude_deg,
    ).distance_nm
    effective_distance = 5.0 if abs(distance - 5.0) * 1852.0 <= 1.0 + 1e-9 else distance
    rounded_elevation = 100 * _round_half_up_nonnegative(
        float(destination.elevation_ft_msl) / 100.0
    )
    derived_pattern = selected_pattern
    base_altitude = selected_pattern + 500
    excess_exact = max(0.0, effective_distance - 5.0)
    excess_rounded = _round_half_up_nonnegative(excess_exact)
    automatic = standard_vrep_altitude_ft_msl(distance, selected_pattern)
    if plan.altitude_mode == ArrivalAltitudeMode.STANDARD_DISTANCE_RULE:
        adopted = automatic
        source = AdoptedSource.AUTOMATIC
        reason = None
    else:
        manual_adopted = plan.manual_vrep_altitude_ft_msl
        reason = (plan.manual_override_reason or "").strip()
        if manual_adopted is None or not reason:
            return ArrivalAltitudeComputation(
                None,
                (
                    _blocker(
                        "ARRIVAL_ALTITUDE_OVERRIDE_REASON_REQUIRED",
                        "変則EntryのVREP高度と理由を入力してください。",
                    ),
                ),
            )
        if manual_adopted <= destination.elevation_ft_msl:
            return ArrivalAltitudeComputation(
                None,
                (
                    _blocker(
                        "VISUAL_REPORTING_POINT_ROUTE_INVALID",
                        "手動VREP高度は目的空港標高より高くしてください。",
                    ),
                ),
            )
        adopted = manual_adopted
        source = AdoptedSource.MANUAL

    result = ArrivalAltitudeResult(
        vrep_node_id=vrep.id,
        destination_airport_id=destination.id,
        distance_nm_exact=distance,
        effective_distance_nm=effective_distance,
        airport_elevation_ft_msl=destination.elevation_ft_msl,
        airport_elevation_rounded_ft_msl=rounded_elevation,
        derived_pattern_altitude_ft_msl=derived_pattern,
        pattern_altitude_ft_msl=destination.pattern_altitude_ft_msl,
        selected_pattern_altitude_ft_msl=selected_pattern,
        selected_pattern_altitude_source=selected_pattern_source,
        base_vrep_altitude_ft_msl=base_altitude,
        excess_distance_nm_exact=excess_exact,
        excess_distance_nm_rounded=excess_rounded,
        automatic_altitude_ft_msl=automatic,
        adopted_altitude_ft_msl=adopted,
        adopted_source=source,
        manual_override_reason=reason,
        selected_reference_fingerprint=selected_reference_fingerprint(snapshot),
        rule_version="CAC_REV19_8_4_9_V4",
    )
    return ArrivalAltitudeComputation(result, ())
