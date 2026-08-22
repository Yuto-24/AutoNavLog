from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from autonavlog.application.rjfm_departure_plan import (
    RjfmPlanReferences,
    apply_rjfm_departure_exception,
)
from autonavlog.domain.enums import FlightPhase, RouteNodeRole
from autonavlog.domain.planning import (
    RjfmCoordinate,
    RjfmDepartureGuidance,
    RjfmDepartureTrigger,
    RjfmGuidanceStatus,
    RjfmMainRouteMode,
    RjfmRunwayGuidance,
    RjfmTurnDirection,
    load_persisted_ui_state,
)
from autonavlog.domain.project import ManualWind, NavSection, Project, RouteNode

JST = ZoneInfo("Asia/Tokyo")


def _references() -> RjfmPlanReferences:
    return RjfmPlanReferences(
        revision="fixture-r1",
        content_fingerprint="b" * 64,
        umk=RjfmCoordinate(
            latitude_deg=32.08,
            longitude_deg=131.50,
            source="fixture-map",
            estimated_error_nm=0.2,
        ),
        over_field=RjfmCoordinate(
            latitude_deg=32.08,
            longitude_deg=131.45,
            source="fixture-map",
            estimated_error_nm=0.2,
        ),
        omaru=RjfmCoordinate(
            latitude_deg=32.22,
            longitude_deg=131.55,
            source="fixture-map",
            estimated_error_nm=0.2,
        ),
    )


def _project(points: list[tuple[str, float, float]]) -> Project:
    nodes = [
        RouteNode(
            sequence=index,
            name=name,
            latitude_deg=latitude,
            longitude_deg=longitude,
            role=(
                RouteNodeRole.AIRPORT
                if index == 0
                else RouteNodeRole.DESTINATION
                if index == len(points) - 1
                else RouteNodeRole.ROUTE_POINT
            ),
            source="KML",
        )
        for index, (name, latitude, longitude) in enumerate(points)
    ]
    sections = [
        NavSection(
            sequence=index,
            from_node_id=start.id,
            to_node_id=end.id,
            phase=FlightPhase.CLIMB if index == 0 else FlightPhase.CRUISE,
            planned_altitude_ft_msl=5000,
            manual_wind_by_phase={
                FlightPhase.CRUISE: ManualWind(direction_deg_from=270, speed_kt=15)
            },
            manual_temperature_c=18,
            manual_tas_kt=145,
        )
        for index, (start, end) in enumerate(zip(nodes, nodes[1:], strict=False))
    ]
    return Project(
        name="RJFM fixture",
        flight_date=date(2026, 8, 17),
        planned_departure_time_jst=datetime(2026, 8, 17, 9, tzinfo=JST),
        departure_airport_id="RJFM",
        destination_airport_id="RJFO",
        total_usable_fuel_gal=81,
        default_variation_deg_east=-8,
        route_nodes=nodes,
        sections=sections,
    )


def test_umk_trigger_inserts_omaru_once_and_preserves_remainder() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("unnamed first point", 32.08, 131.50),
            ("NEXT", 32.50, 131.65),
            ("RJFO", 33.479, 131.737),
        ]
    )

    plan = apply_rjfm_departure_exception(project, _references())

    assert plan is not None
    assert plan.trigger == RjfmDepartureTrigger.UMK
    assert plan.main_route_mode == RjfmMainRouteMode.UMK_PHYSICAL
    assert [node.name for node in project.ordered_nodes()] == [
        "RJFM",
        "unnamed first point",
        "OMARU",
        "NEXT",
        "RJFO",
    ]
    sections = project.ordered_sections()
    assert [section.phase for section in sections[:3]] == [
        FlightPhase.CLIMB,
        FlightPhase.CRUISE,
        FlightPhase.CRUISE,
    ]
    assert [section.planned_altitude_ft_msl for section in sections[:2]] == [5500, 5500]
    assert sections[1].manual_tas_kt == 145

    first_ids = [node.id for node in project.ordered_nodes()]
    apply_rjfm_departure_exception(project, _references())
    assert [node.id for node in project.ordered_nodes()] == first_ids
    assert len(project.sections) == 4


def test_reference_update_reuses_generated_omaru_and_refreshes_provenance() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("NEXT", 32.50, 131.65),
            ("RJFO", 33.479, 131.737),
        ]
    )
    first_plan = apply_rjfm_departure_exception(project, _references())
    assert first_plan is not None
    generated = next(
        node for node in project.ordered_nodes() if node.source.startswith("RJFM_EXCEPTION:")
    )

    updated = RjfmPlanReferences(
        revision="fixture-r2",
        content_fingerprint="c" * 64,
        umk=_references().umk,
        over_field=_references().over_field,
        omaru=RjfmCoordinate(
            latitude_deg=32.35,
            longitude_deg=131.62,
            source="fixture-map-r2",
            estimated_error_nm=0.2,
        ),
    )
    second_plan = apply_rjfm_departure_exception(project, updated)

    assert second_plan is not None
    synthetic = [
        node for node in project.ordered_nodes() if node.source.startswith("RJFM_EXCEPTION:")
    ]
    assert len(synthetic) == 1
    assert synthetic[0].id == generated.id
    assert (synthetic[0].latitude_deg, synthetic[0].longitude_deg) == (32.35, 131.62)
    assert synthetic[0].source == "RJFM_EXCEPTION:fixture-r2"
    assert second_plan.reference_revision == "fixture-r2"
    assert second_plan.reference_content_fingerprint == "c" * 64


def test_deactivation_removes_generated_node_and_restores_original_inputs() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("NEXT", 32.50, 131.65),
            ("RJFO", 33.479, 131.737),
        ]
    )
    original_node_ids = [node.id for node in project.ordered_nodes()]
    original_section_ids = [section.id for section in project.ordered_sections()]
    original_phases = [section.phase for section in project.ordered_sections()]
    original_altitudes = [
        section.planned_altitude_ft_msl for section in project.ordered_sections()
    ]
    umk = project.ordered_nodes()[1]
    umk.manual_true_course_deg = 24.0
    umk.manual_distance_nm = 18.0

    assert apply_rjfm_departure_exception(project, _references()) is not None
    generated = project.ordered_nodes()[2]
    assert umk.manual_true_course_deg is None
    assert generated.manual_true_course_deg is None
    assert umk.manual_distance_nm is not None
    assert generated.manual_distance_nm is not None
    assert umk.manual_distance_nm + generated.manual_distance_nm == pytest.approx(18.0)
    project.ordered_sections()[-1].planned_altitude_ft_msl = 7000.0
    assert apply_rjfm_departure_exception(project, _references()) is not None

    project.departure_airport_id = "OTHER"
    assert apply_rjfm_departure_exception(project, _references()) is None

    assert [node.id for node in project.ordered_nodes()] == original_node_ids
    assert [section.id for section in project.ordered_sections()] == original_section_ids
    assert [section.phase for section in project.ordered_sections()] == original_phases
    assert [
        section.planned_altitude_ft_msl for section in project.ordered_sections()
    ] == [*original_altitudes[:-1], 7000.0]
    assert project.ordered_nodes()[1].manual_true_course_deg == 24.0
    assert project.ordered_nodes()[1].manual_distance_nm == 18.0
    assert all(
        section.from_node_id == start.id and section.to_node_id == end.id
        for section, (start, end) in zip(
            project.ordered_sections(),
            zip(project.ordered_nodes(), project.ordered_nodes()[1:], strict=False),
            strict=False,
        )
    )


def test_physical_umk_rca_uses_adopted_manual_first_leg_distance() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("RJFO", 33.479, 131.737),
        ]
    )
    project.ordered_nodes()[0].manual_distance_nm = 12.5

    plan = apply_rjfm_departure_exception(project, _references())

    assert plan is not None
    assert plan.virtual_rca_distance_nm == 12.5


def test_generated_to_user_omaru_transition_restores_original_manual_leg() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("NEXT", 32.50, 131.65),
            ("RJFO", 33.479, 131.737),
        ]
    )
    umk = project.ordered_nodes()[1]
    umk.manual_true_course_deg = 24.0
    umk.manual_distance_nm = 18.0
    assert apply_rjfm_departure_exception(project, _references()) is not None
    assert any(
        node.source.startswith("RJFM_EXCEPTION:") for node in project.ordered_nodes()
    )

    user_omaru = project.ordered_nodes()[3]
    user_omaru.latitude_deg = _references().omaru.latitude_deg
    user_omaru.longitude_deg = _references().omaru.longitude_deg
    reapplied = apply_rjfm_departure_exception(project, _references())

    assert reapplied is not None
    assert all(
        not node.source.startswith("RJFM_EXCEPTION:")
        for node in project.ordered_nodes()
    )
    assert project.ordered_nodes()[1].manual_true_course_deg == 24.0
    assert project.ordered_nodes()[1].manual_distance_nm == 18.0


def test_reference_update_restores_sections_that_leave_controlled_span() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("KEEP", 32.15, 131.52),
            ("old OMARU", 32.22, 131.55),
            ("RJFO", 33.479, 131.737),
        ]
    )
    original_sections = project.ordered_sections()
    for section, altitude in zip(
        original_sections,
        (4000.0, 5000.0, 6000.0, 7000.0),
        strict=True,
    ):
        section.planned_altitude_ft_msl = altitude
    original_ids = [section.id for section in original_sections]

    assert apply_rjfm_departure_exception(project, _references()) is not None
    assert [
        section.planned_altitude_ft_msl for section in project.ordered_sections()
    ] == [5500.0, 5500.0, 5500.0, 7000.0]

    updated = RjfmPlanReferences(
        revision="fixture-r2",
        content_fingerprint="c" * 64,
        umk=_references().umk,
        over_field=_references().over_field,
        omaru=RjfmCoordinate(
            latitude_deg=32.35,
            longitude_deg=131.62,
            source="fixture-map-r2",
            estimated_error_nm=0.2,
        ),
    )
    assert apply_rjfm_departure_exception(project, updated) is not None

    by_id = {section.id: section for section in project.ordered_sections()}
    assert [by_id[section_id].planned_altitude_ft_msl for section_id in original_ids] == [
        5500.0,
        5000.0,
        6000.0,
        7000.0,
    ]

    by_id[original_ids[1]].planned_altitude_ft_msl = 8888.0
    assert apply_rjfm_departure_exception(project, updated) is not None
    by_id = {section.id: section for section in project.ordered_sections()}
    assert by_id[original_ids[1]].planned_altitude_ft_msl == 8888.0

    assert apply_rjfm_departure_exception(project, _references()) is not None
    by_id = {section.id: section for section in project.ordered_sections()}
    assert by_id[original_ids[1]].planned_altitude_ft_msl == 5500.0
    project.departure_airport_id = "OTHER"
    assert apply_rjfm_departure_exception(project, _references()) is None
    by_id = {section.id: section for section in project.ordered_sections()}
    assert by_id[original_ids[1]].planned_altitude_ft_msl == 8888.0


def test_idempotent_normalization_preserves_matching_saved_guidance_payload() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("RJFO", 33.479, 131.737),
        ]
    )
    first_plan = apply_rjfm_departure_exception(project, _references())
    assert first_plan is not None
    state = load_persisted_ui_state(project.metadata["ui_state"])
    saved_guidance = RjfmDepartureGuidance(
        reference_revision="fixture-r1",
        reference_content_fingerprint="b" * 64,
        generated_against_fingerprint="a" * 64,
        candidates=[
            RjfmRunwayGuidance(
                runway="09",
                status=RjfmGuidanceStatus.UNAVAILABLE,
                turn_direction=RjfmTurnDirection.LEFT,
            ),
            RjfmRunwayGuidance(
                runway="27",
                status=RjfmGuidanceStatus.UNAVAILABLE,
                turn_direction=RjfmTurnDirection.RIGHT,
            ),
        ],
        center_route=[first_plan.umk, first_plan.over_field, first_plan.omaru],
    )
    project.metadata["ui_state"] = state.model_copy(
        update={"rjfm_departure_guidance": saved_guidance}
    ).model_dump(mode="json")
    apply_rjfm_departure_exception(project, _references())

    reapplied = load_persisted_ui_state(project.metadata["ui_state"])
    assert reapplied.rjfm_departure_plan == first_plan
    assert reapplied.rjfm_departure_guidance == saved_guidance


@pytest.mark.parametrize(
    "mismatch",
    ["rule_version", "reference_revision", "reference_fingerprint", "center_route"],
)
def test_normalization_discards_guidance_that_does_not_match_current_plan(
    mismatch: str,
) -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("RJFO", 33.479, 131.737),
        ]
    )
    plan = apply_rjfm_departure_exception(project, _references())
    assert plan is not None
    guidance = RjfmDepartureGuidance(
        reference_revision=plan.reference_revision,
        reference_content_fingerprint=plan.reference_content_fingerprint,
        generated_against_fingerprint="a" * 64,
        candidates=[
            RjfmRunwayGuidance(
                runway="09",
                status=RjfmGuidanceStatus.UNAVAILABLE,
                turn_direction=RjfmTurnDirection.LEFT,
            ),
            RjfmRunwayGuidance(
                runway="27",
                status=RjfmGuidanceStatus.UNAVAILABLE,
                turn_direction=RjfmTurnDirection.RIGHT,
            ),
        ],
        center_route=[plan.umk, plan.over_field, plan.omaru],
    )
    mismatch_update: dict[str, object]
    if mismatch == "rule_version":
        mismatch_update = {"rule_version": "RJFM_NORTHBOUND_R6_5_1_V1"}
    elif mismatch == "reference_revision":
        mismatch_update = {"reference_revision": "fixture-old"}
    elif mismatch == "reference_fingerprint":
        mismatch_update = {"reference_content_fingerprint": "c" * 64}
    else:
        mismatch_update = {"center_route": [plan.umk, plan.over_field, plan.umk]}
    state = load_persisted_ui_state(project.metadata["ui_state"])
    project.metadata["ui_state"] = state.model_copy(
        update={"rjfm_departure_guidance": guidance.model_copy(update=mismatch_update)}
    ).model_dump(mode="json")

    apply_rjfm_departure_exception(project, _references())

    reapplied = load_persisted_ui_state(project.metadata["ui_state"])
    assert reapplied.rjfm_departure_plan == plan
    assert reapplied.rjfm_departure_guidance is None


def test_existing_later_omaru_is_not_moved_or_duplicated() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("KEEP", 32.15, 131.52),
            ("OMARU-ish", 32.22, 131.55),
            ("RJFO", 33.479, 131.737),
        ]
    )

    apply_rjfm_departure_exception(project, _references())

    assert [node.name for node in project.ordered_nodes()] == [
        "RJFM",
        "UMK-ish",
        "KEEP",
        "OMARU-ish",
        "RJFO",
    ]
    assert all(
        section.planned_altitude_ft_msl == 5500
        for section in project.ordered_sections()[:3]
    )


def test_omaru_first_uses_virtual_umk_without_inserting_node() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("first", 32.22, 131.55),
            ("RJFO", 33.479, 131.737),
        ]
    )

    plan = apply_rjfm_departure_exception(project, _references())

    assert plan is not None
    assert plan.trigger == RjfmDepartureTrigger.OMARU
    assert plan.main_route_mode == RjfmMainRouteMode.OMARU_VIRTUAL_UMK
    assert len(project.route_nodes) == 3
    assert project.ordered_sections()[0].planned_altitude_ft_msl == 5500
    state = load_persisted_ui_state(project.metadata["ui_state"])
    assert state.rjfm_departure_plan == plan


def test_non_matching_first_point_does_not_apply() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("OTHER", 32.50, 132.00),
            ("RJFO", 33.479, 131.737),
        ]
    )

    assert apply_rjfm_departure_exception(project, _references()) is None
    assert "ui_state" not in project.metadata


def test_legacy_v1_guidance_left_turn_fields_load_into_generic_contract() -> None:
    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("RJFO", 33.479, 131.737),
        ]
    )
    plan = apply_rjfm_departure_exception(project, _references())
    assert plan is not None
    legacy_plan = plan.model_dump(mode="json")
    legacy_plan["rule_version"] = "RJFM_NORTHBOUND_R6_5_1_V1"
    legacy_guidance = {
        "rule_version": "RJFM_NORTHBOUND_R6_5_1_V1",
        "reference_revision": "fixture-r1",
        "reference_content_fingerprint": "b" * 64,
        "source_effective_dates": {},
        "generated_against_fingerprint": "a" * 64,
        "candidates": [
            {
                "runway": runway,
                "status": "UNAVAILABLE",
                "full_left_turns": 1,
                "partial_left_turn_deg": 42.0,
            }
            for runway in ("09", "27")
        ],
        "center_route": [
            plan.umk.model_dump(mode="json"),
            plan.over_field.model_dump(mode="json"),
            plan.omaru.model_dump(mode="json"),
        ],
        "limitations": [],
    }

    restored = load_persisted_ui_state(
        {
            "state_schema_version": 5,
            "rjfm_departure_plan": legacy_plan,
            "rjfm_departure_guidance": legacy_guidance,
        }
    )

    assert restored.rjfm_departure_plan is not None
    assert restored.rjfm_departure_plan.rule_version == "RJFM_NORTHBOUND_R6_5_1_V1"
    assert restored.rjfm_departure_guidance is not None
    assert restored.rjfm_departure_guidance.rule_version == "RJFM_NORTHBOUND_R6_5_1_V1"
    assert all(
        candidate.turn_direction is RjfmTurnDirection.LEFT
        and candidate.full_turns == 1
        and candidate.partial_turn_deg == 42.0
        for candidate in restored.rjfm_departure_guidance.candidates
    )
    dumped_candidates = restored.rjfm_departure_guidance.model_dump(mode="json")[
        "candidates"
    ]
    assert all("full_left_turns" not in candidate for candidate in dumped_candidates)
    assert all("partial_left_turn_deg" not in candidate for candidate in dumped_candidates)

    new_guidance = RjfmDepartureGuidance(
        reference_revision="fixture-r1",
        reference_content_fingerprint="b" * 64,
        generated_against_fingerprint="a" * 64,
        candidates=[
            RjfmRunwayGuidance(
                runway="09",
                status=RjfmGuidanceStatus.UNAVAILABLE,
                turn_direction=RjfmTurnDirection.LEFT,
            ),
            RjfmRunwayGuidance(
                runway="27",
                status=RjfmGuidanceStatus.UNAVAILABLE,
                turn_direction=RjfmTurnDirection.RIGHT,
            ),
        ],
        center_route=[plan.umk, plan.over_field, plan.omaru],
    )
    assert new_guidance.rule_version == "RJFM_NORTHBOUND_R6_5_1_V2"


def test_current_guidance_requires_direction_and_runway_mapping() -> None:
    with pytest.raises(ValueError, match="turn_direction"):
        RjfmRunwayGuidance(
            runway="09",
            status=RjfmGuidanceStatus.UNAVAILABLE,
        )

    project = _project(
        [
            ("RJFM", 31.877, 131.449),
            ("UMK-ish", 32.08, 131.50),
            ("RJFO", 33.479, 131.737),
        ]
    )
    plan = apply_rjfm_departure_exception(project, _references())
    assert plan is not None
    with pytest.raises(
        ValueError,
        match="current RJFM guidance requires LEFT for RWY09 and RIGHT for RWY27",
    ):
        RjfmDepartureGuidance(
            reference_revision=plan.reference_revision,
            reference_content_fingerprint=plan.reference_content_fingerprint,
            generated_against_fingerprint="a" * 64,
            candidates=[
                RjfmRunwayGuidance(
                    runway="09",
                    status=RjfmGuidanceStatus.UNAVAILABLE,
                    turn_direction=RjfmTurnDirection.RIGHT,
                ),
                RjfmRunwayGuidance(
                    runway="27",
                    status=RjfmGuidanceStatus.UNAVAILABLE,
                    turn_direction=RjfmTurnDirection.LEFT,
                ),
            ],
            center_route=[plan.umk, plan.over_field, plan.omaru],
        )
