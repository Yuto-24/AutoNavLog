from __future__ import annotations

import re
from dataclasses import dataclass
from math import floor
from typing import Literal
from unicodedata import normalize

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
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg

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


RJFM_ICAO = "RJFM"
SPECIAL_RJFM_VREP_ALTITUDE_FT_MSL = 1500
SPECIAL_RJFM_VREP_MATCH_RADIUS_NM = 0.5
ARITA_COORDINATE = (31.94977931375621, 131.3535165268158)
# RJFM ARP from data/reference/default/airports.csv. SHIRAHAMA is the point
# 160°T / 5.8 NM from that ARP (the hotel at the tip of Tozaki Cape).
RJFM_ARP_COORDINATE = (31.8772222222, 131.4486111111)
SHIRAHAMA_COORDINATE = point_along_leg(*RJFM_ARP_COORDINATE, 160.0, 5.8)


@dataclass(frozen=True)
class AutomaticVrepAltitude:
    altitude_ft_msl: int
    rule: Literal["STANDARD_DISTANCE_RULE", "RJFM_ARITA_SHIRAHAMA_1500FT"]
    reason: str | None


def _normalized_vrep_name(name: str | None) -> str:
    return "" if name is None else normalize("NFKC", name).upper()


def _special_rjfm_vrep_name(name: str | None) -> str | None:
    normalized = _normalized_vrep_name(name)
    # Imported KML names commonly add ``V-REP``, altitude, and source notes.
    # Keep token boundaries so, for example, NARITA cannot match ARITA.
    if "有田" in normalized or re.search(r"(?<![A-Z0-9])ARITA(?![A-Z0-9])", normalized):
        return "ARITA"
    if "白浜" in normalized or re.search(r"(?<![A-Z0-9])SHIRAHAMA(?![A-Z0-9])", normalized):
        return "SHIRAHAMA"
    return None


def _special_rjfm_vrep_coordinate(
    latitude_deg: float | None,
    longitude_deg: float | None,
) -> str | None:
    if latitude_deg is None or longitude_deg is None:
        return None
    for name, coordinate in (
        ("ARITA", ARITA_COORDINATE),
        ("SHIRAHAMA", SHIRAHAMA_COORDINATE),
    ):
        if (
            geodesic_leg(latitude_deg, longitude_deg, *coordinate).distance_nm
            <= SPECIAL_RJFM_VREP_MATCH_RADIUS_NM + 1e-9
        ):
            return name
    return None


def automatic_vrep_altitude(
    distance_nm: float,
    selected_pattern_altitude_ft_msl: float,
    *,
    destination_icao: str | None = None,
    vrep_name: str | None = None,
    vrep_latitude_deg: float | None = None,
    vrep_longitude_deg: float | None = None,
) -> AutomaticVrepAltitude:
    """Return the automatic VREP altitude and the policy that produced it."""
    if distance_nm < 0:
        raise ValueError("VREP distance must be non-negative")
    special_vrep = None
    if (destination_icao or "").upper() == RJFM_ICAO:
        special_vrep = _special_rjfm_vrep_name(vrep_name) or _special_rjfm_vrep_coordinate(
            vrep_latitude_deg,
            vrep_longitude_deg,
        )
    if special_vrep is not None:
        return AutomaticVrepAltitude(
            altitude_ft_msl=SPECIAL_RJFM_VREP_ALTITUDE_FT_MSL,
            rule="RJFM_ARITA_SHIRAHAMA_1500FT",
            reason=f"RJFM final VREP matched {special_vrep}",
        )

    effective_distance = 5.0 if abs(distance_nm - 5.0) * 1852.0 <= 1.0 + 1e-9 else distance_nm
    excess_rounded = _round_half_up_nonnegative(max(0.0, effective_distance - 5.0))
    return AutomaticVrepAltitude(
        altitude_ft_msl=int(selected_pattern_altitude_ft_msl) + 500 + 200 * excess_rounded,
        rule="STANDARD_DISTANCE_RULE",
        reason=None,
    )


def standard_vrep_altitude_ft_msl(
    distance_nm: float,
    selected_pattern_altitude_ft_msl: float,
    *,
    destination_icao: str | None = None,
    vrep_name: str | None = None,
    vrep_latitude_deg: float | None = None,
    vrep_longitude_deg: float | None = None,
) -> int:
    """Return the NAV2 standard VREP altitude for a route preview or calculation."""
    return automatic_vrep_altitude(
        distance_nm,
        selected_pattern_altitude_ft_msl,
        destination_icao=destination_icao,
        vrep_name=vrep_name,
        vrep_latitude_deg=vrep_latitude_deg,
        vrep_longitude_deg=vrep_longitude_deg,
    ).altitude_ft_msl


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
    automatic_policy = automatic_vrep_altitude(
        distance,
        selected_pattern,
        destination_icao=destination.icao,
        vrep_name=vrep.name,
        vrep_latitude_deg=vrep.latitude_deg,
        vrep_longitude_deg=vrep.longitude_deg,
    )
    automatic = automatic_policy.altitude_ft_msl
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
        automatic_altitude_rule=automatic_policy.rule,
        automatic_altitude_reason=automatic_policy.reason,
        rule_version="CAC_REV19_8_4_9_V5",
    )
    return ArrivalAltitudeComputation(result, ())
