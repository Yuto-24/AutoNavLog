from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from autonavlog.application.rjfm_coordinate_matcher import coordinate_matches_reference
from autonavlog.domain.enums import FlightPhase, RouteNodeNameSource, RouteNodeRole
from autonavlog.domain.planning import (
    PersistedUiState,
    RjfmCoordinate,
    RjfmInboundPlan,
    load_persisted_ui_state,
)
from autonavlog.domain.project import NavSection, Project, RouteNode

TARGET_ALTITUDE_FT_MSL = 4500.0
RJFM_INPUT_MODE_OMARU_TO_UMK_FIXED = "RJFM_INBOUND_OMARU_TO_UMK_FIXED"
_ORIGINAL_SECTIONS_METADATA_KEY = "rjfm_inbound_exception_original_sections"


@dataclass(frozen=True)
class RjfmInboundReferences:
    revision: str
    content_fingerprint: str
    umk: RjfmCoordinate
    omaru: RjfmCoordinate


def apply_rjfm_inbound_exception(
    project: Project,
    references: RjfmInboundReferences,
    *,
    adopted_vrep_altitude_ft_msl: int | None = None,
) -> RjfmInboundPlan | None:
    """Apply the physical OMARU→UMK fixed-altitude return policy.

    The profile is recognized only from physical coordinates and route order.
    It never inserts a point or changes any course/distance input.
    """

    _restore_original_sections(project)
    if project.destination_airport_id.strip().upper() != "RJFM":
        _clear_plan(project)
        return None
    nodes = project.ordered_nodes()
    sections = project.ordered_sections()
    if len(nodes) < 4 or len(sections) != len(nodes) - 1:
        _clear_plan(project)
        return None
    omaru_index = _matching_index(nodes, references.omaru)
    if omaru_index is None:
        _clear_plan(project)
        return None
    umk_index = _matching_index(nodes, references.umk, start=omaru_index + 1)
    if umk_index is None or umk_index <= omaru_index:
        _clear_plan(project)
        return None
    vrep_index = _final_vrep_index(nodes)
    if vrep_index is None or vrep_index <= umk_index:
        _clear_plan(project)
        return None
    controlled_sections = sections[omaru_index:umk_index]
    if not _sections_link_nodes(controlled_sections, nodes[omaru_index : umk_index + 1]):
        _clear_plan(project)
        return None

    _capture_original_sections(project, controlled_sections)
    for controlled in controlled_sections:
        controlled.phase = FlightPhase.CRUISE
        controlled.planned_altitude_ft_msl = TARGET_ALTITUDE_FT_MSL
    _normalize_reference_name(nodes[omaru_index], "OMARU")
    _normalize_reference_name(nodes[umk_index], "UMK")
    plan = RjfmInboundPlan(
        application_reason="RJFM destination and physical OMARU→UMK→final VREP coordinates matched",
        omaru_node_id=nodes[omaru_index].id,
        umk_node_id=nodes[umk_index].id,
        vrep_node_id=nodes[vrep_index].id,
        controlled_section_id=controlled_sections[0].id,
        controlled_section_ids=tuple(section.id for section in controlled_sections),
        omaru_coordinate=RjfmCoordinate(
            latitude_deg=nodes[omaru_index].latitude_deg,
            longitude_deg=nodes[omaru_index].longitude_deg,
            source="KML:OMARU",
            estimated_error_nm=0.0,
        ),
        umk_coordinate=RjfmCoordinate(
            latitude_deg=nodes[umk_index].latitude_deg,
            longitude_deg=nodes[umk_index].longitude_deg,
            source="KML:UMK",
            estimated_error_nm=0.0,
        ),
        adopted_vrep_altitude_ft_msl=adopted_vrep_altitude_ft_msl,
        descent_rate_fpm=project.descent_rate_fpm,
        reference_revision=references.revision,
        reference_content_fingerprint=references.content_fingerprint,
    )
    _store_plan(project, plan)
    return plan


def rjfm_inbound_plan_matches_project(project: Project, plan: RjfmInboundPlan) -> bool:
    if project.destination_airport_id.strip().upper() != "RJFM":
        return False
    nodes = project.ordered_nodes()
    sections = project.ordered_sections()
    node_index = {node.id: index for index, node in enumerate(nodes)}
    try:
        omaru_index = node_index[plan.omaru_node_id]
        umk_index = node_index[plan.umk_node_id]
        vrep_index = node_index[plan.vrep_node_id]
    except KeyError:
        return False
    if umk_index <= omaru_index or vrep_index <= umk_index:
        return False
    if not (
        coordinate_matches_reference(
            nodes[omaru_index].latitude_deg,
            nodes[omaru_index].longitude_deg,
            plan.omaru_coordinate.latitude_deg,
            plan.omaru_coordinate.longitude_deg,
            tolerance_nm=1e-6,
        )
        and coordinate_matches_reference(
            nodes[umk_index].latitude_deg,
            nodes[umk_index].longitude_deg,
            plan.umk_coordinate.latitude_deg,
            plan.umk_coordinate.longitude_deg,
            tolerance_nm=1e-6,
        )
        and plan.descent_rate_fpm == project.descent_rate_fpm
    ):
        return False
    if _final_vrep_index(nodes) != vrep_index:
        return False
    if omaru_index >= len(sections):
        return False
    expected_ids = tuple(section.id for section in sections[omaru_index:umk_index])
    controlled_ids = plan.controlled_section_ids or (plan.controlled_section_id,)
    if controlled_ids != expected_ids:
        return False
    controlled_sections = sections[omaru_index:umk_index]
    return all(
        section.planned_altitude_ft_msl == TARGET_ALTITUDE_FT_MSL
        and section.phase == FlightPhase.CRUISE
        and section.from_node_id == nodes[omaru_index + offset].id
        and section.to_node_id == nodes[omaru_index + offset + 1].id
        for offset, section in enumerate(controlled_sections)
    )


def rjfm_inbound_section_input_modes(
    project: Project,
    plan: RjfmInboundPlan,
) -> dict[str, str]:
    if not rjfm_inbound_plan_matches_project(project, plan):
        return {}
    return {
        str(section_id): RJFM_INPUT_MODE_OMARU_TO_UMK_FIXED
        for section_id in (plan.controlled_section_ids or (plan.controlled_section_id,))
    }


def _sections_link_nodes(
    sections: list[NavSection],
    nodes: list[RouteNode],
) -> bool:
    return len(sections) + 1 == len(nodes) and all(
        section.from_node_id == start.id and section.to_node_id == end.id
        for section, (start, end) in zip(sections, zip(nodes, nodes[1:], strict=False), strict=True)
    )


def _matching_index(
    nodes: list[RouteNode],
    reference: RjfmCoordinate,
    *,
    start: int = 0,
) -> int | None:
    return next(
        (
            index
            for index, node in enumerate(nodes[start:], start=start)
            if coordinate_matches_reference(
                node.latitude_deg,
                node.longitude_deg,
                reference.latitude_deg,
                reference.longitude_deg,
            )
        ),
        None,
    )


def _final_vrep_index(nodes: list[RouteNode]) -> int | None:
    if len(nodes) < 2 or nodes[-1].role != RouteNodeRole.DESTINATION:
        return None
    index = len(nodes) - 2
    return index if nodes[index].role == RouteNodeRole.VISUAL_REPORTING_POINT else None


def _normalize_reference_name(node: RouteNode, name: str) -> None:
    if (
        node.name_source == RouteNodeNameSource.USER
        or (
            node.name_source == RouteNodeNameSource.IMPORTED
            and node.source == "KML/KMZ Point"
        )
        or node.name.strip().upper() == name
    ):
        return
    node.name = name
    node.name_source = RouteNodeNameSource.GENERATED


def _capture_original_sections(project: Project, sections: Iterable[NavSection]) -> None:
    raw = project.metadata.get(_ORIGINAL_SECTIONS_METADATA_KEY)
    stored = dict(raw) if isinstance(raw, dict) else {}
    for section in sections:
        stored.setdefault(
            str(section.id),
            {
                "phase": section.phase.value,
                "planned_altitude_ft_msl": section.planned_altitude_ft_msl,
            },
        )
    project.metadata[_ORIGINAL_SECTIONS_METADATA_KEY] = stored


def _restore_original_sections(project: Project) -> None:
    raw = project.metadata.pop(_ORIGINAL_SECTIONS_METADATA_KEY, None)
    if not isinstance(raw, dict):
        return
    for section in project.ordered_sections():
        stored = raw.get(str(section.id))
        if not isinstance(stored, dict):
            continue
        try:
            section.phase = FlightPhase(stored["phase"])
            section.planned_altitude_ft_msl = float(stored["planned_altitude_ft_msl"])
        except (KeyError, TypeError, ValueError):
            continue


def _state(project: Project) -> PersistedUiState:
    raw = project.metadata.get("ui_state")
    return PersistedUiState() if raw is None else load_persisted_ui_state(raw)


def _store_plan(project: Project, plan: RjfmInboundPlan) -> None:
    project.metadata["ui_state"] = (
        _state(project).model_copy(update={"rjfm_inbound_plan": plan}).model_dump(mode="json")
    )


def _clear_plan(project: Project) -> None:
    raw = project.metadata.get("ui_state")
    if raw is None:
        return None
    project.metadata["ui_state"] = (
        _state(project).model_copy(update={"rjfm_inbound_plan": None}).model_dump(mode="json")
    )
    return None
