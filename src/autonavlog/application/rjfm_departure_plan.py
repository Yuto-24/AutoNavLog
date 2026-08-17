from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest

from autonavlog.domain.enums import FlightPhase, RouteNodeRole
from autonavlog.domain.planning import (
    RJFM_DEPARTURE_RULE_VERSION,
    PersistedUiState,
    RjfmCoordinate,
    RjfmDeparturePlan,
    RjfmDepartureTrigger,
    RjfmMainRouteMode,
    load_persisted_ui_state,
)
from autonavlog.domain.project import NavSection, Project, RouteNode
from autonavlog.nav.geodesy import geodesic_leg

TRIGGER_TOLERANCE_NM = 1.0
TARGET_ALTITUDE_FT_MSL = 5500.0
RJFM_INPUT_MODE_EDITABLE = "EDITABLE"
RJFM_INPUT_MODE_DEPARTURE_TO_UMK_FIXED = "RJFM_DEPARTURE_TO_UMK_FIXED"
RJFM_INPUT_MODE_UMK_TO_OMARU_FIXED = "RJFM_UMK_TO_OMARU_FIXED"
RJFM_INPUT_MODE_PARENT_CONTAINS_UMK_FIXED = "RJFM_PARENT_CONTAINS_UMK_FIXED"
_ORIGINAL_SECTIONS_METADATA_KEY = "rjfm_departure_exception_original_sections"
_ORIGINAL_NODE_OVERRIDE_METADATA_KEY = "rjfm_departure_exception_original_node_override"


@dataclass(frozen=True)
class RjfmPlanReferences:
    revision: str
    content_fingerprint: str
    umk: RjfmCoordinate
    over_field: RjfmCoordinate
    omaru: RjfmCoordinate


def apply_rjfm_departure_exception(
    project: Project,
    references: RjfmPlanReferences,
) -> RjfmDeparturePlan | None:
    """Idempotently materialize the RJFM northbound main-route exception.

    The generated maneuver path remains separate.  This function only fixes the
    physical/virtual NAV LOG structure and records the coordinate assumptions
    needed by the calculation and guidance services.
    """

    if project.departure_airport_id.strip().upper() != "RJFM":
        _deactivate_exception(project)
        return None
    nodes = project.ordered_nodes()
    sections = project.ordered_sections()
    if len(nodes) < 2 or len(sections) != len(nodes) - 1:
        _deactivate_exception(project)
        return None
    # Rebuild each application from the saved pre-exception values, then start
    # a fresh snapshot for the newly controlled span.  A section that leaves
    # the span must neither retain 5500 ft nor remain pinned to an old value.
    _restore_original_sections(project)
    sections = project.ordered_sections()

    first = nodes[1]
    umk_gap = _distance_to(first, references.umk)
    omaru_gap = _distance_to(first, references.omaru)
    if min(umk_gap, omaru_gap) > TRIGGER_TOLERANCE_NM + 1e-9:
        _deactivate_exception(project)
        return None

    trigger = (
        RjfmDepartureTrigger.UMK
        if umk_gap <= omaru_gap
        else RjfmDepartureTrigger.OMARU
    )
    umk = _kml_coordinate(nodes, references.umk, "KML:UMK")
    omaru = _kml_coordinate(nodes, references.omaru, "KML:OMARU")

    if trigger == RjfmDepartureTrigger.UMK:
        user_omaru_index = _matching_user_node_index(nodes, references.omaru, start=2)
        _capture_original_sections(
            project,
            sections[: user_omaru_index if user_omaru_index is not None else 1],
        )
        # The trigger node is the authoritative KML UMK even when its name is
        # absent or misspelled.
        umk = RjfmCoordinate(
            latitude_deg=first.latitude_deg,
            longitude_deg=first.longitude_deg,
            source="KML:UMK",
            estimated_error_nm=0.0,
        )
        omaru_index = _ensure_omaru_after_umk(project, omaru, references.revision)
        nodes = project.ordered_nodes()
        sections = project.ordered_sections()
        existing = nodes[omaru_index]
        if not _is_synthetic_omaru(existing):
            omaru = RjfmCoordinate(
                latitude_deg=existing.latitude_deg,
                longitude_deg=existing.longitude_deg,
                source="KML:OMARU",
                estimated_error_nm=0.0,
            )
        _set_umk_route_phases(sections, omaru_index)
        mode = RjfmMainRouteMode.UMK_PHYSICAL
    else:
        _capture_original_sections(project, sections[:1])
        _remove_synthetic_omarus(project)
        _restore_original_node_override(project)
        nodes = project.ordered_nodes()
        sections = project.ordered_sections()
        first = nodes[1]
        omaru = RjfmCoordinate(
            latitude_deg=first.latitude_deg,
            longitude_deg=first.longitude_deg,
            source="KML:OMARU",
            estimated_error_nm=0.0,
        )
        sections[0].phase = FlightPhase.CLIMB
        sections[0].planned_altitude_ft_msl = TARGET_ALTITUDE_FT_MSL
        mode = RjfmMainRouteMode.OMARU_VIRTUAL_UMK

    virtual_rca_distance_nm = _adopted_rca_distance_nm(project, trigger, umk)
    plan = RjfmDeparturePlan(
        trigger=trigger,
        main_route_mode=mode,
        umk=umk,
        over_field=references.over_field,
        omaru=omaru,
        virtual_rca_distance_nm=virtual_rca_distance_nm,
        route_application_key=_application_key(
            project,
            references.revision,
            references.content_fingerprint,
            trigger,
            umk,
            references.over_field,
            omaru,
            virtual_rca_distance_nm,
        ),
        reference_revision=references.revision,
        reference_content_fingerprint=references.content_fingerprint,
    )
    _store_plan(project, plan)
    return plan


def _distance_to(node: RouteNode, coordinate: RjfmCoordinate) -> float:
    return geodesic_leg(
        node.latitude_deg,
        node.longitude_deg,
        coordinate.latitude_deg,
        coordinate.longitude_deg,
    ).distance_nm


def _matching_node_index(
    nodes: list[RouteNode],
    coordinate: RjfmCoordinate,
    *,
    start: int = 0,
) -> int | None:
    return next(
        (
            index
            for index, node in enumerate(nodes[start:], start=start)
            if _distance_to(node, coordinate) <= TRIGGER_TOLERANCE_NM + 1e-9
        ),
        None,
    )


def _matching_user_node_index(
    nodes: list[RouteNode],
    coordinate: RjfmCoordinate,
    *,
    start: int = 0,
) -> int | None:
    return next(
        (
            index
            for index, node in enumerate(nodes[start:], start=start)
            if not _is_synthetic_omaru(node)
            and _distance_to(node, coordinate) <= TRIGGER_TOLERANCE_NM + 1e-9
        ),
        None,
    )


def _kml_coordinate(
    nodes: list[RouteNode],
    fallback: RjfmCoordinate,
    source: str,
) -> RjfmCoordinate:
    index = _matching_node_index(nodes, fallback, start=1)
    if index is None:
        return fallback
    node = nodes[index]
    if _is_synthetic_omaru(node):
        return fallback
    return RjfmCoordinate(
        latitude_deg=node.latitude_deg,
        longitude_deg=node.longitude_deg,
        source=source,
        estimated_error_nm=0.0,
    )


def _ensure_omaru_after_umk(
    project: Project,
    omaru: RjfmCoordinate,
    revision: str,
) -> int:
    nodes = project.ordered_nodes()
    user_index = _matching_user_node_index(nodes, omaru, start=2)
    if user_index is not None:
        _remove_synthetic_omarus(project)
        _restore_original_node_override(project)
        matched = _matching_user_node_index(project.ordered_nodes(), omaru, start=2)
        if matched is None:  # pragma: no cover - defensive graph invariant
            raise ValueError("matching user OMARU disappeared during reconciliation")
        return matched

    synthetic = [
        (index, node)
        for index, node in enumerate(nodes)
        if _is_synthetic_omaru(node)
    ]
    if len(synthetic) == 1 and synthetic[0][0] == 2:
        node = synthetic[0][1]
        _capture_original_node_override(project, nodes[1], legacy_synthetic=node)
        node.name = "OMARU"
        node.latitude_deg = omaru.latitude_deg
        node.longitude_deg = omaru.longitude_deg
        node.role = RouteNodeRole.ROUTE_POINT
        node.source = f"RJFM_EXCEPTION:{revision}"
        _apply_split_manual_override(project, node)
        return 2

    _remove_synthetic_omarus(project)
    _insert_omaru_after_umk(project, omaru, revision)
    return 2


def _insert_omaru_after_umk(
    project: Project,
    omaru: RjfmCoordinate,
    revision: str,
) -> None:
    nodes = project.ordered_nodes()
    sections = project.ordered_sections()
    if len(nodes) < 2 or not sections:
        return
    _capture_original_node_override(project, nodes[1])
    new_node = RouteNode(
        project_id=project.id,
        sequence=2,
        name="OMARU",
        latitude_deg=omaru.latitude_deg,
        longitude_deg=omaru.longitude_deg,
        role=RouteNodeRole.ROUTE_POINT,
        source=f"RJFM_EXCEPTION:{revision}",
    )
    nodes[1].manual_true_course_deg = None
    nodes[1].manual_distance_nm = None
    for index, node in enumerate(nodes[2:], start=3):
        node.sequence = index
    project.route_nodes = [nodes[0], nodes[1], new_node, *nodes[2:]]

    original_remainder = sections[1] if len(sections) > 1 else None
    copied = original_remainder or sections[0]
    inserted = NavSection(
        project_id=project.id,
        sequence=1,
        from_node_id=nodes[1].id,
        to_node_id=new_node.id,
        phase=FlightPhase.CRUISE,
        planned_altitude_ft_msl=TARGET_ALTITUDE_FT_MSL,
        safe_enroute_altitude_ft_msl=copied.safe_enroute_altitude_ft_msl,
        manual_wind_direction_deg=copied.manual_wind_direction_deg,
        manual_wind_speed_kt=copied.manual_wind_speed_kt,
        manual_wind_by_phase=dict(copied.manual_wind_by_phase),
        manual_temperature_c=copied.manual_temperature_c,
        manual_temperature_c_by_phase=dict(copied.manual_temperature_c_by_phase),
        manual_tas_kt=copied.manual_tas_kt,
        loss_time_seconds=0.0,
        notes=copied.notes,
    )
    rebuilt: list[NavSection] = [sections[0], inserted]
    if original_remainder is not None:
        original_remainder.from_node_id = new_node.id
        original_remainder.to_node_id = nodes[2].id
        rebuilt.append(original_remainder)
        rebuilt.extend(sections[2:])
    for index, section in enumerate(rebuilt):
        section.sequence = index
    project.sections = rebuilt
    _apply_split_manual_override(project, new_node)


def _remove_synthetic_omarus(project: Project) -> None:
    """Remove generated OMARU nodes while preserving the original remainder."""

    while True:
        nodes = project.ordered_nodes()
        synthetic_index = next(
            (index for index, node in enumerate(nodes) if _is_synthetic_omaru(node)),
            None,
        )
        if synthetic_index is None:
            return
        synthetic = nodes[synthetic_index]
        predecessor = nodes[synthetic_index - 1] if synthetic_index > 0 else None
        successor = (
            nodes[synthetic_index + 1]
            if synthetic_index + 1 < len(nodes)
            else None
        )
        sections = project.ordered_sections()
        incoming = next(
            (section for section in sections if section.to_node_id == synthetic.id),
            None,
        )
        outgoing = next(
            (section for section in sections if section.from_node_id == synthetic.id),
            None,
        )

        if outgoing is not None and predecessor is not None and successor is not None:
            outgoing.from_node_id = predecessor.id
            outgoing.to_node_id = successor.id

        rebuilt_sections = [
            section
            for section in sections
            if incoming is None or section.id != incoming.id
        ]
        if outgoing is None:
            rebuilt_sections = [
                section
                for section in rebuilt_sections
                if section.from_node_id != synthetic.id
                and section.to_node_id != synthetic.id
            ]
        for index, section in enumerate(rebuilt_sections):
            section.sequence = index
        # Retarget/remove sections before deleting the node so assignment-time
        # graph validation never observes a dangling section endpoint.
        project.sections = rebuilt_sections

        rebuilt_nodes = [node for node in nodes if node.id != synthetic.id]
        for index, node in enumerate(rebuilt_nodes):
            node.sequence = index
        project.route_nodes = rebuilt_nodes


def _is_synthetic_omaru(node: RouteNode) -> bool:
    return node.source.startswith("RJFM_EXCEPTION:")


def _capture_original_node_override(
    project: Project,
    predecessor: RouteNode,
    *,
    legacy_synthetic: RouteNode | None = None,
) -> None:
    if _ORIGINAL_NODE_OVERRIDE_METADATA_KEY in project.metadata:
        return
    source = predecessor
    if (
        legacy_synthetic is not None
        and predecessor.manual_true_course_deg is None
        and predecessor.manual_distance_nm is None
        and (
            legacy_synthetic.manual_true_course_deg is not None
            or legacy_synthetic.manual_distance_nm is not None
        )
    ):
        source = legacy_synthetic
    project.metadata[_ORIGINAL_NODE_OVERRIDE_METADATA_KEY] = {
        "node_id": str(predecessor.id),
        "manual_true_course_deg": source.manual_true_course_deg,
        "manual_distance_nm": source.manual_distance_nm,
    }


def _apply_split_manual_override(project: Project, synthetic: RouteNode) -> None:
    raw = project.metadata.get(_ORIGINAL_NODE_OVERRIDE_METADATA_KEY)
    if not isinstance(raw, dict):
        return
    node_id = raw.get("node_id")
    predecessor = next(
        (node for node in project.ordered_nodes() if str(node.id) == node_id),
        None,
    )
    nodes = project.ordered_nodes()
    try:
        synthetic_index = next(
            index for index, node in enumerate(nodes) if node.id == synthetic.id
        )
    except StopIteration:
        return
    if predecessor is None or synthetic_index + 1 >= len(nodes):
        return
    successor = nodes[synthetic_index + 1]
    predecessor.manual_true_course_deg = None
    synthetic.manual_true_course_deg = None
    manual_distance = raw.get("manual_distance_nm")
    if manual_distance is None:
        predecessor.manual_distance_nm = None
        synthetic.manual_distance_nm = None
        return
    try:
        original_distance_nm = float(manual_distance)
    except (TypeError, ValueError):
        predecessor.manual_distance_nm = None
        synthetic.manual_distance_nm = None
        return
    first_geodesic_nm = geodesic_leg(
        predecessor.latitude_deg,
        predecessor.longitude_deg,
        synthetic.latitude_deg,
        synthetic.longitude_deg,
    ).distance_nm
    remainder_geodesic_nm = geodesic_leg(
        synthetic.latitude_deg,
        synthetic.longitude_deg,
        successor.latitude_deg,
        successor.longitude_deg,
    ).distance_nm
    total_geodesic_nm = first_geodesic_nm + remainder_geodesic_nm
    if total_geodesic_nm <= 1e-9:
        predecessor.manual_distance_nm = None
        synthetic.manual_distance_nm = None
        return
    if first_geodesic_nm <= 1e-9:
        predecessor.manual_distance_nm = None
        synthetic.manual_distance_nm = original_distance_nm
        return
    if remainder_geodesic_nm <= 1e-9:
        predecessor.manual_distance_nm = original_distance_nm
        synthetic.manual_distance_nm = None
        return
    predecessor.manual_distance_nm = (
        original_distance_nm * first_geodesic_nm / total_geodesic_nm
    )
    synthetic.manual_distance_nm = (
        original_distance_nm * remainder_geodesic_nm / total_geodesic_nm
    )


def _restore_original_node_override(project: Project) -> None:
    raw = project.metadata.pop(_ORIGINAL_NODE_OVERRIDE_METADATA_KEY, None)
    if not isinstance(raw, dict):
        return
    node_id = raw.get("node_id")
    node = next(
        (item for item in project.ordered_nodes() if str(item.id) == node_id),
        None,
    )
    if node is None:
        return
    course = raw.get("manual_true_course_deg")
    distance = raw.get("manual_distance_nm")
    try:
        node.manual_true_course_deg = None if course is None else float(course)
        node.manual_distance_nm = None if distance is None else float(distance)
    except (TypeError, ValueError):
        node.manual_true_course_deg = None
        node.manual_distance_nm = None


def _adopted_rca_distance_nm(
    project: Project,
    trigger: RjfmDepartureTrigger,
    umk: RjfmCoordinate,
) -> float:
    nodes = project.ordered_nodes()
    origin = nodes[0]
    first = nodes[1]
    first_geodesic_nm = geodesic_leg(
        origin.latitude_deg,
        origin.longitude_deg,
        first.latitude_deg,
        first.longitude_deg,
    ).distance_nm
    adopted_first_leg_nm = (
        origin.manual_distance_nm
        if origin.manual_distance_nm is not None
        else first_geodesic_nm
    )
    if trigger == RjfmDepartureTrigger.UMK:
        return adopted_first_leg_nm
    umk_geodesic_nm = geodesic_leg(
        origin.latitude_deg,
        origin.longitude_deg,
        umk.latitude_deg,
        umk.longitude_deg,
    ).distance_nm
    return adopted_first_leg_nm * umk_geodesic_nm / first_geodesic_nm


def _set_umk_route_phases(sections: list[NavSection], omaru_node_index: int) -> None:
    sections[0].phase = FlightPhase.CLIMB
    sections[0].planned_altitude_ft_msl = TARGET_ALTITUDE_FT_MSL
    for section in sections[1:omaru_node_index]:
        section.phase = FlightPhase.CRUISE
        section.planned_altitude_ft_msl = TARGET_ALTITUDE_FT_MSL


def _application_key(
    project: Project,
    revision: str,
    content_fingerprint: str,
    trigger: RjfmDepartureTrigger,
    umk: RjfmCoordinate,
    over_field: RjfmCoordinate,
    omaru: RjfmCoordinate,
    virtual_rca_distance_nm: float,
) -> str:
    plan_coordinates = (umk, over_field, omaru)
    payload = "|".join(
        [
            project.departure_airport_id.strip().upper(),
            revision,
            content_fingerprint,
            trigger.value,
            f"RCA:{virtual_rca_distance_nm!r}",
            *(
                (
                    f"POINT:{point.latitude_deg!r}:{point.longitude_deg!r}:"
                    f"{point.source}:{point.estimated_error_nm!r}"
                )
                for point in plan_coordinates
            ),
            *(
                (
                    f"{node.id}:{node.latitude_deg:.8f}:{node.longitude_deg:.8f}:"
                    f"{node.role.value}:{node.source}:"
                    f"{node.manual_true_course_deg!r}:{node.manual_distance_nm!r}"
                )
                for node in project.ordered_nodes()
            ),
            *(
                (
                    f"SECTION:{section.id}:{section.from_node_id}:{section.to_node_id}:"
                    f"{section.phase.value}:{section.planned_altitude_ft_msl!r}"
                )
                for section in project.ordered_sections()
            ),
        ]
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def rjfm_plan_matches_project(project: Project, plan: RjfmDeparturePlan) -> bool:
    """Return whether a persisted exception plan still matches its route inputs."""

    if project.departure_airport_id.strip().upper() != "RJFM":
        return False
    nodes = project.ordered_nodes()
    sections = project.ordered_sections()
    if len(nodes) < 2 or len(sections) != len(nodes) - 1:
        return False
    expected_mode = {
        RjfmDepartureTrigger.UMK: RjfmMainRouteMode.UMK_PHYSICAL,
        RjfmDepartureTrigger.OMARU: RjfmMainRouteMode.OMARU_VIRTUAL_UMK,
    }[plan.trigger]
    if plan.main_route_mode != expected_mode:
        return False
    trigger_coordinate = (
        plan.umk if plan.trigger == RjfmDepartureTrigger.UMK else plan.omaru
    )
    if _distance_to(nodes[1], trigger_coordinate) > 1e-6:
        return False
    expected_rca_distance_nm = _adopted_rca_distance_nm(
        project,
        plan.trigger,
        plan.umk,
    )
    if abs(expected_rca_distance_nm - plan.virtual_rca_distance_nm) > 1e-9:
        return False
    expected_key = _application_key(
        project,
        plan.reference_revision,
        plan.reference_content_fingerprint,
        plan.trigger,
        plan.umk,
        plan.over_field,
        plan.omaru,
        plan.virtual_rca_distance_nm,
    )
    return compare_digest(expected_key, plan.route_application_key)


def rjfm_section_input_modes(
    project: Project,
    plan: RjfmDeparturePlan,
) -> dict[str, str]:
    """Return the UI input policy for sections controlled by the RJFM exception.

    The route graph deliberately retains physical UMK and OMARU nodes when they
    are present.  This mapping exposes only the sections whose altitude and
    phase are owned by the exception, so callers do not need to infer control
    from point names or mutable display labels.
    """

    if not rjfm_plan_matches_project(project, plan):
        return {}
    sections = project.ordered_sections()
    if not sections:
        return {}
    if plan.trigger == RjfmDepartureTrigger.OMARU:
        return {
            str(sections[0].id): RJFM_INPUT_MODE_PARENT_CONTAINS_UMK_FIXED,
        }

    nodes = project.ordered_nodes()
    omaru_index = next(
        (
            index
            for index, node in enumerate(nodes[2:], start=2)
            if _distance_to(node, plan.omaru) <= 1e-6
        ),
        None,
    )
    if omaru_index is None:
        return {}
    modes = {
        str(sections[0].id): RJFM_INPUT_MODE_DEPARTURE_TO_UMK_FIXED,
    }
    modes.update(
        {
            str(section.id): RJFM_INPUT_MODE_UMK_TO_OMARU_FIXED
            for section in sections[1:omaru_index]
        }
    )
    return modes


def _deactivate_exception(project: Project) -> None:
    _remove_synthetic_omarus(project)
    _restore_original_node_override(project)
    _restore_original_sections(project)
    _clear_plan(project)


def _capture_original_sections(
    project: Project,
    sections: list[NavSection],
) -> None:
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
        phase = stored.get("phase")
        altitude = stored.get("planned_altitude_ft_msl")
        if phase is None or altitude is None:
            continue
        try:
            restored_phase = FlightPhase(phase)
            restored_altitude = float(altitude)
        except (TypeError, ValueError):
            continue
        section.phase = restored_phase
        section.planned_altitude_ft_msl = restored_altitude


def _ui_state(project: Project) -> PersistedUiState:
    raw = project.metadata.get("ui_state")
    return PersistedUiState() if raw is None else load_persisted_ui_state(raw)


def _store_plan(project: Project, plan: RjfmDeparturePlan) -> None:
    current = _ui_state(project)
    guidance = current.rjfm_departure_guidance
    guidance_matches_plan = bool(
        guidance is not None
        and guidance.rule_version == RJFM_DEPARTURE_RULE_VERSION
        and guidance.reference_revision == plan.reference_revision
        and guidance.reference_content_fingerprint
        == plan.reference_content_fingerprint
        and guidance.center_route == [plan.umk, plan.over_field, plan.omaru]
    )
    state = current.model_copy(
        update={
            "rjfm_departure_plan": plan,
            # Preserve a saved diagnostic result only when both the normalized
            # plan and the guidance's current rule/reference/route identity match.
            # Any old pack or material route change must be recalculated.
            "rjfm_departure_guidance": (
                guidance
                if current.rjfm_departure_plan == plan and guidance_matches_plan
                else None
            ),
        }
    )
    project.metadata["ui_state"] = state.model_dump(mode="json")


def _clear_plan(project: Project) -> None:
    raw = project.metadata.get("ui_state")
    if raw is None:
        return
    state = load_persisted_ui_state(raw)
    if state.rjfm_departure_plan is None and state.rjfm_departure_guidance is None:
        return
    project.metadata["ui_state"] = state.model_copy(
        update={
            "rjfm_departure_plan": None,
            "rjfm_departure_guidance": None,
        }
    ).model_dump(mode="json")
