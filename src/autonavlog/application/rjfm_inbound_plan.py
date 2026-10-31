from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import uuid4

from autonavlog.application.arrival import ARITA_COORDINATE, SPECIAL_RJFM_VREP_MATCH_RADIUS_NM
from autonavlog.application.checkpoints import project_check_points
from autonavlog.application.rjfm_coordinate_matcher import coordinate_matches_reference
from autonavlog.domain.enums import (
    FlightPhase,
    RouteNodeNameSource,
    RouteNodeRole,
    VisualReferenceRole,
)
from autonavlog.domain.planning import (
    CheckPointProjection,
    PersistedUiState,
    RjfmCoordinate,
    RjfmInboundPlan,
    load_persisted_ui_state,
)
from autonavlog.domain.project import NavSection, Project, RouteNode

TARGET_ALTITUDE_FT_MSL = 4500.0
RJFM_INPUT_MODE_OMARU_TO_UMK_FIXED = "RJFM_INBOUND_OMARU_TO_UMK_FIXED"
_ORIGINAL_SECTIONS_METADATA_KEY = "rjfm_inbound_exception_original_sections"
_ARITA_SOURCE = "RJFM_INBOUND_ARITA:"
_ARITA_ORIGINAL = "rjfm_inbound_arita_original"
_SYNTHETIC_UMK_SOURCE = "RJFM_INBOUND_EXCEPTION:"
_ORIGINAL_GEOMETRY_METADATA_KEY = "rjfm_inbound_exception_original_geometry"


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
    """Complete the OMARU→UMK→VREP route and apply the return policy.

    Match coordinates, preserving an explicit downstream UMK and final VREP.
    Direct returns without a separate VREP await their own descent policy.
    """

    _restore_original_sections(project)
    _reconcile_arita(project, references)
    _reconcile_umk(project, references)
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


def is_arita_node(node: RouteNode) -> bool:
    return coordinate_matches_reference(
        node.latitude_deg,
        node.longitude_deg,
        *ARITA_COORDINATE,
        tolerance_nm=SPECIAL_RJFM_VREP_MATCH_RADIUS_NM,
    )


def _relink_split_checkpoints(project: Project, original: NavSection, inserted: NavSection) -> None:
    for reference in project.visual_references:
        if (
            reference.role != VisualReferenceRole.CHECK_POINT
            or reference.linked_section_id != original.id
        ):
            continue
        projections: list[CheckPointProjection] = []
        for section in (inserted, original):
            probe = project.model_copy(
                update={
                    "visual_references": [
                        reference.model_copy(update={"linked_section_id": section.id})
                    ]
                }
            )
            projections.extend(project_check_points(probe).projections)
        if projections:
            reference.linked_section_id = min(
                projections, key=lambda p: p.cross_track_distance_nm
            ).section_id


def _reconcile_arita(project: Project, references: RjfmInboundReferences) -> None:
    nodes = project.ordered_nodes()
    sections = project.ordered_sections()
    if not _sections_link_nodes(sections, nodes) or len(nodes) < 2:
        return
    omaru_index = _matching_index(nodes, references.omaru)
    active = project.destination_airport_id.strip().upper() == "RJFM" and omaru_index is not None
    generated = next((n for n in nodes if n.source.startswith(_ARITA_SOURCE)), None)
    if generated is not None:
        other_arita = next(
            (
                n
                for n in nodes[(omaru_index + 1) if omaru_index is not None else 0 :]
                if n.id != generated.id and is_arita_node(n)
            ),
            None,
        )
        if active and other_arita is None and nodes[-2].id == generated.id:
            return
        _remove_synthetic_umks(project)
        nodes, sections = project.ordered_nodes(), project.ordered_sections()
        incoming = next(s for s in sections if s.to_node_id == generated.id)
        outgoing = next(s for s in sections if s.from_node_id == generated.id)
        outgoing.from_node_id = incoming.from_node_id
        for cp in project.visual_references:
            if cp.linked_section_id == incoming.id:
                cp.linked_section_id = outgoing.id
        project.sections = [s for s in sections if s.id != incoming.id]
        project.route_nodes = [n for n in nodes if n.id != generated.id]
        original = project.metadata.pop(_ARITA_ORIGINAL, {})
        if isinstance(original, dict) and {"phase", "altitude", "role"}.issubset(original):
            predecessor = next(
                (n for n in project.route_nodes if str(n.id) == original.get("predecessor")), None
            )
            if (
                predecessor is not None
                and outgoing.from_node_id == predecessor.id
                and str(outgoing.to_node_id) == original.get("successor")
            ):
                predecessor.manual_true_course_deg = original.get("manual_true_course_deg")
                predecessor.manual_distance_nm = original.get("manual_distance_nm")
                predecessor.role = RouteNodeRole(original["role"])
                outgoing.phase = FlightPhase(original["phase"])
                outgoing.planned_altitude_ft_msl = original["altitude"]
        for i, n in enumerate(project.ordered_nodes()):
            n.sequence = i
        for i, s in enumerate(project.ordered_sections()):
            s.sequence = i
        completed = project.ordered_nodes()
        if active and len(completed) >= 3 and is_arita_node(completed[-2]):
            for node in completed[1:-2]:
                if node.role == RouteNodeRole.VISUAL_REPORTING_POINT:
                    node.role = RouteNodeRole.ROUTE_POINT
            completed[-2].role = RouteNodeRole.VISUAL_REPORTING_POINT
        state = _state(project)
        if (
            state.arrival_plan is not None
            and state.arrival_plan.visual_reporting_point_node_id == generated.id
        ):
            old_vrep = _final_vrep_index(project.ordered_nodes())
            arrival = (
                None
                if old_vrep is None
                else state.arrival_plan.model_copy(
                    update={"visual_reporting_point_node_id": project.ordered_nodes()[old_vrep].id}
                )
            )
            project.metadata["ui_state"] = state.model_copy(
                update={"arrival_plan": arrival}
            ).model_dump(mode="json")
    if not active:
        return
    nodes, sections = project.ordered_nodes(), project.ordered_sections()
    omaru_index = _matching_index(nodes, references.omaru)
    assert omaru_index is not None
    if any(is_arita_node(n) for n in nodes[omaru_index + 1 :]):
        if is_arita_node(nodes[-2]):
            for node in nodes[1:-2]:
                if node.role == RouteNodeRole.VISUAL_REPORTING_POINT:
                    node.role = RouteNodeRole.ROUTE_POINT
            nodes[-2].role = RouteNodeRole.VISUAL_REPORTING_POINT
        return
    # Complete only the agreed direct tail (OMARU or UMK immediately before RJFM).
    predecessor = nodes[-2]
    if predecessor.id != nodes[omaru_index].id and not coordinate_matches_reference(
        predecessor.latitude_deg,
        predecessor.longitude_deg,
        references.umk.latitude_deg,
        references.umk.longitude_deg,
    ):
        return
    if omaru_index >= len(nodes) - 1:
        return
    remainder = sections[-1]
    project.metadata[_ARITA_ORIGINAL] = {
        "predecessor": str(predecessor.id),
        "successor": str(remainder.to_node_id),
        "role": predecessor.role.value,
        "manual_true_course_deg": predecessor.manual_true_course_deg,
        "manual_distance_nm": predecessor.manual_distance_nm,
        "phase": remainder.phase.value,
        "altitude": remainder.planned_altitude_ft_msl,
    }
    predecessor.manual_true_course_deg = None
    predecessor.manual_distance_nm = None
    if predecessor.role == RouteNodeRole.VISUAL_REPORTING_POINT:
        predecessor.role = RouteNodeRole.ROUTE_POINT
    arita = RouteNode(
        sequence=len(nodes) - 1,
        name="ARITA",
        name_source=RouteNodeNameSource.GENERATED,
        latitude_deg=ARITA_COORDINATE[0],
        longitude_deg=ARITA_COORDINATE[1],
        role=RouteNodeRole.VISUAL_REPORTING_POINT,
        source=f"{_ARITA_SOURCE}{references.revision}",
    )
    nodes.insert(len(nodes) - 1, arita)
    for i, n in enumerate(nodes):
        n.sequence = i
    project.route_nodes = nodes
    inserted = remainder.model_copy(
        deep=True, update={"id": uuid4(), "to_node_id": arita.id, "phase": FlightPhase.DESCENT}
    )
    remainder.from_node_id = arita.id
    remainder.phase = FlightPhase.VISUAL_ARRIVAL
    remainder.planned_altitude_ft_msl = 1500
    sections.insert(len(sections) - 1, inserted)
    for i, section in enumerate(sections):
        section.sequence = i
    project.sections = sections
    _relink_split_checkpoints(project, remainder, inserted)


def _is_synthetic_umk(node: RouteNode) -> bool:
    return node.source.startswith(_SYNTHETIC_UMK_SOURCE)


def _reconcile_umk(project: Project, references: RjfmInboundReferences) -> None:
    nodes = project.ordered_nodes()
    sections = project.ordered_sections()
    if not _sections_link_nodes(sections, nodes):
        return
    omaru_index = _matching_index(nodes, references.omaru)
    vrep_index = _final_vrep_index(nodes)
    manual_umk_index = next(
        (
            index
            for index, node in enumerate(nodes)
            if omaru_index is not None
            and index > omaru_index
            and not _is_synthetic_umk(node)
            and not node.source.startswith("RJFM_DEPARTURE_UMK:")
            and coordinate_matches_reference(
                node.latitude_deg,
                node.longitude_deg,
                references.umk.latitude_deg,
                references.umk.longitude_deg,
            )
        ),
        None,
    )
    needs_umk = (
        project.destination_airport_id.strip().upper() == "RJFM"
        and omaru_index is not None
        and vrep_index is not None
        and vrep_index > omaru_index
        and not _is_synthetic_umk(nodes[vrep_index])
        and manual_umk_index is None
    )
    synthetic = [node for node in nodes if _is_synthetic_umk(node)]
    if (
        needs_umk
        and len(synthetic) == 1
        and omaru_index is not None
        and nodes[omaru_index + 1].id == synthetic[0].id
    ):
        synthetic[0].latitude_deg = references.umk.latitude_deg
        synthetic[0].longitude_deg = references.umk.longitude_deg
        synthetic[0].source = f"{_SYNTHETIC_UMK_SOURCE}{references.revision}"
        return

    _remove_synthetic_umks(project)
    if not needs_umk:
        return
    nodes = project.ordered_nodes()
    sections = project.ordered_sections()
    omaru_index = _matching_index(nodes, references.omaru)
    assert omaru_index is not None
    predecessor = nodes[omaru_index]
    remainder = sections[omaru_index]
    umk = RouteNode(
        sequence=omaru_index + 1,
        name="UMK",
        name_source=RouteNodeNameSource.GENERATED,
        latitude_deg=references.umk.latitude_deg,
        longitude_deg=references.umk.longitude_deg,
        role=RouteNodeRole.ROUTE_POINT,
        source=f"{_SYNTHETIC_UMK_SOURCE}{references.revision}",
    )
    # A manual direct-leg course/distance cannot describe either new segment.
    # Retain it for removal of the generated point; compute both new geometries.
    project.metadata[_ORIGINAL_GEOMETRY_METADATA_KEY] = {
        "node_id": str(predecessor.id),
        "successor_id": str(remainder.to_node_id),
        "manual_true_course_deg": predecessor.manual_true_course_deg,
        "manual_distance_nm": predecessor.manual_distance_nm,
    }
    predecessor.manual_true_course_deg = None
    predecessor.manual_distance_nm = None
    nodes.insert(omaru_index + 1, umk)
    for index, node in enumerate(nodes):
        node.sequence = index
    project.route_nodes = nodes
    incoming = remainder.model_copy(
        deep=True,
        update={
            "id": uuid4(),
            "to_node_id": umk.id,
        },
    )
    remainder.from_node_id = umk.id
    sections.insert(omaru_index, incoming)
    for index, section in enumerate(sections):
        section.sequence = index
    project.sections = sections
    _relink_split_checkpoints(project, remainder, incoming)


def _remove_synthetic_umks(project: Project) -> None:
    for synthetic in [node for node in project.ordered_nodes() if _is_synthetic_umk(node)]:
        nodes = project.ordered_nodes()
        sections = project.ordered_sections()
        incoming = next((s for s in sections if s.to_node_id == synthetic.id), None)
        outgoing = next((s for s in sections if s.from_node_id == synthetic.id), None)
        if incoming is not None and outgoing is not None:
            outgoing.from_node_id = incoming.from_node_id
            for cp in project.visual_references:
                if cp.linked_section_id == incoming.id:
                    cp.linked_section_id = outgoing.id
        sections = [
            section
            for section in sections
            if section is not incoming
            and section.from_node_id != synthetic.id
            and section.to_node_id != synthetic.id
        ]
        for sequence, section in enumerate(sections):
            section.sequence = sequence
        project.sections = sections
        nodes.remove(synthetic)
        for sequence, node in enumerate(nodes):
            node.sequence = sequence
        project.route_nodes = nodes
    stored = project.metadata.pop(_ORIGINAL_GEOMETRY_METADATA_KEY, None)
    if isinstance(stored, dict):
        predecessor = next(
            (node for node in project.route_nodes if str(node.id) == stored.get("node_id")), None
        )
        successor = next(
            (
                section.to_node_id
                for section in project.sections
                if predecessor is not None and section.from_node_id == predecessor.id
            ),
            None,
        )
        if predecessor is not None and str(successor) == stored.get("successor_id"):
            predecessor.manual_true_course_deg = stored.get("manual_true_course_deg")
            predecessor.manual_distance_nm = stored.get("manual_distance_nm")


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
        node.reference_snapshot is not None
        or node.name_source == RouteNodeNameSource.USER
        or (node.name_source == RouteNodeNameSource.IMPORTED and node.source == "KML/KMZ Point")
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
