from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from test_rjfm_inbound_calculation import _inbound_project, _service

from autonavlog.application.rjfm_inbound_guidance import (
    InboundGuidanceRequest,
    InboundGuidanceSolution,
    InboundGuidanceStatus,
)
from autonavlog.application.rjfm_inbound_plan import apply_rjfm_inbound_exception
from autonavlog.application.rjfm_inbound_service import build_rjfm_inbound_guidance
from autonavlog.domain.enums import FlightPhase, RouteNodeRole
from autonavlog.domain.planning import load_persisted_ui_state
from autonavlog.domain.project import NavSection, RouteNode
from autonavlog.storage.rjfm_inbound_reference import (
    AirspaceBoundary,
    GeoPoint,
    InboundPolicy,
    RjfmInboundGuidanceReference,
)
from autonavlog.weather.fake_provider import FakeWeatherProvider

ROOT = Path(__file__).resolve().parents[2]


def _available_reference() -> RjfmInboundGuidanceReference:
    return RjfmInboundGuidanceReference(
        revision="synthetic-inbound-v1",
        content_fingerprint="c" * 64,
        status="AVAILABLE",
        policy=InboundPolicy(250.0, 290.0, 5.0, 0.5),
        mze_position=GeoPoint(31.87872777777778, 131.43746666666667),
        mze_elevation_ft_msl=54.0,
        boundary=AirspaceBoundary(
            source_id="synthetic-primary-boundary",
            revision="synthetic-boundary-v1",
            checksum_sha256="d" * 64,
            polygon_vertices=(
                GeoPoint(31.5, 130.7),
                GeoPoint(31.5, 130.9),
                GeoPoint(31.7, 130.9),
            ),
        ),
    )


def _calculated_inbound(airports, performance_repository, *, three_inbound_legs: bool = False):
    project, references = _inbound_project()
    if three_inbound_legs:
        nodes = project.ordered_nodes()
        umk_index = next(index for index, node in enumerate(nodes) if node.name == "renamed UMK")
        middle = nodes[umk_index + 1]
        vrep = nodes[umk_index + 2]
        bridge = RouteNode(
            sequence=middle.sequence + 1,
            name="MID-2",
            latitude_deg=(middle.latitude_deg + vrep.latitude_deg) / 2.0,
            longitude_deg=(middle.longitude_deg + vrep.longitude_deg) / 2.0,
            role=RouteNodeRole.ROUTE_POINT,
        )
        project.route_nodes = [
            *nodes[: umk_index + 2],
            bridge,
            *[
                node.model_copy(update={"sequence": node.sequence + 1})
                for node in nodes[umk_index + 2 :]
            ],
        ]
        replaced_sections: list[NavSection] = []
        for section in project.ordered_sections():
            if section.from_node_id == middle.id and section.to_node_id == vrep.id:
                replaced_sections.extend(
                    (
                        section.model_copy(update={"to_node_id": bridge.id}),
                        NavSection(
                            sequence=section.sequence + 1,
                            from_node_id=bridge.id,
                            to_node_id=vrep.id,
                            phase=FlightPhase.DESCENT,
                            planned_altitude_ft_msl=4500.0,
                        ),
                    )
                )
            else:
                replaced_sections.append(section)
        project.sections = [
            section.model_copy(update={"sequence": index})
            for index, section in enumerate(replaced_sections)
        ]
    plan = apply_rjfm_inbound_exception(
        project,
        references,
        adopted_vrep_altitude_ft_msl=1500,
    )
    assert plan is not None
    outcome = _service(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    assert not outcome.blockers
    return project, outcome, load_persisted_ui_state(project.metadata["ui_state"])


def _available_solution(reference: RjfmInboundGuidanceReference) -> InboundGuidanceSolution:
    return InboundGuidanceSolution(
        status=InboundGuidanceStatus.AVAILABLE,
        reason_code=None,
        raw_turn_point=GeoPoint(32.05, 131.18),
        rounded_turn_point=GeoPoint(32.04, 131.17),
        bearing_magnetic_deg=270.0,
        actual_bearing_magnetic_deg=270.1,
        raw_extra_distance_nm=6.25,
        extra_distance_nm=6.5,
        raw_predicted_ete_min=7.01,
        predicted_ete_min=7.18,
        raw_dme_nm=18.24,
        rounded_dme_nm=18.5,
        raw_turn_altitude_ft_msl=3200.0,
        rounded_turn_altitude_ft_msl=3190.0,
        raw_minimum_boundary_clearance_nm=1.2,
        minimum_boundary_clearance_nm=1.1,
        reference_revision=reference.revision,
    )


def _assert_no_numeric_diagnostics(guidance) -> None:
    assert guidance.raw_turn_point is None
    assert guidance.rounded_turn_point is None
    assert guidance.bearing_magnetic_deg is None
    assert guidance.actual_bearing_magnetic_deg is None
    assert guidance.raw_extra_distance_nm is None
    assert guidance.extra_distance_nm is None
    assert guidance.raw_predicted_ete_min is None
    assert guidance.predicted_ete_min is None
    assert guidance.raw_dme_nm is None
    assert guidance.rounded_dme_nm is None
    assert guidance.raw_turn_altitude_ft_msl is None
    assert guidance.rounded_turn_altitude_ft_msl is None
    assert guidance.raw_minimum_boundary_clearance_nm is None
    assert guidance.minimum_boundary_clearance_nm is None


def test_service_uses_authoritative_operational_descent_and_keeps_core_unchanged(
    airports,
    performance_repository,
) -> None:
    project, outcome, state = _calculated_inbound(airports, performance_repository)
    reference = _available_reference()
    captured: list[InboundGuidanceRequest] = []
    core_before = outcome.model_dump(mode="json")

    def solver(request: InboundGuidanceRequest) -> InboundGuidanceSolution:
        captured.append(request)
        return _available_solution(reference)

    guidance = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="e" * 64,
        solver=solver,
    )

    assert guidance is not None
    assert guidance.status == "AVAILABLE"
    assert guidance.rounded_dme_nm == 18.5
    assert guidance.raw_turn_point is not None
    assert guidance.rounded_turn_point is not None
    assert guidance.rounded_turn_altitude_ft_msl == pytest.approx(3190.0)
    assert guidance.generated_against_fingerprint == "e" * 64
    assert len(captured) == 1
    request = captured[0]
    assert request.required_ete_min == pytest.approx(7.0)
    assert request.descent_tas_kt > 0
    assert request.wind_speed_kt >= 0
    assert state.rjfm_inbound_plan is not None
    umk_node = next(
        node for node in project.route_nodes if node.id == state.rjfm_inbound_plan.umk_node_id
    )
    vrep_node = next(
        node for node in project.route_nodes if node.id == state.rjfm_inbound_plan.vrep_node_id
    )
    assert request.umk.latitude_deg == pytest.approx(umk_node.latitude_deg)
    assert request.vrep.latitude_deg == pytest.approx(vrep_node.latitude_deg)
    assert request.magnetic_variation_deg_east == pytest.approx(8.0)
    assert request.descent_start_altitude_ft_msl == pytest.approx(4500.0)
    assert request.target_altitude_ft_msl == pytest.approx(1500.0)
    assert request.descent_rate_fpm == pytest.approx(500.0)
    assert outcome.model_dump(mode="json") == core_before


def test_production_unavailable_reference_never_calls_solver(
    airports,
    performance_repository,
) -> None:
    project, outcome, state = _calculated_inbound(airports, performance_repository)
    reference = RjfmInboundGuidanceReference.from_directory(
        ROOT / "data" / "reference" / "rjfm-inbound-guidance"
    )

    def solver(_: InboundGuidanceRequest) -> InboundGuidanceSolution:
        raise AssertionError("unavailable reference must not invoke the solver")

    guidance = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="f" * 64,
        solver=solver,
    )

    assert guidance is not None
    assert guidance.status == "UNAVAILABLE"
    assert guidance.reason_code == "KS43_HORIZONTAL_BOUNDARY_UNVERIFIED"
    _assert_no_numeric_diagnostics(guidance)


@pytest.mark.parametrize(
    "status",
    [
        InboundGuidanceStatus.ADVERSE_WIND,
        InboundGuidanceStatus.NO_SOLUTION,
        InboundGuidanceStatus.CONVERGENCE_FAILURE,
    ],
)
def test_solver_failures_are_nonblocking_and_suppress_numeric_diagnostics(
    airports,
    performance_repository,
    status: InboundGuidanceStatus,
) -> None:
    project, outcome, state = _calculated_inbound(airports, performance_repository)
    reference = _available_reference()
    core_before = outcome.model_dump(mode="json")

    guidance = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="a" * 64,
        solver=lambda _: InboundGuidanceSolution(
            status=status,
            reason_code=status.value,
            reference_revision=reference.revision,
        ),
    )

    assert guidance is not None
    assert guidance.status == status.value
    assert guidance.reason_code == status.value
    _assert_no_numeric_diagnostics(guidance)
    assert outcome.model_dump(mode="json") == core_before


def test_reference_mismatch_and_stale_authoritative_inputs_suppress_guidance(
    airports,
    performance_repository,
) -> None:
    project, outcome, state = _calculated_inbound(airports, performance_repository)
    reference = _available_reference()
    mismatch = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="b" * 64,
        solver=lambda _: replace(
            _available_solution(reference), reference_revision="wrong-reference"
        ),
    )

    assert mismatch is not None
    assert mismatch.status == "UNAVAILABLE"
    assert mismatch.reason_code == "REFERENCE_REVISION_MISMATCH"
    _assert_no_numeric_diagnostics(mismatch)

    project.descent_rate_fpm = 1000
    stale = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="b" * 64,
    )

    assert stale is not None
    assert stale.status == "UNAVAILABLE"
    assert stale.reason_code == "INBOUND_PLAN_STALE"
    _assert_no_numeric_diagnostics(stale)


def test_solver_exception_and_malformed_available_output_are_nonblocking(
    airports,
    performance_repository,
) -> None:
    project, outcome, state = _calculated_inbound(airports, performance_repository)
    reference = _available_reference()
    core_before = outcome.model_dump(mode="json")

    exception = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="1" * 64,
        solver=lambda _: (_ for _ in ()).throw(RuntimeError("synthetic solver failure")),
    )
    assert exception is not None
    assert exception.status == "UNAVAILABLE"
    assert exception.reason_code == "SOLVER_EXCEPTION"
    _assert_no_numeric_diagnostics(exception)

    malformed = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="2" * 64,
        solver=lambda _: replace(_available_solution(reference), raw_dme_nm=float("nan")),
    )
    assert malformed is not None
    assert malformed.status == "UNAVAILABLE"
    assert malformed.reason_code == "AVAILABLE_SOLUTION_INCOMPLETE"
    _assert_no_numeric_diagnostics(malformed)

    negative = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="3" * 64,
        solver=lambda _: replace(_available_solution(reference), extra_distance_nm=-0.1),
    )
    assert negative is not None
    assert negative.status == "UNAVAILABLE"
    assert negative.reason_code == "AVAILABLE_SOLUTION_INCOMPLETE"
    _assert_no_numeric_diagnostics(negative)
    assert outcome.model_dump(mode="json") == core_before


def test_route_coordinate_and_outcome_identity_staleness_never_calls_solver(
    airports,
    performance_repository,
) -> None:
    project, outcome, state = _calculated_inbound(airports, performance_repository)
    reference = _available_reference()
    assert state.rjfm_inbound_plan is not None

    umk = next(
        node for node in project.route_nodes if node.id == state.rjfm_inbound_plan.umk_node_id
    )
    umk.latitude_deg += 0.01
    stale_route = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="4" * 64,
        solver=lambda _: (_ for _ in ()).throw(AssertionError("stale route invoked solver")),
    )
    assert stale_route is not None
    assert stale_route.reason_code == "INBOUND_PLAN_STALE"
    _assert_no_numeric_diagnostics(stale_route)

    project, outcome, state = _calculated_inbound(airports, performance_repository)
    wrong_outcome = outcome.model_copy(update={"project_id": uuid4()})
    mismatch = build_rjfm_inbound_guidance(
        project,
        wrong_outcome,
        state,
        reference,
        generated_against_fingerprint="5" * 64,
        solver=lambda _: (_ for _ in ()).throw(AssertionError("wrong outcome invoked solver")),
    )
    assert mismatch is not None
    assert mismatch.reason_code == "OUTCOME_PROJECT_MISMATCH"
    _assert_no_numeric_diagnostics(mismatch)


def test_service_accepts_every_physical_umk_to_vrep_leg_in_order(
    airports,
    performance_repository,
) -> None:
    project, outcome, state = _calculated_inbound(
        airports,
        performance_repository,
        three_inbound_legs=True,
    )
    reference = _available_reference()
    calls: list[InboundGuidanceRequest] = []
    guidance = build_rjfm_inbound_guidance(
        project,
        outcome,
        state,
        reference,
        generated_against_fingerprint="6" * 64,
        solver=lambda request: calls.append(request) or _available_solution(reference),
    )

    assert guidance is not None
    assert guidance.status == "AVAILABLE"
    assert len(calls) == 1
    assert state.rjfm_inbound_plan is not None
    nodes = project.ordered_nodes()
    umk_index = next(
        index for index, node in enumerate(nodes) if node.id == state.rjfm_inbound_plan.umk_node_id
    )
    vrep_index = next(
        index for index, node in enumerate(nodes) if node.id == state.rjfm_inbound_plan.vrep_node_id
    )
    expected_ids = [section.id for section in project.ordered_sections()[umk_index:vrep_index]]
    actual_ids: list[object] = []
    for section in outcome.sections:
        if section.performance_metadata.get("type") != "rjfm_inbound_operational_descent":
            continue
        if not actual_ids or actual_ids[-1] != section.section_id:
            actual_ids.append(section.section_id)
    assert actual_ids == expected_ids
