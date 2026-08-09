from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from uuid import UUID

from geographiclib.geodesic import Geodesic

from autonavlog.domain.calculation import Issue
from autonavlog.domain.enums import IssueSeverity, VisualReferenceRole
from autonavlog.domain.planning import CheckPointProjection
from autonavlog.domain.project import NavSection, Project, RouteNode, VisualReference
from autonavlog.nav.geodesy import METERS_PER_NM, geodesic_leg


@dataclass(frozen=True)
class CheckPointProjectionComputation:
    projections: tuple[CheckPointProjection, ...]
    issues: tuple[Issue, ...]


def _blocker(
    code: str,
    message: str,
    *,
    section_id: UUID | None = None,
) -> Issue:
    return Issue(
        code=code,
        severity=IssueSeverity.BLOCKER,
        message=message,
        section_id=section_id,
    )


def _closest_point_on_leg(
    start: RouteNode,
    end: RouteNode,
    point: VisualReference,
) -> tuple[float, float, float, float]:
    inverse = Geodesic.WGS84.Inverse(
        start.latitude_deg,
        start.longitude_deg,
        end.latitude_deg,
        end.longitude_deg,
    )
    length_m = float(inverse["s12"])
    if length_m <= 0:
        raise ValueError("linked Section has zero geodesic length")
    line = Geodesic.WGS84.Line(
        start.latitude_deg,
        start.longitude_deg,
        float(inverse["azi1"]),
    )

    def evaluate(station_m: float) -> tuple[float, float, float]:
        position = line.Position(
            station_m,
            Geodesic.LATITUDE | Geodesic.LONGITUDE,
        )
        latitude = float(position["lat2"])
        longitude = float(position["lon2"])
        distance = float(
            Geodesic.WGS84.Inverse(
                point.latitude_deg,
                point.longitude_deg,
                latitude,
                longitude,
            )["s12"]
        )
        return distance, latitude, longitude

    left = 0.0
    right = length_m
    ratio = (sqrt(5.0) - 1.0) / 2.0
    inner_right = left + ratio * (right - left)
    inner_left = right - ratio * (right - left)
    left_value = evaluate(inner_left)[0]
    right_value = evaluate(inner_right)[0]
    for _ in range(96):
        if right - left <= 0.1:
            break
        if left_value <= right_value:
            right = inner_right
            inner_right = inner_left
            right_value = left_value
            inner_left = right - ratio * (right - left)
            left_value = evaluate(inner_left)[0]
        else:
            left = inner_left
            inner_left = inner_right
            left_value = right_value
            inner_right = left + ratio * (right - left)
            right_value = evaluate(inner_right)[0]
    station_m = (left + right) / 2.0
    cross_track_m, latitude, longitude = evaluate(station_m)
    return station_m, cross_track_m, latitude, longitude


def _adopted_section_distance(
    section: NavSection,
    start: RouteNode,
    end: RouteNode,
) -> float:
    del section
    return (
        start.manual_distance_nm
        or geodesic_leg(
            start.latitude_deg,
            start.longitude_deg,
            end.latitude_deg,
            end.longitude_deg,
        ).distance_nm
    )


def project_check_points(project: Project) -> CheckPointProjectionComputation:
    nodes = {node.id: node for node in project.route_nodes}
    sections = {section.id: section for section in project.sections}
    ordered_sections = project.ordered_sections()
    cumulative_before: dict[object, float] = {}
    cumulative = 0.0
    for section in ordered_sections:
        cumulative_before[section.id] = cumulative
        start = nodes[section.from_node_id]
        end = nodes[section.to_node_id]
        cumulative += _adopted_section_distance(section, start, end)

    projected: list[tuple[int, float, float, CheckPointProjection]] = []
    issues: list[Issue] = []
    section_sequence = {section.id: section.sequence for section in ordered_sections}
    geometric_stations: dict[object, list[float]] = {}
    check_points = sorted(
        (
            reference
            for reference in project.visual_references
            if reference.role == VisualReferenceRole.CHECK_POINT
        ),
        key=lambda item: str(item.id),
    )
    for check_point in check_points:
        if check_point.linked_section_id is None:
            issues.append(
                _blocker(
                    "CP_LINK_REQUIRED",
                    f"Check Point「{check_point.name}」の関連Legを確認してください。",
                )
            )
            continue
        linked_section = sections.get(check_point.linked_section_id)
        if linked_section is None:
            issues.append(
                _blocker(
                    "CP_LINK_REQUIRED",
                    f"Check Point「{check_point.name}」の関連Legが存在しません。",
                )
            )
            continue
        section = linked_section
        start = nodes[section.from_node_id]
        end = nodes[section.to_node_id]
        try:
            station_m, cross_track_m, latitude, longitude = _closest_point_on_leg(
                start, end, check_point
            )
        except ValueError as error:
            issues.append(
                _blocker(
                    "CP_NOT_ABEAM_LINKED_SECTION",
                    str(error),
                    section_id=section.id,
                )
            )
            continue
        physical_length_m = (
            geodesic_leg(
                start.latitude_deg,
                start.longitude_deg,
                end.latitude_deg,
                end.longitude_deg,
            ).distance_nm
            * METERS_PER_NM
        )
        if station_m <= 1.0 or physical_length_m - station_m <= 1.0:
            issues.append(
                _blocker(
                    "CP_NOT_ABEAM_LINKED_SECTION",
                    (
                        f"Check Point「{check_point.name}」のabeam点がLeg端点です。"
                        "別の関連Legを選択してください。"
                    ),
                    section_id=section.id,
                )
            )
            continue
        prior_stations = geometric_stations.setdefault(section.id, [])
        if any(abs(station_m - existing) <= 1.0 for existing in prior_stations):
            issues.append(
                _blocker(
                    "CP_NOT_ABEAM_LINKED_SECTION",
                    "同じLeg上に1 m以内で重複するCheck Pointがあります。",
                    section_id=section.id,
                )
            )
            continue
        prior_stations.append(station_m)
        fraction = station_m / physical_length_m
        adopted_distance = _adopted_section_distance(section, start, end)
        along_distance = fraction * adopted_distance
        projection = CheckPointProjection(
            checkpoint_id=check_point.id,
            section_id=section.id,
            abeam_latitude_deg=latitude,
            abeam_longitude_deg=longitude,
            along_track_fraction=fraction,
            along_section_distance_nm=along_distance,
            cumulative_distance_nm=(cumulative_before[section.id] + along_distance),
            cross_track_distance_nm=cross_track_m / METERS_PER_NM,
            policy_version="CP_ABEAM_WGS84_V1",
        )
        projected.append(
            (
                section_sequence[section.id],
                fraction,
                station_m,
                projection,
            )
        )
    projected.sort(key=lambda item: (item[0], item[1], str(item[3].checkpoint_id)))
    return CheckPointProjectionComputation(
        projections=tuple(item[3] for item in projected),
        issues=tuple(issues),
    )
