from __future__ import annotations

import shutil
from datetime import UTC, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from autonavlog.application.calculation_service import (
    CalculationPolicies,
    CalculationService,
)
from autonavlog.application.navlog_display import (
    NavLogPhysicalLeg,
    build_navlog_summary,
)
from autonavlog.application.project_service import ProjectService
from autonavlog.application.vertical_profile import descent_profile_from_metadata
from autonavlog.domain.calculation import CalculationOutcome, Issue
from autonavlog.domain.enums import (
    AdoptedSource,
    Availability,
    DisplayCellState,
    FlightPhase,
    IssueSeverity,
    ProjectStatus,
    RouteNodeRole,
    ValueState,
    WeatherRequestKind,
)
from autonavlog.domain.project import ManualWind, NavSection, RouteNode
from autonavlog.domain.values import AdoptedValue
from autonavlog.domain.weather import WeatherResult
from autonavlog.nav.airspeed import tas_from_cas
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.weather.destination_taf import DestinationWindForecast
from autonavlog.weather.fake_provider import FakeWeatherProvider


def _project_with_climb_endpoint_at_25_nm(project):
    aligned = project.model_copy(deep=True)
    departure, turn = aligned.ordered_nodes()[:2]
    course = geodesic_leg(
        departure.latitude_deg,
        departure.longitude_deg,
        turn.latitude_deg,
        turn.longitude_deg,
    ).initial_true_course_deg
    turn.latitude_deg, turn.longitude_deg = point_along_leg(
        departure.latitude_deg,
        departure.longitude_deg,
        course,
        25.0,
    )
    return aligned


def _displayed_parent_distance_totals(outcome: CalculationOutcome) -> list[float]:
    return [
        float(row.distance.text.split(" / ")[0])
        for row in outcome.display_rows
        if row.row_type == "PHYSICAL_LEG_SUMMARY" and row.distance.text is not None
    ]


def _displayed_ttl_distance(outcome: CalculationOutcome) -> float:
    parent_rows = [
        row for row in outcome.display_rows if row.row_type == "PHYSICAL_LEG_SUMMARY"
    ]
    assert parent_rows
    assert parent_rows[-1].distance.text is not None
    return float(parent_rows[-1].distance.text.split(" / ")[1])


def _assert_display_distance_invariants(outcome: CalculationOutcome) -> None:
    parent_rows = [
        row for row in outcome.display_rows if row.row_type == "PHYSICAL_LEG_SUMMARY"
    ]
    assert parent_rows

    displayed_parent_total = 0.0
    for parent in parent_rows:
        assert parent.distance.text is not None
        parent_total = float(parent.distance.text.split(" / ")[0])
        group = [row for row in outcome.display_rows if row.section_id == parent.section_id]
        detail_rows = [row for row in group if row.row_type == "CALCULATION_ZONE"]
        if detail_rows:
            assert sum(float(row.distance.text or "nan") for row in detail_rows) == pytest.approx(
                parent_total
            )
        displayed_parent_total += parent_total

    assert displayed_parent_total == pytest.approx(_displayed_ttl_distance(outcome))


def test_full_calculation_iteration_and_navlog_projection(
    airports,
    performance_repository,
    project,
) -> None:
    service = CalculationService(airports, performance_repository)
    provider = FakeWeatherProvider()
    aligned_project = _project_with_climb_endpoint_at_25_nm(project)
    outcome = service.calculate(aligned_project, provider)
    assert outcome.converged
    assert outcome.selected_forecast_run_id == "20260728000000"
    assert len(outcome.sections) == 3
    assert outcome.sections[0].to_name == "RCA"
    assert outcome.sections[1].from_name == "RCA"
    first_route_node = aligned_project.ordered_nodes()[0]
    assert outcome.sections[0].from_latitude_deg == pytest.approx(
        first_route_node.latitude_deg
    )
    assert outcome.sections[0].from_longitude_deg == pytest.approx(
        first_route_node.longitude_deg
    )
    assert outcome.sections[0].to_latitude_deg == pytest.approx(
        outcome.sections[1].from_latitude_deg
    )
    assert outcome.sections[0].to_longitude_deg == pytest.approx(
        outcome.sections[1].from_longitude_deg
    )
    last_route_node = aligned_project.ordered_nodes()[-1]
    assert outcome.sections[-1].to_latitude_deg == pytest.approx(
        last_route_node.latitude_deg
    )
    assert outcome.sections[-1].to_longitude_deg == pytest.approx(
        last_route_node.longitude_deg
    )
    positioned_rows = [
        row
        for row in outcome.display_rows
        if row.row_type in {"PHYSICAL_LEG_SUMMARY", "CALCULATION_ZONE"}
    ]
    assert positioned_rows
    assert all(row.from_latitude_deg is not None for row in positioned_rows)
    assert all(row.from_longitude_deg is not None for row in positioned_rows)
    assert all(row.to_latitude_deg is not None for row in positioned_rows)
    assert all(row.to_longitude_deg is not None for row in positioned_rows)
    assert positioned_rows[0].from_latitude_deg == outcome.sections[0].from_latitude_deg
    assert positioned_rows[0].from_longitude_deg == outcome.sections[0].from_longitude_deg
    first_summary = positioned_rows[0]
    first_leg_zones = [
        section for section in outcome.sections if section.section_id == first_summary.section_id
    ]
    assert first_summary.to_latitude_deg == first_leg_zones[-1].to_latitude_deg
    assert first_summary.to_longitude_deg == first_leg_zones[-1].to_longitude_deg
    for row in positioned_rows:
        if row.row_type != "CALCULATION_ZONE":
            continue
        assert row.source_result_sequence is not None
        source = outcome.sections[row.source_result_sequence]
        assert row.to_latitude_deg == source.to_latitude_deg
        assert row.to_longitude_deg == source.to_longitude_deg
    assert not outcome.blockers
    assert outcome.status == ProjectStatus.READY_FOR_COPY
    assert outcome.derived_points[0].type.value == "RCA"
    assert outcome.sections[0].wind_speed_kt.adopted() == 0
    assert outcome.sections[0].wind_direction_deg_from.adopted() is None
    assert outcome.sections[-1].remaining_fuel_gal.adopted() is not None
    assert len(provider.query_history) >= 2
    assert all(run_id == "20260728000000" for run_id, _ in provider.query_history)


def _automatic_value(value: float | None) -> AdoptedValue[float]:
    return AdoptedValue(
        automatic_value=value,
        automatic_status=ValueState.AUTO if value is not None else ValueState.UNAVAILABLE,
        adopted_source=AdoptedSource.AUTOMATIC if value is not None else None,
    )


def test_navlog_summary_uses_canonical_split_zones_and_hhmm_rounding(
    airports,
    performance_repository,
    project,
) -> None:
    outcome = CalculationService(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    source = outcome.sections[0]
    first = source.model_copy(
        update={
            "sequence": 0,
            "zone_distance_nm": _automatic_value(1.2),
            "cumulative_distance_nm": _automatic_value(1.2),
            "zone_ete_seconds": _automatic_value(29.5 * 60),
            "cumulative_ete_seconds": _automatic_value(29.5 * 60),
        }
    )
    second = source.model_copy(
        update={
            "sequence": 1,
            "zone_distance_nm": _automatic_value(1.3),
            "cumulative_distance_nm": _automatic_value(2.5),
            "zone_ete_seconds": _automatic_value(30 * 60),
            "cumulative_ete_seconds": _automatic_value(59.5 * 60),
        }
    )
    summary = build_navlog_summary(
        [first, second],
        [
            NavLogPhysicalLeg(
                section_ids=(source.section_id,),
                phase=source.phase,
                start_name=source.from_name,
                end_name=source.to_name,
                adopted_distance_nm=2.5,
            )
        ],
    )

    assert summary.distance.text == "2.5"
    assert summary.time.text == "1:00"


def test_navlog_summary_keeps_distance_when_an_early_ete_is_unavailable(
    airports,
    performance_repository,
    project,
) -> None:
    outcome = CalculationService(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )
    first = outcome.sections[0]
    unavailable_first = first.model_copy(
        update={
            "zone_distance_nm": _automatic_value(1.2),
            "zone_ete_seconds": _automatic_value(None),
        }
    )
    available_second = first.model_copy(
        update={
            "section_id": uuid4(),
            "sequence": 1,
            "zone_distance_nm": _automatic_value(2.0),
            "zone_ete_seconds": _automatic_value(60.0),
        }
    )
    summary = build_navlog_summary(
        [unavailable_first, available_second],
        [
            NavLogPhysicalLeg(
                section_ids=(first.section_id,),
                phase=first.phase,
                start_name=first.from_name,
                end_name=first.to_name,
                adopted_distance_nm=1.2,
            ),
            NavLogPhysicalLeg(
                section_ids=(available_second.section_id,),
                phase=available_second.phase,
                start_name=available_second.from_name,
                end_name=available_second.to_name,
                adopted_distance_nm=2.0,
            ),
        ],
    )

    assert summary.distance.text == "3.0"
    assert summary.time.text == "未取得"
    assert summary.time.reason_code == "SUMMARY_TIME_UNAVAILABLE"


def test_cruise_power_extrapolation_stays_in_metadata_for_each_zone(airports, project) -> None:
    from autonavlog.performance.repository import PerformanceRepository

    boundary_project = project.model_copy(deep=True)
    original_destination = boundary_project.ordered_nodes()[-1]
    middle = RouteNode(
        sequence=2,
        name="TP2",
        latitude_deg=33.0,
        longitude_deg=131.6,
        role=RouteNodeRole.TURN_POINT,
    )
    original_destination.sequence = 3
    boundary_project.route_nodes.append(middle)
    first, second = boundary_project.sections
    first.phase = FlightPhase.CRUISE
    first.planned_altitude_ft_msl = 13_020
    first.manual_temperature_c_by_phase = {FlightPhase.CRUISE: 4.0}
    second.sequence = 2
    second.from_node_id = middle.id
    second.phase = FlightPhase.CRUISE
    second.planned_altitude_ft_msl = 13_020
    second.manual_temperature_c_by_phase = {FlightPhase.CRUISE: 4.0}
    boundary_project.sections.insert(
        1,
        NavSection(
            sequence=1,
            from_node_id=first.to_node_id,
            to_node_id=middle.id,
            phase=FlightPhase.CRUISE,
            planned_altitude_ft_msl=13_020,
            manual_temperature_c_by_phase={FlightPhase.CRUISE: 4.0},
        ),
    )

    outcome = CalculationService(
        airports,
        PerformanceRepository.from_directory(Path("data/performance")),
    ).calculate(boundary_project, FakeWeatherProvider())

    assert not any(
        issue.code == "CRUISE_POWER_TABLE_BOUNDARY_USED"
        for issue in outcome.issues
    )
    cruise_sections = [
        section for section in outcome.sections if section.phase == FlightPhase.CRUISE
    ]
    assert len(cruise_sections) == 3
    for section in cruise_sections:
        provenance = section.performance_metadata["interpolation"]["boundary_provenance"]
        assert len(provenance) == 3
        assert all(
            item["axis"] == "POWER_PERCENT"
            and item["requested_value"] == 65.0
            and item["extrapolated"] is True
            for item in provenance
        )


def test_destination_surface_temperature_uses_calculated_arrival_time(
    airports,
    performance_repository,
    project,
) -> None:
    class RequirementRecordingProvider(FakeWeatherProvider):
        def __init__(self) -> None:
            super().__init__()
            self.inspected_requirements = []

        def inspect_run_status(self, selected_run_id, requirement):
            self.inspected_requirements.append(requirement)
            return super().inspect_run_status(selected_run_id, requirement)

    provider = RequirementRecordingProvider()
    service = CalculationService(airports, performance_repository)

    outcome = service.calculate(project, provider)

    cumulative_ete = outcome.sections[-1].cumulative_ete_seconds.adopted()
    assert cumulative_ete is not None
    calculated_arrival = project.planned_departure_time_jst.astimezone(
        UTC
    ) + timedelta(seconds=cumulative_ete)
    destination_requests = [
        request
        for _, batch in provider.query_history
        for request in batch
        if request.request_id == "destination:surface"
    ]
    assert len(destination_requests) >= 2
    assert destination_requests[1].valid_time_utc == calculated_arrival
    assert destination_requests[-1].valid_time_utc == calculated_arrival
    assert provider.inspected_requirements
    assert (
        calculated_arrival
        in provider.inspected_requirements[-1].valid_times_utc
    )


def test_final_destination_surface_query_is_refreshed_to_exact_arrival(
    airports,
    performance_repository,
    project,
) -> None:
    provider = FakeWeatherProvider()
    service = CalculationService(
        airports,
        performance_repository,
        policies=CalculationPolicies(max_iterations=1),
    )

    outcome = service.calculate(project, provider)

    cumulative_ete = outcome.sections[-1].cumulative_ete_seconds.adopted()
    assert cumulative_ete is not None
    calculated_arrival = project.planned_departure_time_jst.astimezone(
        UTC
    ) + timedelta(seconds=cumulative_ete)
    destination_requests = [
        request
        for _, batch in provider.query_history
        for request in batch
        if request.request_id == "destination:surface"
    ]
    assert len(destination_requests) == 2
    assert destination_requests[0].valid_time_utc != calculated_arrival
    assert destination_requests[-1].valid_time_utc == calculated_arrival
    assert destination_requests[-1].metadata["timing_policy"] == (
        "FINAL_CALCULATED_ARRIVAL"
    )


def test_failed_exact_destination_surface_query_clears_approximate_temperature(
    airports,
    performance_repository,
    project,
) -> None:
    visual_project = project.model_copy(deep=True)
    visual_project.sections[-1].phase = FlightPhase.VISUAL_ARRIVAL

    class ExactDestinationFailureProvider(FakeWeatherProvider):
        def query_batch(self, forecast_run_id, requests):
            if len(requests) == 1 and requests[0].request_id == "destination:surface":
                raise RuntimeError("exact destination temperature unavailable")
            return super().query_batch(forecast_run_id, requests)

    outcome = CalculationService(
        airports,
        performance_repository,
        policies=CalculationPolicies(max_iterations=1),
    ).calculate(visual_project, ExactDestinationFailureProvider())

    destination_row = next(
        row for row in outcome.display_rows if row.row_type == "DESTINATION_INFO"
    )
    destination = airports.get(visual_project.destination_airport_id)
    assert destination_row.to_latitude_deg == destination.latitude_deg
    assert destination_row.to_longitude_deg == destination.longitude_deg
    assert destination_row.toat.state == DisplayCellState.UNAVAILABLE
    assert destination_row.toat.text == "未取得"
    assert any(issue.code == "WEATHER_QUERY_FAILED" for issue in outcome.blockers)


def test_variation_changes_by_physical_leg_departure_and_ignores_legacy_default(
    airports,
    performance_repository,
    project,
) -> None:
    legacy = project.model_copy(deep=True)
    legacy.default_variation_deg_east = -12.5

    outcome = CalculationService(airports, performance_repository).calculate(
        legacy,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    source_departures = {
        str(section.id): legacy.route_nodes[section.sequence].latitude_deg
        for section in legacy.ordered_sections()
    }
    for result in outcome.sections:
        expected = 8.0 if source_departures[str(result.section_id)] >= 32.0 else 7.0
        assert result.variation_deg_east.adopted() == expected
        assert result.variation_deg_east.adopted_source == AdoptedSource.AUTOMATIC
        assert result.variation_deg_east.automatic_metadata == {
            "rule_version": "DEPARTURE_LATITUDE_32N_V1",
            "method": "LEG_DEPARTURE_LATITUDE_BAND",
            "departure_latitude_deg": source_departures[str(result.section_id)],
            "threshold_latitude_deg": 32.0,
            "threshold_inclusive_side": "NORTH",
            "selected_band": "NORTH" if expected == 8.0 else "SOUTH",
            "degrees_east": expected,
        }


def test_saved_forecast_run_stays_pinned_until_explicitly_changed(
    airports,
    performance_repository,
    project,
) -> None:
    latest_run = "20260728060000"
    saved_run = "20260728000000"
    pinned = project.model_copy(
        deep=True,
        update={"selected_forecast_run_id": saved_run},
    )
    provider = FakeWeatherProvider(runs=(latest_run, saved_run))
    service = CalculationService(airports, performance_repository)

    old_outcome = service.calculate(pinned, provider)

    assert old_outcome.selected_forecast_run_id == saved_run
    assert pinned.selected_forecast_run_id == saved_run
    assert provider.query_history
    assert all(run_id == saved_run for run_id, _ in provider.query_history)
    update = next(
        issue for issue in old_outcome.issues if issue.code == "FORECAST_UPDATE_AVAILABLE"
    )
    assert update.metadata == {
        "selected_run_id": saved_run,
        "latest_compatible_run_id": latest_run,
    }
    assert service.last_forecast_metadata["forecast_run_id"] == saved_run
    assert service.last_forecast_metadata["initial_time_utc"] == ("2026-07-28T00:00:00Z")

    switched = pinned.model_copy(
        deep=True,
        update={"selected_forecast_run_id": latest_run},
    )
    provider.query_history.clear()
    latest_outcome = service.calculate(switched, provider)

    assert latest_outcome.selected_forecast_run_id == latest_run
    assert all(run_id == latest_run for run_id, _ in provider.query_history)
    assert not any(issue.code == "FORECAST_UPDATE_AVAILABLE" for issue in latest_outcome.issues)


def test_climb_leg_is_automatically_split_at_rca_without_losing_distance(
    airports,
    performance_repository,
    project,
) -> None:
    outcome = CalculationService(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    assert outcome.status == ProjectStatus.READY_FOR_COPY
    assert [section.phase for section in outcome.sections] == [
        FlightPhase.CLIMB,
        FlightPhase.CRUISE,
        FlightPhase.CRUISE,
    ]
    assert outcome.sections[0].to_name == "RCA"
    assert outcome.sections[1].from_name == "RCA"
    assert sum(
        section.zone_distance_nm.adopted() or 0.0 for section in outcome.sections
    ) == pytest.approx(
        outcome.sections[-1].cumulative_distance_nm.adopted(),
        abs=1e-9,
    )
    climb_sections = [section for section in outcome.sections if section.phase == FlightPhase.CLIMB]
    climb_metadata = climb_sections[0].performance_metadata
    cruise_metadata = next(
        section.performance_metadata
        for section in outcome.sections
        if section.phase == FlightPhase.CRUISE
    )
    assert cruise_metadata["reason"] == (
        "PWR_LINEAR_OR_65_PERCENT_EXTRAPOLATION_THEN_ISA_LINEAR_THEN_ALTITUDE_LINEAR"
    )
    assert cruise_metadata["selected_cell"]["power_percent"] == 65.0
    assert cruise_metadata["selected_cell"]["rpm"] is None
    assert cruise_metadata["selected_cell"]["map_in_hg"] is None
    assert cruise_metadata["interpolation"]["power_percent"] == 65.0
    assert cruise_metadata["interpolation"]["corners"]
    representative_pressure_altitude = (
        airports.get("RJFM").elevation_ft_msl
        + project.sections[0].planned_altitude_ft_msl
    ) / 2.0
    expected_climb_tas = tas_from_cas(
        111.0,
        representative_pressure_altitude,
        15.0,
    )
    assert climb_metadata["representative_pressure_altitude_ft"] == (
        pytest.approx(representative_pressure_altitude)
    )
    assert climb_metadata["midpoint_temperature_c"] == 15.0
    assert climb_metadata["cas_kt"] == 111.0
    assert climb_metadata["tas_method"] == (
        "CAC_REV19_8-(3)_5_(1)_CAS_111_AT_REPRESENTATIVE_PRESSURE_ALTITUDE"
    )
    assert climb_metadata["poh_table_distance_usage"] == "REFERENCE_ONLY"
    assert climb_metadata["poh_table_distance_reference_nm"] == (
        pytest.approx(climb_metadata["table_distance_nm"])
    )
    assert all(
        section.cas_kt.adopted() == pytest.approx(111.0)
        and section.tas_kt.adopted() == pytest.approx(expected_climb_tas)
        for section in climb_sections
    )
    assert sum(
        section.zone_ete_seconds.adopted() or 0.0 for section in climb_sections
    ) == pytest.approx(
        climb_sections[0].performance_metadata["planned_duration_seconds"],
        abs=1e-6,
    )
    assert sum(
        section.section_fuel_gal.adopted() or 0.0 for section in climb_sections
    ) == pytest.approx(
        climb_sections[0].performance_metadata["planned_fuel_gal"],
        abs=1e-9,
    )
    assert outcome.derived_points[0].along_route_distance_nm == pytest.approx(
        expected_climb_tas * climb_metadata["planned_duration_seconds"] / 3600.0,
        abs=1e-8,
    )
    assert climb_metadata["poh_table_distance_reference_nm"] != pytest.approx(
        outcome.derived_points[0].along_route_distance_nm,
        abs=1e-3,
    )
    assert [point.type.value for point in outcome.derived_points] == ["RCA"]
    first_leg_rows = [
        row for row in outcome.display_rows if row.section_id == project.sections[0].id
    ]
    assert [row.row_type for row in first_leg_rows] == [
        "PHYSICAL_LEG_SUMMARY",
        "CALCULATION_ZONE",
        "CALCULATION_ZONE",
    ]
    summary, *details = first_leg_rows
    assert summary.from_name == "RJFM"
    assert all(row.from_name == "" for row in details)
    assert summary.distance.text is not None
    assert " / " in summary.distance.text
    assert summary.ete.text is not None
    assert " / " in summary.ete.text
    # display_rows intentionally are not a totals source.  In particular the
    # final visual-arrival Leg has only its parent plus destination information.
    assert outcome.sections[-1].cumulative_distance_nm.adopted() == pytest.approx(
        sum(section.zone_distance_nm.adopted() or 0.0 for section in outcome.sections)
    )


def test_rca_split_uses_distinct_phase_altitude_temperature_and_manual_overrides(
    airports,
    performance_repository,
    project,
) -> None:
    routed = project.model_copy(deep=True)
    source = routed.sections[0]
    source.manual_temperature_c = 4.0
    source.manual_temperature_c_by_phase = {FlightPhase.CRUISE: 9.0}
    source = NavSection.model_validate(
        source.model_dump()
        | {
            "manual_wind_direction_deg": 111,
            "manual_wind_speed_kt": 11.0,
            "manual_wind_by_phase": {
                FlightPhase.CRUISE: ManualWind(direction_deg_from=222, speed_kt=22)
            },
        }
    )
    routed.sections[0] = source

    def result_for_representative_altitude(request):
        if request.kind == WeatherRequestKind.SURFACE_TEMPERATURE:
            temperature_c = 20.0
        else:
            assert request.altitude_ft_msl is not None
            temperature_c = request.altitude_ft_msl / 1000.0
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values={
                "u_ms": 0.0,
                "v_ms": 0.0,
                "wind_speed_kt": 0.0,
                "wind_direction_deg_from": None,
                "temperature_c": temperature_c,
            },
        )

    service = CalculationService(airports, performance_repository)
    outcome = service.calculate(
        routed,
        FakeWeatherProvider(result_factory=result_for_representative_altitude),
    )

    assert not outcome.blockers
    split = [section for section in outcome.sections if section.section_id == source.id]
    assert [section.phase for section in split] == [
        FlightPhase.CLIMB,
        FlightPhase.CRUISE,
    ]
    assert [section.planned_altitude_ft_msl.adopted() for section in split] == [
        pytest.approx(5_000.0),
        pytest.approx(5_000.0),
    ]
    assert [section.temperature_c.automatic_value for section in split] == [
        pytest.approx(2.51),
        pytest.approx(5.0),
    ]
    assert [section.temperature_c.adopted() for section in split] == [
        pytest.approx(4.0),
        pytest.approx(9.0),
    ]
    assert [section.wind_direction_deg_from.adopted() for section in split] == [
        pytest.approx(111.0),
        pytest.approx(222.0),
    ]
    assert [section.wind_speed_kt.adopted() for section in split] == [
        pytest.approx(11.0),
        pytest.approx(22.0),
    ]
    assert all(section.wind_speed_kt.adopted_source == AdoptedSource.MANUAL for section in split)
    requests = {
        request.metadata["phase"]: request
        for request in service.last_weather_requests
        if request.request_id.startswith(f"section:{source.id}:")
    }
    assert requests["CLIMB"].request_id.endswith(":aloft")
    assert requests["CRUISE"].request_id.endswith(":cruise")


def test_manual_low_altitude_and_hot_toat_keep_cruise_outputs_complete(
    airports,
    performance_repository,
    project,
) -> None:
    routed = project.model_copy(deep=True)
    for section in routed.sections:
        section.planned_altitude_ft_msl = 1_500.0
        section.manual_temperature_c = 60.0
        section.manual_temperature_c_by_phase = {FlightPhase.CRUISE: 60.0}
    routed.sections[0].manual_temperature_c = 15.0

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    unexpected = [
        issue
        for issue in outcome.blockers
        if issue.code in {"CRUISE_PERFORMANCE_UNAVAILABLE", "CALCULATION_OUTPUT_INCOMPLETE"}
    ]
    assert not unexpected, [(issue.code, issue.message) for issue in unexpected]
    cruise = [section for section in outcome.sections if section.phase == FlightPhase.CRUISE]
    assert cruise
    assert all(
        section.cas_kt.adopted() is not None
        and section.tas_kt.adopted() is not None
        and section.zone_ete_seconds.adopted() is not None
        and section.section_fuel_gal.adopted() is not None
        for section in cruise
    )
    warning_codes = {issue.code for issue in outcome.issues}
    assert "CRUISE_PRESSURE_ALTITUDE_TABLE_BOUNDARY_USED" in warning_codes
    assert "CRUISE_ISA_DEVIATION_TABLE_BOUNDARY_USED" in warning_codes
    assert "CRUISE_POWER_TABLE_BOUNDARY_USED" not in warning_codes
    metadata = cruise[0].performance_metadata
    assert metadata["requested_condition"] == {
        "pressure_altitude_ft": 1_500.0,
        "isa_deviation_c": pytest.approx(47.9718),
    }
    selected_condition = metadata["selected_condition"]
    assert selected_condition["pressure_altitude_ft"] == 4_000.0
    assert selected_condition["isa_deviation_c"] == 15.0
    assert selected_condition["power_percent_by_corner"] == [
        {
            "pressure_altitude_ft": 4_000.0,
            "isa_deviation_c": 15.0,
            "power_percent": 65.0,
        }
    ]


def test_cruise_equipment_adjustments_apply_after_poh_interpolation_only(
    airports,
    performance_repository,
    project,
) -> None:
    service = CalculationService(airports, performance_repository)
    outcomes = {}
    for nose_fairing_enabled, air_conditioning_enabled in (
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ):
        configured = project.model_copy(deep=True)
        configured.nose_fairing_enabled = nose_fairing_enabled
        configured.air_conditioning_enabled = air_conditioning_enabled
        outcomes[(nose_fairing_enabled, air_conditioning_enabled)] = service.calculate(
            configured,
            FakeWeatherProvider(),
        )

    source_section_id = str(project.sections[1].id)

    def source_cruise(outcome):
        return next(
            section
            for section in outcome.sections
            if section.phase == FlightPhase.CRUISE
            and section.performance_metadata["phase_segment"]["source_section_id"]
            == source_section_id
        )

    sections = {
        configuration: source_cruise(outcome)
        for configuration, outcome in outcomes.items()
    }
    metadata = {
        configuration: section.performance_metadata
        for configuration, section in sections.items()
    }
    table_ktas = metadata[(False, False)]["poh_table_ktas"]
    expected_adjustments = {
        (False, False): (-10.0, -10.0, 0.0),
        (True, False): (0.0, 0.0, 0.0),
        (False, True): (-12.0, -10.0, -2.0),
        (True, True): (-2.0, 0.0, -2.0),
    }
    for configuration, (total, nose, air_conditioning) in expected_adjustments.items():
        assert sections[configuration].tas_kt.adopted() == pytest.approx(table_ktas + total)
        assert metadata[configuration]["nose_fairing_adjustment_ktas"] == nose
        assert metadata[configuration]["air_conditioning_adjustment_ktas"] == air_conditioning
        assert metadata[configuration]["selected_cell"]["gph"] == (
            metadata[(False, False)]["selected_cell"]["gph"]
        )
    assert sections[(True, False)].ground_speed_kt.adopted() > (
        sections[(False, True)].ground_speed_kt.adopted()
    )
    assert sections[(True, False)].zone_ete_seconds.adopted() < (
        sections[(False, True)].zone_ete_seconds.adopted()
    )
    assert sections[(True, False)].section_fuel_gal.adopted() < (
        sections[(False, True)].section_fuel_gal.adopted()
    )

    manual_project = project.model_copy(deep=True)
    manual_project.sections[1].manual_tas_kt = 140.0
    manual = service.calculate(manual_project, FakeWeatherProvider())
    manual_section = source_cruise(manual)
    assert manual_section.tas_kt.adopted() == 140.0
    assert manual_section.performance_metadata["equipment_adjustments_applied"] is False
    assert manual_section.performance_metadata["nose_fairing_adjustment_ktas"] == 0.0
    assert manual_section.performance_metadata["air_conditioning_adjustment_ktas"] == 0.0


def test_descent_leg_is_automatically_split_at_eoc_without_losing_distance(
    airports,
    performance_repository,
    project,
) -> None:
    descent_project = project.model_copy(deep=True)
    descent_project.sections[0].phase = FlightPhase.CRUISE
    descent_project.sections[1].phase = FlightPhase.DESCENT
    descent_project.sections = [
        section.__class__.model_validate(
            section.model_dump()
            | {
                "manual_wind_direction_deg": 90.0,
                "manual_wind_speed_kt": 30.0,
            }
        )
        for section in descent_project.sections
    ]
    descent_project.sections[1].manual_wind_by_phase = {
        FlightPhase.CRUISE: ManualWind(direction_deg_from=270, speed_kt=20)
    }

    outcome = CalculationService(airports, performance_repository).calculate(
        descent_project,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    assert outcome.status == ProjectStatus.READY_FOR_COPY
    assert [section.phase for section in outcome.sections] == [
        FlightPhase.CRUISE,
        FlightPhase.CRUISE,
        FlightPhase.DESCENT,
    ]
    assert outcome.sections[-2].to_name == "EOC"
    assert outcome.sections[-1].from_name == "EOC"
    assert outcome.sections[-2].wind_direction_deg_from.adopted() == pytest.approx(270)
    assert outcome.sections[-2].wind_speed_kt.adopted() == pytest.approx(20)
    assert outcome.sections[-1].wind_direction_deg_from.adopted() == pytest.approx(90)
    assert outcome.sections[-1].wind_speed_kt.adopted() == pytest.approx(30)
    assert outcome.sections[-2].wind_speed_kt.adopted_source == AdoptedSource.MANUAL
    assert outcome.sections[-1].wind_speed_kt.adopted_source == AdoptedSource.MANUAL
    assert sum(
        section.zone_distance_nm.adopted() or 0.0 for section in outcome.sections
    ) == pytest.approx(
        outcome.sections[-1].cumulative_distance_nm.adopted(),
        abs=1e-9,
    )
    descent_sections = [
        section for section in outcome.sections if section.phase == FlightPhase.DESCENT
    ]
    assert sum(
        section.zone_ete_seconds.adopted() or 0.0 for section in descent_sections
    ) == pytest.approx(
        descent_sections[0].performance_metadata["planned_duration_seconds"],
        abs=1e-6,
    )
    assert [point.type.value for point in outcome.derived_points] == ["EOC"]


def test_eoc_starts_from_descent_leg_altitude_before_using_previous_leg(
    airports,
    performance_repository,
    project,
) -> None:
    """Case A: a 5,500 ft descent profile fits without using the 6,500 ft leg."""
    routed = project.model_copy(deep=True)
    departure, _, destination = routed.ordered_nodes()
    departure.manual_distance_nm = 30.0
    descent_turn = RouteNode(
        sequence=1,
        name="WP3",
        latitude_deg=32.45,
        longitude_deg=131.55,
        role=RouteNodeRole.TURN_POINT,
        manual_distance_nm=20.0,
    )
    vrep = RouteNode(
        sequence=2,
        name="VREP",
        latitude_deg=33.0,
        longitude_deg=131.65,
        role=RouteNodeRole.VISUAL_REPORTING_POINT,
        manual_distance_nm=5.0,
    )
    destination.sequence = 3
    sections = [
        NavSection(
            sequence=0,
            from_node_id=departure.id,
            to_node_id=descent_turn.id,
            phase=FlightPhase.CRUISE,
            planned_altitude_ft_msl=6_500,
            manual_wind_direction_deg=360,
            manual_wind_speed_kt=0.0,
            manual_tas_kt=120.0,
        ),
        NavSection(
            sequence=1,
            from_node_id=descent_turn.id,
            to_node_id=vrep.id,
            phase=FlightPhase.DESCENT,
            planned_altitude_ft_msl=5_500,
            manual_wind_direction_deg=360,
            manual_wind_speed_kt=0.0,
            manual_tas_kt=120.0,
        ),
        NavSection(
            sequence=2,
            from_node_id=vrep.id,
            to_node_id=destination.id,
            phase=FlightPhase.VISUAL_ARRIVAL,
            planned_altitude_ft_msl=2_800,
            manual_wind_direction_deg=360,
            manual_wind_speed_kt=0.0,
            manual_tas_kt=120.0,
        ),
    ]
    routed = routed.model_copy(
        update={
            "route_nodes": [departure, descent_turn, vrep, destination],
            "sections": sections,
        }
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    eoc = next(point for point in outcome.derived_points if point.type.value == "EOC")
    # (5,500 - 2,800) / 500 fpm + one 60-second deceleration at 120 kt.
    assert eoc.along_route_distance_nm == pytest.approx(37.2)
    descent = next(section for section in outcome.sections if section.phase == FlightPhase.DESCENT)
    assert descent.performance_metadata["cruise_altitude_ft_msl"] == 5_500
    assert descent.performance_metadata["eoc_source_section_id"] == str(sections[1].id)


def _eoc_backtracking_project(
    project,
    *,
    leg_distances_nm: tuple[float, float, float],
    altitudes_ft_msl: tuple[float, float, float],
    manual_courses_deg: tuple[float, float, float],
    descent_winds: dict[int, ManualWind] | None = None,
):
    """Build three physical legs ending at a descent basis leg and then VREP."""
    routed = project.model_copy(deep=True)
    departure, _, destination = routed.ordered_nodes()
    departure.manual_distance_nm = leg_distances_nm[0]
    departure.manual_true_course_deg = manual_courses_deg[0]
    first_turn = RouteNode(
        sequence=1,
        name="WP1",
        latitude_deg=32.15,
        longitude_deg=131.50,
        role=RouteNodeRole.TURN_POINT,
        manual_distance_nm=leg_distances_nm[1],
        manual_true_course_deg=manual_courses_deg[1],
    )
    descent_turn = RouteNode(
        sequence=2,
        name="WP2",
        latitude_deg=32.45,
        longitude_deg=131.55,
        role=RouteNodeRole.TURN_POINT,
        manual_distance_nm=leg_distances_nm[2],
        manual_true_course_deg=manual_courses_deg[2],
    )
    vrep = RouteNode(
        sequence=3,
        name="VREP",
        latitude_deg=33.0,
        longitude_deg=131.65,
        role=RouteNodeRole.VISUAL_REPORTING_POINT,
        manual_distance_nm=5.0,
    )
    destination.sequence = 4
    nodes = [departure, first_turn, descent_turn, vrep, destination]
    phases = (FlightPhase.CRUISE, FlightPhase.CRUISE, FlightPhase.DESCENT)
    sections = [
        NavSection(
            sequence=index,
            from_node_id=nodes[index].id,
            to_node_id=nodes[index + 1].id,
            phase=phase,
            planned_altitude_ft_msl=altitudes_ft_msl[index],
            manual_wind_direction_deg=360,
            manual_wind_speed_kt=0.0,
            manual_tas_kt=120.0,
            manual_wind_by_phase=(
                {}
                if descent_winds is None or index not in descent_winds
                else {FlightPhase.DESCENT: descent_winds[index]}
            ),
        )
        for index, phase in enumerate(phases)
    ]
    sections.append(
        NavSection(
            sequence=3,
            from_node_id=vrep.id,
            to_node_id=destination.id,
            phase=FlightPhase.VISUAL_ARRIVAL,
            planned_altitude_ft_msl=2_800,
            manual_wind_direction_deg=360,
            manual_wind_speed_kt=0.0,
            manual_tas_kt=120.0,
        )
    )
    return (
        routed.model_copy(update={"route_nodes": nodes, "sections": sections}),
        sections,
    )


def _section_for_source(outcome, section_id, phase: FlightPhase):
    return next(
        result
        for result in outcome.sections
        if result.phase == phase
        and result.performance_metadata["phase_segment"]["source_section_id"]
        == str(section_id)
    )


def test_selectable_descent_rate_recalculates_eoc_time_profile_and_fuel(
    airports,
    performance_repository,
    project,
) -> None:
    routed, _ = _eoc_backtracking_project(
        project,
        leg_distances_nm=(20.0, 30.0, 20.0),
        altitudes_ft_msl=(7_500, 6_500, 5_500),
        manual_courses_deg=(0, 90, 180),
    )
    service = CalculationService(airports, performance_repository)

    outcomes = {}
    for rate in (500, 1000):
        configured = routed.model_copy(deep=True)
        configured.descent_rate_fpm = rate
        outcomes[rate] = service.calculate(configured, FakeWeatherProvider())

    standard = outcomes[500]
    fast = outcomes[1000]
    assert not standard.blockers
    assert not fast.blockers

    standard_eoc = next(point for point in standard.derived_points if point.type.value == "EOC")
    fast_eoc = next(point for point in fast.derived_points if point.type.value == "EOC")
    assert standard_eoc.along_route_distance_nm == pytest.approx(57.2)
    assert fast_eoc.along_route_distance_nm == pytest.approx(62.6)

    standard_descent = next(
        section for section in standard.sections if section.phase == FlightPhase.DESCENT
    )
    fast_descent = next(
        section for section in fast.sections if section.phase == FlightPhase.DESCENT
    )
    standard_metadata = standard_descent.performance_metadata
    fast_metadata = fast_descent.performance_metadata
    assert standard_metadata["descent_rate_fpm"] == 500.0
    assert fast_metadata["descent_rate_fpm"] == 1000.0
    assert standard_metadata["vertical_descent_duration_seconds"] == pytest.approx(324.0)
    assert fast_metadata["vertical_descent_duration_seconds"] == pytest.approx(162.0)
    assert standard_metadata["planned_duration_seconds"] == pytest.approx(384.0)
    assert fast_metadata["planned_duration_seconds"] == pytest.approx(222.0)
    assert standard_metadata["deceleration_duration_seconds"] == 60.0
    assert fast_metadata["deceleration_duration_seconds"] == 60.0

    standard_profile = descent_profile_from_metadata(standard_metadata)
    fast_profile = descent_profile_from_metadata(fast_metadata)
    assert standard_profile is not None
    assert fast_profile is not None
    assert standard_profile.altitude_at_elapsed(162.0) == pytest.approx(4_150.0)
    assert fast_profile.altitude_at_elapsed(162.0) == pytest.approx(2_800.0)

    for outcome, planned_seconds in ((standard, 384.0), (fast, 222.0)):
        descent_sections = [
            section for section in outcome.sections if section.phase == FlightPhase.DESCENT
        ]
        assert all(
            section.performance_metadata["fuel_flow_gph"] == 12.0
            for section in descent_sections
        )
        assert sum(
            section.section_fuel_gal.adopted() or 0.0 for section in descent_sections
        ) == pytest.approx(12.0 * planned_seconds / 3600.0)


def test_eoc_backtracks_with_common_descent_wind_and_leg_specific_ground_speeds(
    airports,
    performance_repository,
    project,
) -> None:
    """Case B: use one basis-Leg wind while solving each physical Leg's GS."""
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 30.0, 10.0),
        altitudes_ft_msl=(7_000, 6_500, 5_500),
        manual_courses_deg=(0, 90, 180),
        descent_winds={
            1: ManualWind(direction_deg_from=270, speed_kt=20),
            2: ManualWind(direction_deg_from=180, speed_kt=20),
        },
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    eoc = next(point for point in outcome.derived_points if point.type.value == "EOC")
    # 6,500 -> 2,800 ft at 500 fpm plus one minute requires 504 seconds.
    # The basis leg contributes 360 s, then 144 s with the same 180/20 wind
    # solved against the preceding Leg's different course.
    preceding_ground_speed = (120.0**2 - 20.0**2) ** 0.5
    assert eoc.along_route_distance_nm == pytest.approx(
        32.0 - preceding_ground_speed * 144.0 / 3600.0
    )
    descent = _section_for_source(outcome, sections[1].id, FlightPhase.DESCENT)
    assert descent.wind_direction_deg_from.adopted() == pytest.approx(180.0)
    assert descent.wind_speed_kt.adopted() == pytest.approx(20.0)
    assert descent.wind_speed_kt.adopted_source == AdoptedSource.MANUAL
    assert descent.ground_speed_kt.adopted() == pytest.approx(preceding_ground_speed)
    descent_basis = _section_for_source(outcome, sections[2].id, FlightPhase.DESCENT)
    assert descent_basis.wind_direction_deg_from.adopted() == pytest.approx(180.0)
    assert descent_basis.wind_speed_kt.adopted() == pytest.approx(20.0)
    assert descent_basis.ground_speed_kt.adopted() == pytest.approx(100.0)
    metadata = descent.performance_metadata
    assert metadata["eoc_source_section_id"] == str(sections[1].id)
    assert metadata["vertical_descent_duration_seconds"] == pytest.approx(444.0)
    assert metadata["planned_duration_seconds"] == pytest.approx(504.0)
    assert [
        (
            transition["start_altitude_ft_msl"],
            transition["target_altitude_ft_msl"],
            transition["vertical_duration_seconds"],
        )
        for transition in metadata["constraint_transitions"]
    ] == [(6_500.0, 2_800.0, 444.0)]
    assert metadata["descent_path_section_ids"] == [
        str(sections[1].id),
        str(sections[2].id),
    ]
    assert [detail["ground_speed_kt"] for detail in metadata["ground_speeds"]] == [
        pytest.approx(preceding_ground_speed),
        pytest.approx(100.0),
    ]
    assert metadata["descent_wind_source_section_id"] == str(sections[2].id)
    assert {
        detail["wind_source_section_id"] for detail in metadata["ground_speeds"]
    } == {str(sections[2].id)}
    profile = descent_profile_from_metadata(metadata)
    assert profile is not None
    assert profile.altitude_at_elapsed(120.0) == pytest.approx(5_500.0)
    assert profile.altitude_at_elapsed(444.0) == pytest.approx(2_800.0)
    assert descent.zone_ete_seconds.adopted() == pytest.approx(144.0)
    assert descent.section_fuel_gal.adopted() == pytest.approx(0.48)
    assert descent_basis.zone_ete_seconds.adopted() == pytest.approx(360.0)
    assert descent_basis.section_fuel_gal.adopted() == pytest.approx(1.2)
    wp2_row = next(
        row
        for row in outcome.display_rows
        if row.to_name == "WP2" and row.row_type == "CALCULATION_ZONE"
    )
    assert wp2_row.pa.text == "(5300)"


def test_eoc_wind_dependent_cells_start_a_new_display_context(
    airports,
    performance_repository,
    project,
) -> None:
    """EOC exposes its first DESCENT zone even when a cell value is unchanged."""
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 30.0, 10.0),
        altitudes_ft_msl=(7_000, 6_500, 5_500),
        manual_courses_deg=(0, 90, 180),
        descent_winds={
            1: ManualWind(direction_deg_from=180, speed_kt=20),
            2: ManualWind(direction_deg_from=270, speed_kt=20),
        },
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    cruise = _section_for_source(outcome, sections[1].id, FlightPhase.CRUISE)
    eoc_descent = _section_for_source(outcome, sections[1].id, FlightPhase.DESCENT)
    next_descent = _section_for_source(outcome, sections[2].id, FlightPhase.DESCENT)
    assert eoc_descent.from_name == "EOC"
    assert cruise.wind_speed_kt.adopted() == 0.0
    assert eoc_descent.wind_direction_deg_from.adopted() == 270.0
    assert eoc_descent.wind_speed_kt.adopted() == 20.0
    assert eoc_descent.wca_deg.adopted() == pytest.approx(cruise.wca_deg.adopted())
    assert eoc_descent.magnetic_heading_deg.adopted() == pytest.approx(
        cruise.magnetic_heading_deg.adopted()
    )
    assert eoc_descent.ground_speed_kt.adopted() != cruise.ground_speed_kt.adopted()
    assert next_descent.wca_deg.adopted() != eoc_descent.wca_deg.adopted()
    assert next_descent.magnetic_heading_deg.adopted() != eoc_descent.magnetic_heading_deg.adopted()
    assert next_descent.ground_speed_kt.adopted() != eoc_descent.ground_speed_kt.adopted()

    eoc_row = next(
        row
        for row in outcome.display_rows
        if row.row_type == "CALCULATION_ZONE"
        and row.source_result_sequence == eoc_descent.sequence
    )
    assert eoc_row.wind.state == DisplayCellState.DISPLAY_VALUE
    assert eoc_row.wind.text == "270/20"
    assert eoc_row.section_id == sections[1].id
    assert eoc_row.wind_source_section_id == sections[2].id
    for name in ("wca", "mh", "gs"):
        assert getattr(eoc_row, name).state == DisplayCellState.DISPLAY_VALUE
        assert getattr(eoc_row, name).text is not None

    next_summary = next(
        row
        for row in outcome.display_rows
        if row.row_type == "PHYSICAL_LEG_SUMMARY" and row.section_id == sections[2].id
    )
    assert next_summary.wind_source_section_id == sections[2].id
    for name in ("wca", "mh", "gs"):
        assert getattr(next_summary, name).state == DisplayCellState.DISPLAY_VALUE
        assert getattr(next_summary, name).effective_value != getattr(eoc_row, name).effective_value

    next_row = next(
        row
        for row in outcome.display_rows
        if row.row_type == "CALCULATION_ZONE"
        and row.source_result_sequence == next_descent.sequence
    )
    assert next_row.wind.state == DisplayCellState.INHERIT
    assert next_row.wca.state == DisplayCellState.INHERIT
    assert next_row.mh.state == DisplayCellState.INHERIT
    assert next_row.gs.state == DisplayCellState.INHERIT


def test_eoc_common_wind_does_not_require_preceding_leg_descent_wind(
    airports,
    performance_repository,
    project,
) -> None:
    """Only the basis Leg supplies wind; crossed Legs still supply DESCENT temperature."""
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 30.0, 10.0),
        altitudes_ft_msl=(7_000, 6_500, 5_500),
        manual_courses_deg=(0, 90, 180),
        descent_winds={
            2: ManualWind(direction_deg_from=180, speed_kt=20),
        },
    )
    preceding_descent_request_id = f"section:{sections[1].id}:descent"

    def preceding_descent_temperature_only(request):
        values = (
            {"temperature_c": 15.0}
            if request.request_id == preceding_descent_request_id
            else {
                "temperature_c": 15.0,
                "wind_direction_deg_from": 360.0,
                "wind_speed_kt": 0.0,
            }
        )
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values=values,
        )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(result_factory=preceding_descent_temperature_only),
    )

    assert not any(issue.code == "WIND_UNAVAILABLE" for issue in outcome.issues)
    assert not outcome.blockers
    eoc_descent = _section_for_source(outcome, sections[1].id, FlightPhase.DESCENT)
    assert eoc_descent.wind_direction_deg_from.adopted() == pytest.approx(180.0)
    assert eoc_descent.wind_speed_kt.adopted() == pytest.approx(20.0)
    assert eoc_descent.temperature_c.adopted() == pytest.approx(15.0)


def test_snapped_eoc_wind_dependent_cells_do_not_inherit_parent_values(
    airports,
    performance_repository,
    project,
) -> None:
    """An EOC snapped to a turn still starts a wind-dependent display context."""
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 6.0, 16.4),
        altitudes_ft_msl=(7_000, 6_500, 6_500),
        manual_courses_deg=(0, 90, 180),
        descent_winds={
            1: ManualWind(direction_deg_from=270, speed_kt=20),
            2: ManualWind(direction_deg_from=270, speed_kt=20),
        },
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    eoc_descent = _section_for_source(outcome, sections[2].id, FlightPhase.DESCENT)
    eoc_marker = next(
        section for section in outcome.sections if section.to_name == "WP2 / EOC"
    )
    assert eoc_marker.phase == FlightPhase.CRUISE
    assert eoc_descent.from_name == "WP2 / EOC"
    eoc_row = next(
        row
        for row in outcome.display_rows
        if row.row_type == "CALCULATION_ZONE"
        and row.source_result_sequence == eoc_descent.sequence
    )
    for name in ("wind", "wca", "mh", "gs"):
        assert getattr(eoc_row, name).state == DisplayCellState.DISPLAY_VALUE
        assert getattr(eoc_row, name).text is not None
    # Non-wind cells retain ordinary parent/preceding-value inheritance.
    for name in ("toat", "cas", "tas", "tc", "variation", "mc"):
        assert getattr(eoc_row, name).state == DisplayCellState.INHERIT


def test_eoc_auto_tas_uses_each_crossed_leg_descent_environment(
    airports,
    performance_repository,
    project,
) -> None:
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 30.0, 10.0),
        altitudes_ft_msl=(7_000, 6_500, 6_500),
        manual_courses_deg=(0, 90, 180),
        descent_winds={
            1: ManualWind(direction_deg_from=270, speed_kt=20),
            2: ManualWind(direction_deg_from=180, speed_kt=20),
        },
    )
    # No manual DESCENT TAS: each crossed Leg must derive TAS from the common
    # descent CAS using its own DESCENT-phase PA and OAT.
    sections[1].manual_temperature_c_by_phase = {FlightPhase.DESCENT: -20.0}
    sections[2].manual_tas_kt = None
    sections[2].manual_temperature_c_by_phase = {FlightPhase.DESCENT: 20.0}

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    descent_sections = [
        _section_for_source(outcome, section.id, FlightPhase.DESCENT)
        for section in sections[1:3]
    ]
    metadata = descent_sections[0].performance_metadata
    details = metadata["ground_speeds"]
    assert len(details) == 2
    assert details[0]["tas_kt"] != pytest.approx(details[1]["tas_kt"])
    for result, detail in zip(descent_sections, details, strict=True):
        assert result.tas_kt.adopted() == pytest.approx(detail["tas_kt"])
        assert result.ground_speed_kt.adopted() == pytest.approx(
            detail["ground_speed_kt"]
        )
        distance = result.zone_distance_nm.adopted()
        ground_speed = result.ground_speed_kt.adopted()
        assert distance is not None
        assert ground_speed is not None
        assert result.zone_ete_seconds.adopted() == pytest.approx(
            distance / ground_speed * 3600.0
        )


def test_eoc_accepts_equal_vrep_altitude_as_one_minute_level_deceleration(
    airports,
    performance_repository,
    project,
) -> None:
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 30.0, 3.0),
        altitudes_ft_msl=(7_000, 2_700, 2_800),
        manual_courses_deg=(0, 90, 180),
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    eoc = next(point for point in outcome.derived_points if point.type.value == "EOC")
    assert eoc.along_route_distance_nm == pytest.approx(33.0)
    descent = _section_for_source(outcome, sections[2].id, FlightPhase.DESCENT)
    metadata = descent.performance_metadata
    assert metadata["eoc_source_section_id"] == str(sections[2].id)
    assert metadata["vertical_descent_duration_seconds"] == pytest.approx(0.0)
    assert metadata["deceleration_duration_seconds"] == pytest.approx(60.0)
    assert metadata["planned_duration_seconds"] == pytest.approx(60.0)
    assert descent.zone_ete_seconds.adopted() == pytest.approx(60.0)
    assert descent.section_fuel_gal.adopted() == pytest.approx(0.2)


def test_eoc_backtracks_when_descent_basis_altitude_is_below_vrep(
    airports,
    performance_repository,
    project,
) -> None:
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 30.0, 10.0),
        altitudes_ft_msl=(7_000, 6_500, 2_600),
        manual_courses_deg=(0, 90, 180),
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    descent = _section_for_source(outcome, sections[1].id, FlightPhase.DESCENT)
    metadata = descent.performance_metadata
    assert metadata["eoc_source_section_id"] == str(sections[1].id)
    assert metadata["cruise_altitude_ft_msl"] == pytest.approx(6_500.0)
    assert metadata["target_altitude_ft_msl"] == pytest.approx(2_800.0)
    assert metadata["planned_duration_seconds"] == pytest.approx(504.0)


@pytest.mark.parametrize(
    (
        "descent_distance_nm",
        "expected_eoc_distance_nm",
        "expected_eoc_label",
        "expected_zone_seconds",
        "expected_eoc_source_index",
    ),
    [
        (16.4, 8.0, "WP2 / EOC", 492.0, 2),
        (16.3, 7.5, "EOC", 504.0, 1),
    ],
)
def test_eoc_integration_snap_threshold_preserves_profile_metadata(
    airports,
    performance_repository,
    project,
    descent_distance_nm,
    expected_eoc_distance_nm,
    expected_eoc_label,
    expected_zone_seconds,
    expected_eoc_source_index,
) -> None:
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 6.0, descent_distance_nm),
        altitudes_ft_msl=(7_000, 6_500, 6_500),
        manual_courses_deg=(0, 90, 180),
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    eoc = next(point for point in outcome.derived_points if point.type.value == "EOC")
    assert eoc.along_route_distance_nm == pytest.approx(expected_eoc_distance_nm)
    assert any(section.to_name == expected_eoc_label for section in outcome.sections)
    descent = next(section for section in outcome.sections if section.phase == FlightPhase.DESCENT)
    metadata = descent.performance_metadata
    expected_source_id = str(sections[expected_eoc_source_index].id)
    assert eoc.section_id == sections[expected_eoc_source_index].id
    assert metadata["eoc_source_section_id"] == expected_source_id
    assert metadata["constraint_section_ids"][0] == expected_source_id
    assert metadata["descent_path_section_ids"][0] == expected_source_id
    if expected_eoc_source_index == 2:
        assert metadata["profile_start_section_id"] == str(sections[1].id)
        assert metadata["constraint_transitions"][0]["section_id"] == str(
            sections[1].id
        )
        assert metadata["constraint_section_ids"] == [expected_source_id]
        assert metadata["descent_path_section_ids"] == [expected_source_id]
        assert [detail["section_id"] for detail in metadata["ground_speeds"]] == [
            expected_source_id
        ]
        assert metadata["eoc_boundary_normalization"] == (
            "SNAPPED_TO_FOLLOWING_LEG_START"
        )
    assert metadata["vertical_descent_duration_seconds"] == pytest.approx(444.0)
    assert metadata["deceleration_duration_seconds"] == pytest.approx(60.0)
    assert metadata["planned_duration_seconds"] == pytest.approx(504.0)
    descent_sections = [
        section for section in outcome.sections if section.phase == FlightPhase.DESCENT
    ]
    assert all(
        section.performance_metadata["fuel_flow_gph"] == 12.0
        for section in descent_sections
    )
    zone_seconds = sum(
        section.zone_ete_seconds.adopted() or 0.0 for section in descent_sections
    )
    assert zone_seconds == pytest.approx(expected_zone_seconds)
    assert sum(
        section.section_fuel_gal.adopted() or 0.0 for section in descent_sections
    ) == pytest.approx(12.0 * zone_seconds / 3600.0)
    assert descent_sections[-1].cumulative_ete_seconds.adopted() is not None


def test_eoc_split_positions_keep_parent_and_ttl_display_distance_stable(
    airports,
    performance_repository,
    project,
) -> None:
    service = CalculationService(airports, performance_repository)
    variants = {
        "within_leg": (7_000, 6_500, 5_000),
        "boundary": (7_000, 6_500, 6_500),
        "previous_leg": (7_000, 7_000, 7_000),
    }
    outcomes: dict[str, CalculationOutcome] = {}

    for name, altitudes in variants.items():
        routed, _ = _eoc_backtracking_project(
            project,
            leg_distances_nm=(2.0, 6.0, 16.4),
            altitudes_ft_msl=altitudes,
            manual_courses_deg=(0, 90, 180),
        )
        outcome = service.calculate(routed, FakeWeatherProvider())
        assert not outcome.blockers
        _assert_display_distance_invariants(outcome)
        outcomes[name] = outcome

    within_leg_eoc = next(
        point for point in outcomes["within_leg"].derived_points if point.type.value == "EOC"
    )
    boundary_eoc = next(
        point for point in outcomes["boundary"].derived_points if point.type.value == "EOC"
    )
    previous_leg_eoc = next(
        point for point in outcomes["previous_leg"].derived_points if point.type.value == "EOC"
    )
    assert 8.0 < within_leg_eoc.along_route_distance_nm < 24.4
    assert boundary_eoc.along_route_distance_nm == pytest.approx(8.0)
    assert previous_leg_eoc.along_route_distance_nm < 8.0
    assert any(section.to_name == "WP2 / EOC" for section in outcomes["boundary"].sections)
    assert not any(section.to_name == "WP2 / EOC" for section in outcomes["previous_leg"].sections)

    baseline = outcomes["within_leg"]
    baseline_exact_total = baseline.sections[-1].cumulative_distance_nm.adopted()
    baseline_parent_totals = _displayed_parent_distance_totals(baseline)
    baseline_ttl = _displayed_ttl_distance(baseline)
    for outcome in outcomes.values():
        assert outcome.sections[-1].cumulative_distance_nm.adopted() == pytest.approx(
            baseline_exact_total
        )
        assert _displayed_parent_distance_totals(outcome) == baseline_parent_totals
        assert _displayed_ttl_distance(outcome) == pytest.approx(baseline_ttl)


@pytest.mark.parametrize("descent_distance_nm", [2.2, 2.5])
def test_eoc_does_not_snap_to_the_vrep_descent_end_boundary(
    airports,
    performance_repository,
    project,
    descent_distance_nm,
) -> None:
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 30.0, descent_distance_nm),
        altitudes_ft_msl=(7_000, 6_500, 2_800),
        manual_courses_deg=(0, 90, 180),
    )
    sections[2].manual_tas_kt = 12.0

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    eoc = next(point for point in outcome.derived_points if point.type.value == "EOC")
    # Equal VREP altitude still requires exactly the one 60 s deceleration.
    # At 12 kt that is 0.2 NM, so the first case is 0.2 NM before VREP. VREP
    # is not an EOC snap candidate; exactly 0.5 NM remains raw as well.
    assert eoc.along_route_distance_nm == pytest.approx(
        32.0 + descent_distance_nm - 0.2
    )
    assert eoc.section_id == sections[2].id
    descent = _section_for_source(outcome, sections[2].id, FlightPhase.DESCENT)
    assert descent.performance_metadata["planned_duration_seconds"] == pytest.approx(
        60.0
    )
    assert descent.performance_metadata["vertical_descent_duration_seconds"] == pytest.approx(
        0.0
    )
    assert descent.performance_metadata["deceleration_duration_seconds"] == pytest.approx(
        60.0
    )


def test_eoc_backtracks_through_multiple_legs_with_one_global_deceleration_minute(
    airports,
    performance_repository,
    project,
) -> None:
    """Cases C and E: recursive backtracking retains one +1 minute."""
    routed, sections = _eoc_backtracking_project(
        project,
        leg_distances_nm=(30.0, 4.666666666666667, 8.0),
        altitudes_ft_msl=(7_500, 6_500, 5_500),
        manual_courses_deg=(0, 90, 180),
        descent_winds={
            1: ManualWind(direction_deg_from=270, speed_kt=20),
            2: ManualWind(direction_deg_from=180, speed_kt=20),
        },
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    eoc = next(point for point in outcome.derived_points if point.type.value == "EOC")
    # The 5,500 and 6,500 candidates are short.  A single 7,500 -> 2,800 ft
    # profile needs 624 seconds.  The common 180/20 wind produces a different
    # GS on each course and places EOC in the first Leg.
    middle_ground_speed = (120.0**2 - 20.0**2) ** 0.5
    first_leg_seconds = 624.0 - 8.0 / 100.0 * 3600.0 - (
        4.666666666666667 / middle_ground_speed * 3600.0
    )
    assert eoc.along_route_distance_nm == pytest.approx(
        30.0 - 140.0 * first_leg_seconds / 3600.0
    )
    descent = _section_for_source(outcome, sections[0].id, FlightPhase.DESCENT)
    metadata = descent.performance_metadata
    assert metadata["eoc_source_section_id"] == str(sections[0].id)
    assert metadata["vertical_descent_duration_seconds"] == pytest.approx(564.0)
    assert metadata["deceleration_duration_seconds"] == pytest.approx(60.0)
    assert metadata["planned_duration_seconds"] == pytest.approx(624.0)
    assert metadata["constraint_section_ids"] == [
        str(sections[0].id),
        str(sections[1].id),
        str(sections[2].id),
    ]
    assert [
        (
            transition["start_altitude_ft_msl"],
            transition["target_altitude_ft_msl"],
            transition["vertical_duration_seconds"],
        )
        for transition in metadata["constraint_transitions"]
    ] == [(7_500.0, 2_800.0, 564.0)]
    assert [detail["ground_speed_kt"] for detail in metadata["ground_speeds"]] == [
        pytest.approx(140.0),
        pytest.approx(middle_ground_speed),
        pytest.approx(100.0),
    ]
    profile = descent_profile_from_metadata(metadata)
    assert profile is not None
    assert profile.altitude_at_elapsed(120.0) == pytest.approx(6_500.0)
    assert profile.altitude_at_elapsed(240.0) == pytest.approx(5_500.0)
    assert profile.altitude_at_elapsed(564.0) == pytest.approx(2_800.0)
    descent_sections = [
        _section_for_source(outcome, section.id, FlightPhase.DESCENT)
        for section in sections[:3]
    ]
    middle_leg_seconds = 4.666666666666667 / middle_ground_speed * 3600.0
    assert [section.zone_ete_seconds.adopted() for section in descent_sections] == [
        pytest.approx(first_leg_seconds),
        pytest.approx(middle_leg_seconds),
        pytest.approx(288.0),
    ]
    assert all(
        section.performance_metadata["fuel_flow_gph"] == 12.0
        for section in descent_sections
    )
    assert sum(
        section.section_fuel_gal.adopted() or 0.0 for section in descent_sections
    ) == pytest.approx(
        12.0 * 624.0 / 3600.0
    )
    assert sum(
        section.zone_distance_nm.adopted() or 0.0 for section in outcome.sections
    ) == pytest.approx(
        outcome.sections[-1].cumulative_distance_nm.adopted()
    )


def test_eoc_backtracking_blocks_when_the_profile_precedes_the_route_start(
    airports,
    performance_repository,
    project,
) -> None:
    """Case D: do not create an EOC if every preceding physical leg is too short."""
    routed, _ = _eoc_backtracking_project(
        project,
        leg_distances_nm=(3.0, 4.0, 10.0),
        altitudes_ft_msl=(7_500, 6_500, 5_500),
        manual_courses_deg=(0, 90, 180),
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    blocker = next(
        issue
        for issue in outcome.blockers
        if issue.code == "DESCENT_ALTITUDE_CONSTRAINT_INFEASIBLE"
    )
    assert blocker.message == (
        "経路始点から降下しても、VREPまでに500 fpmの降下と減速1分を完了できません。"
        "計画高度、経路、VREP高度を見直してください。"
    )
    assert blocker.metadata["descent_path_section_ids"]
    assert blocker.metadata["constraint_section_ids"] == blocker.metadata[
        "descent_path_section_ids"
    ]
    assert len(blocker.metadata["constraint_transitions"]) == 1
    assert blocker.metadata["ground_speeds"]
    assert blocker.metadata["required_seconds"] > blocker.metadata["available_seconds"]
    assert not any(point.type.value == "EOC" for point in outcome.derived_points)

    faster = routed.model_copy(deep=True)
    faster.descent_rate_fpm = 1000
    faster_outcome = CalculationService(airports, performance_repository).calculate(
        faster,
        FakeWeatherProvider(),
    )
    assert not any(
        issue.code == "DESCENT_ALTITUDE_CONSTRAINT_INFEASIBLE"
        for issue in faster_outcome.blockers
    )
    assert any(point.type.value == "EOC" for point in faster_outcome.derived_points)


def test_eoc_infeasible_message_uses_selected_descent_rate(
    airports,
    performance_repository,
    project,
) -> None:
    routed, _ = _eoc_backtracking_project(
        project,
        leg_distances_nm=(1.0, 1.0, 1.0),
        altitudes_ft_msl=(7_500, 6_500, 5_500),
        manual_courses_deg=(0, 90, 180),
    )
    routed.descent_rate_fpm = 1000

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    blocker = next(
        issue
        for issue in outcome.blockers
        if issue.code == "DESCENT_ALTITUDE_CONSTRAINT_INFEASIBLE"
    )
    assert blocker.message == (
        "経路始点から降下しても、VREPまでに1000 fpmの降下と減速1分を完了できません。"
        "計画高度、経路、VREP高度を見直してください。"
    )
    assert blocker.metadata["descent_rate_fpm"] == 1000


def test_eoc_backtracking_blocks_a_non_monotonic_turn_altitude_transition(
    airports,
    performance_repository,
    project,
) -> None:
    routed, _ = _eoc_backtracking_project(
        project,
        leg_distances_nm=(2.0, 30.0, 10.0),
        altitudes_ft_msl=(7_000, 5_400, 5_500),
        manual_courses_deg=(0, 90, 180),
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    blocker = next(
        issue
        for issue in outcome.blockers
        if issue.code == "DESCENT_ALTITUDE_CONSTRAINT_INFEASIBLE"
    )
    assert blocker.metadata["constraint_start_altitude_ft_msl"] == 5_400.0
    assert blocker.metadata["constraint_target_altitude_ft_msl"] == 5_500.0
    assert blocker.message == (
        "「WP1 → WP2」は5,400 ft、次の区間は5,500 ftです。"
        "前の区間のほうが低いため、連続降下を計算できません。"
        "前の区間が同じか高くなるように計画高度を見直してください。"
    )
    assert blocker.metadata["constraint_section_ids"] == blocker.metadata[
        "descent_path_section_ids"
    ]
    assert len(blocker.metadata["constraint_transitions"]) == 1
    assert len(blocker.metadata["ground_speeds"]) == 2
    assert not any(point.type.value == "EOC" for point in outcome.derived_points)


@pytest.mark.parametrize(
    (
        "descent_distance_nm",
        "cruise_altitude_ft",
        "expected_eoc_distance_nm",
        "expected_vertical_seconds",
        "expected_eoc_section_index",
    ),
    [
        (7.0, 5_000.0, 31.0, 120.0, 1),
        (6.0, 5_000.0, 30.0, 120.0, 1),
        (4.0, 5_000.0, 18.0, 420.0, 0),
        (6.0, 12_000.0, 30.0, 120.0, 1),
    ],
)
def test_eoc_uses_current_leg_interior_or_exact_turn_transition(
    airports,
    performance_repository,
    project,
    descent_distance_nm,
    cruise_altitude_ft,
    expected_eoc_distance_nm,
    expected_vertical_seconds,
    expected_eoc_section_index,
) -> None:
    routed = project.model_copy(deep=True)
    departure, turn, destination = routed.ordered_nodes()
    departure.manual_distance_nm = 30.0
    turn.manual_distance_nm = descent_distance_nm
    destination.sequence = 3
    vrep = RouteNode(
        sequence=2,
        name="VREP",
        latitude_deg=33.3,
        longitude_deg=131.7,
        role=RouteNodeRole.VISUAL_REPORTING_POINT,
        manual_distance_nm=5.0,
    )
    sections = [
        NavSection(
            sequence=0,
            from_node_id=departure.id,
            to_node_id=turn.id,
            phase=FlightPhase.CRUISE,
            planned_altitude_ft_msl=cruise_altitude_ft,
                manual_wind_direction_deg=360,
            manual_wind_speed_kt=0.0,
        ),
        NavSection(
            sequence=1,
            from_node_id=turn.id,
            to_node_id=vrep.id,
            phase=FlightPhase.DESCENT,
            planned_altitude_ft_msl=2_500,
                manual_wind_direction_deg=360,
            manual_wind_speed_kt=0.0,
            manual_tas_kt=120.0,
        ),
        NavSection(
            sequence=2,
            from_node_id=vrep.id,
            to_node_id=destination.id,
            phase=FlightPhase.VISUAL_ARRIVAL,
            planned_altitude_ft_msl=1_500,
        ),
    ]
    routed = routed.model_copy(
        update={
            "route_nodes": [departure, turn, vrep, destination],
            "sections": sections,
        }
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        routed,
        FakeWeatherProvider(),
    )

    if expected_eoc_distance_nm is None:
        assert "DESCENT_ALTITUDE_CONSTRAINT_INFEASIBLE" in {
            issue.code for issue in outcome.blockers
        }
        assert not any(point.type.value == "EOC" for point in outcome.derived_points)
        return
    assert not outcome.blockers
    eoc = next(point for point in outcome.derived_points if point.type.value == "EOC")
    assert eoc.along_route_distance_nm == pytest.approx(expected_eoc_distance_nm)
    descent = next(section for section in outcome.sections if section.phase == FlightPhase.DESCENT)
    assert expected_vertical_seconds is not None
    assert expected_eoc_section_index is not None
    expected_start_altitude = sections[expected_eoc_section_index].planned_altitude_ft_msl
    assert descent.performance_metadata["cruise_altitude_ft_msl"] == expected_start_altitude
    assert descent.performance_metadata["target_altitude_ft_msl"] == 1_500
    assert descent.performance_metadata["planned_duration_seconds"] == pytest.approx(
        expected_vertical_seconds + 60.0
    )
    assert descent.performance_metadata["vertical_descent_duration_seconds"] == pytest.approx(
        expected_vertical_seconds
    )
    assert descent.performance_metadata["deceleration_duration_seconds"] == 60.0
    assert (
        descent.performance_metadata["phase_profile_rule"]
        == "DESCEND_LEVEL_OFF_DECELERATE_V1"
    )


def test_three_leg_route_calculates_rca_eoc_and_magnetic_course(
    airports,
    performance_repository,
    project,
) -> None:
    departure = RouteNode(
        sequence=0,
        name="RJFM",
        latitude_deg=31.87724387987135,
        longitude_deg=131.4485520078941,
        role=RouteNodeRole.AIRPORT,
    )
    first_turn = RouteNode(
        sequence=1,
        name="米ノ津",
        latitude_deg=32.11545443632519,
        longitude_deg=130.3371470683687,
        role=RouteNodeRole.TURN_POINT,
    )
    vrep = RouteNode(
        sequence=2,
        name="大牟田",
        latitude_deg=33.4000000000,
        longitude_deg=131.7000000000,
        role=RouteNodeRole.VISUAL_REPORTING_POINT,
    )
    destination = RouteNode(
        sequence=3,
        name="RJFO",
        latitude_deg=33.479,
        longitude_deg=131.737,
        role=RouteNodeRole.DESTINATION,
    )
    route_nodes = [departure, first_turn, vrep, destination]
    sections = [
        NavSection(
            sequence=0,
            from_node_id=departure.id,
            to_node_id=first_turn.id,
            phase=FlightPhase.CLIMB,
            planned_altitude_ft_msl=5000,
        ),
        NavSection(
            sequence=1,
            from_node_id=first_turn.id,
            to_node_id=vrep.id,
            phase=FlightPhase.DESCENT,
            planned_altitude_ft_msl=5000,
        ),
        NavSection(
            sequence=2,
            from_node_id=vrep.id,
            to_node_id=destination.id,
            phase=FlightPhase.VISUAL_ARRIVAL,
            planned_altitude_ft_msl=2500,
        ),
    ]
    three_leg = project.__class__.model_validate(
        project.model_dump()
        | {
            "route_nodes": [node.model_dump() for node in route_nodes],
            "sections": [section.model_dump() for section in sections],
        }
    )

    outcome = CalculationService(airports, performance_repository).calculate(
        three_leg,
        FakeWeatherProvider(),
    )

    unexpected = {
        "DESCENT_TAS_UNAVAILABLE",
        "CALCULATION_OUTPUT_INCOMPLETE",
        "DERIVED_PHASE_POINT_MISSING",
    }
    assert not unexpected.intersection(issue.code for issue in outcome.blockers)
    assert {point.type.value for point in outcome.derived_points} == {"RCA", "EOC"}
    first_segment = outcome.sections[0]
    true_course = first_segment.true_course_deg.adopted()
    magnetic_course = first_segment.magnetic_course_deg.adopted()
    assert true_course == pytest.approx(284.0, abs=1.0)
    assert first_segment.variation_deg_east.adopted() == 7.0
    assert magnetic_course == pytest.approx((true_course + 7.0) % 360.0)


def test_incomplete_descent_output_and_missing_eoc_cannot_be_ready(
    airports,
    performance_repository,
    project,
) -> None:
    incomplete_project = project.model_copy(deep=True)
    departure, _, destination = incomplete_project.ordered_nodes()
    descent = incomplete_project.sections[0]
    descent.to_node_id = destination.id
    descent.phase = FlightPhase.DESCENT
    incomplete_project.sections = [descent]
    incomplete_project.route_nodes = [departure, destination]

    outcome = CalculationService(airports, performance_repository).calculate(
        incomplete_project,
        FakeWeatherProvider(),
    )

    incomplete = next(
        item for item in outcome.blockers if item.code == "CALCULATION_OUTPUT_INCOMPLETE"
    )
    missing_point = next(
        item for item in outcome.blockers if item.code == "DERIVED_PHASE_POINT_MISSING"
    )
    assert outcome.status == ProjectStatus.MANUAL_INPUT_REQUIRED
    assert {
        "cas_kt",
        "tas_kt",
        "ground_speed_kt",
        "wca_deg",
        "magnetic_heading_deg",
    }.issubset(incomplete.metadata["missing_fields"])
    assert missing_point.metadata == {
        "phase": "DESCENT",
        "required_point": "EOC",
    }


@pytest.mark.parametrize("manual_distance_nm", [None, 5.0])
def test_zero_length_leg_is_returned_as_route_blocker(
    airports,
    performance_repository,
    project,
    manual_distance_nm,
) -> None:
    invalid = project.model_copy(deep=True)
    departure, turn, _ = invalid.ordered_nodes()
    turn.latitude_deg = departure.latitude_deg
    turn.longitude_deg = departure.longitude_deg
    turn.manual_distance_nm = manual_distance_nm

    outcome = CalculationService(airports, performance_repository).calculate(
        invalid,
        FakeWeatherProvider(),
    )

    assert outcome.status == ProjectStatus.ROUTE_INCOMPLETE
    assert not outcome.sections
    assert any(issue.code == "ROUTE_INCOMPLETE" for issue in outcome.blockers)


def test_visual_arrival_calculates_calm_and_displays_destination_forecast(
    airports,
    performance_repository,
    project,
) -> None:
    visual_project = project.model_copy(deep=True)
    visual_project.sections[0] = visual_project.sections[0].__class__.model_validate(
        visual_project.sections[0].model_dump()
        | {
            "phase": FlightPhase.CRUISE,
            "manual_wind_direction_deg": 360,
            "manual_wind_speed_kt": 0.0,
            "manual_wind_by_phase": {
                FlightPhase.DESCENT: ManualWind(direction_deg_from=360, speed_kt=0),
                FlightPhase.CLIMB: ManualWind(direction_deg_from=360, speed_kt=0),
            },
        }
    )
    visual_project.sections[1].phase = FlightPhase.VISUAL_ARRIVAL

    def temperature_without_wind(request):
        values = {"temperature_c": 15.0}
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values=values,
        )

    destination_wind = DestinationWindForecast(
        airport_icao="RJFO",
        valid_time_utc=None,
        availability=Availability.AVAILABLE,
        wind_direction_deg_from=200,
        wind_speed_kt=8,
    )
    outcome = CalculationService(airports, performance_repository).calculate(
        visual_project,
        FakeWeatherProvider(result_factory=temperature_without_wind),
        destination_wind,
    )

    assert not any(issue.code == "WIND_UNAVAILABLE" for issue in outcome.issues)
    assert not outcome.blockers
    visual = outcome.sections[-1]
    assert visual.phase == FlightPhase.VISUAL_ARRIVAL
    assert visual.wind_speed_kt.adopted() == 0.0
    assert visual.wind_direction_deg_from.adopted() is None
    assert visual.wca_deg.adopted() == 0.0
    assert visual.ground_speed_kt.adopted() == visual.tas_kt.adopted()
    final_parent = next(
        row
        for row in reversed(outcome.display_rows)
        if row.row_type == "PHYSICAL_LEG_SUMMARY"
    )
    destination_row = next(
        row for row in outcome.display_rows if row.row_type == "DESTINATION_INFO"
    )
    assert final_parent.wind.text == "CALM"
    assert final_parent.wca.text == "0"
    assert destination_row.wind.text == "200/8"
    assert destination_row.pa.text == "19"
    assert destination_row.ete.state == DisplayCellState.BLANK

    fallback = CalculationService(airports, performance_repository).calculate(
        visual_project,
        FakeWeatherProvider(result_factory=temperature_without_wind),
    )
    fallback_visual = fallback.sections[-1]
    assert fallback_visual.wind_speed_kt.adopted() == 0.0
    assert fallback_visual.wind_direction_deg_from.adopted() is None
    assert fallback_visual.wind_speed_kt.automatic_metadata["wind_adoption"] == "CALM"
    fallback_destination = next(
        row for row in fallback.display_rows if row.row_type == "DESTINATION_INFO"
    )
    assert fallback_destination.wind.state == DisplayCellState.UNAVAILABLE
    assert fallback_destination.wind.text == "未取得"

    other_airport = destination_wind.model_copy(update={"airport_icao": "RJFM"})
    mismatched = CalculationService(airports, performance_repository).calculate(
        visual_project,
        FakeWeatherProvider(result_factory=temperature_without_wind),
        other_airport,
    )
    mismatched_visual = mismatched.sections[-1]
    assert mismatched_visual.wind_speed_kt.adopted() == 0.0
    assert mismatched_visual.wind_direction_deg_from.adopted() is None
    assert mismatched_visual.wind_speed_kt.automatic_metadata["wind_adoption"] == "CALM"
    mismatched_destination = next(
        row for row in mismatched.display_rows if row.row_type == "DESTINATION_INFO"
    )
    assert mismatched_destination.wind.state == DisplayCellState.UNAVAILABLE
    assert mismatched_destination.wind.text == "未取得"


def test_rca_phase_boundary_keeps_parent_and_ttl_display_distance_stable(
    airports,
    performance_repository,
    project,
) -> None:
    service = CalculationService(airports, performance_repository)
    with_rca = _project_with_climb_endpoint_at_25_nm(project)
    without_rca = with_rca.model_copy(deep=True)
    without_rca.sections[0].phase = FlightPhase.CRUISE

    outcomes = {
        "with_rca": service.calculate(with_rca, FakeWeatherProvider()),
        "without_rca": service.calculate(without_rca, FakeWeatherProvider()),
    }

    assert not outcomes["with_rca"].blockers
    assert not outcomes["without_rca"].blockers
    assert any(section.to_name == "RCA" for section in outcomes["with_rca"].sections)
    assert not any(section.to_name == "RCA" for section in outcomes["without_rca"].sections)

    for outcome in outcomes.values():
        _assert_display_distance_invariants(outcome)

    assert outcomes["with_rca"].sections[-1].cumulative_distance_nm.adopted() == pytest.approx(
        outcomes["without_rca"].sections[-1].cumulative_distance_nm.adopted()
    )
    assert _displayed_parent_distance_totals(
        outcomes["with_rca"]
    ) == _displayed_parent_distance_totals(outcomes["without_rca"])
    assert _displayed_ttl_distance(outcomes["with_rca"]) == pytest.approx(
        _displayed_ttl_distance(outcomes["without_rca"])
    )


def test_missing_climb_wind_is_not_misreported_as_rca_outside_route(
    airports,
    performance_repository,
    project,
) -> None:
    def temperature_without_wind(request):
        values = {"temperature_c": 15.0}
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values=values,
        )

    outcome = CalculationService(airports, performance_repository).calculate(
        project,
        FakeWeatherProvider(result_factory=temperature_without_wind),
    )

    assert any(issue.code == "WIND_UNAVAILABLE" for issue in outcome.blockers)
    assert not any(issue.code == "RCA_OUTSIDE_ROUTE" for issue in outcome.issues)


def test_issue_deduplication_preserves_distinct_split_segments(
    airports,
    performance_repository,
    project,
) -> None:
    section_id = project.sections[0].id
    issues = [
        Issue(
            code="CALCULATION_OUTPUT_INCOMPLETE",
            severity=IssueSeverity.BLOCKER,
            message="segment 1",
            section_id=section_id,
            segment_sequence=0,
        ),
        Issue(
            code="CALCULATION_OUTPUT_INCOMPLETE",
            severity=IssueSeverity.BLOCKER,
            message="segment 2",
            section_id=section_id,
            segment_sequence=1,
        ),
    ]

    deduplicated = CalculationService(
        airports,
        performance_repository,
    )._deduplicate_issues(issues)

    assert [issue.segment_sequence for issue in deduplicated] == [0, 1]


def test_manual_source_distance_is_distributed_with_explicit_provenance(
    airports,
    performance_repository,
    project,
) -> None:
    manual = project.model_copy(deep=True)
    manual.ordered_nodes()[0].manual_distance_nm = 20.0
    source_section_id = manual.sections[0].id

    outcome = CalculationService(airports, performance_repository).calculate(
        manual,
        FakeWeatherProvider(),
    )

    assert not outcome.blockers
    distributed = [
        section.zone_distance_nm
        for section in outcome.sections
        if section.section_id == source_section_id
    ]
    assert sum(value.adopted() or 0.0 for value in distributed) == pytest.approx(
        20.0,
        abs=1e-9,
    )
    assert all(
        value.adopted_source == AdoptedSource.AUTOMATIC
        and value.automatic_metadata["source_manual_distance_nm"] == 20.0
        and "DISTRIBUTED_FROM_MANUAL_SOURCE_DISTANCE" in value.warnings
        for value in distributed
    )


@pytest.mark.parametrize(
    "phases",
    [
        (FlightPhase.DESCENT, FlightPhase.CRUISE),
        (FlightPhase.CLIMB, FlightPhase.CLIMB),
        (FlightPhase.VISUAL_ARRIVAL, FlightPhase.VISUAL_ARRIVAL),
    ],
)
def test_invalid_flight_phase_sequence_is_blocking(
    airports,
    performance_repository,
    project,
    phases,
) -> None:
    invalid_project = project.model_copy(deep=True)
    for section, phase in zip(invalid_project.sections, phases, strict=True):
        section.phase = phase

    outcome = CalculationService(airports, performance_repository).calculate(
        invalid_project,
        FakeWeatherProvider(),
    )

    assert any(item.code == "FLIGHT_PHASE_SEQUENCE_INVALID" for item in outcome.blockers)
    assert outcome.status == ProjectStatus.MANUAL_INPUT_REQUIRED


def test_multiple_cruise_sections_are_a_valid_phase_sequence(
    airports,
    performance_repository,
    project,
) -> None:
    cruise_project = project.model_copy(deep=True)
    for section in cruise_project.sections:
        section.phase = FlightPhase.CRUISE

    outcome = CalculationService(airports, performance_repository).calculate(
        cruise_project,
        FakeWeatherProvider(),
    )

    assert not any(item.code == "FLIGHT_PHASE_SEQUENCE_INVALID" for item in outcome.issues)


def test_outcome_adoption_and_project_round_trip(
    airports,
    performance_repository,
    project,
    tmp_path,
) -> None:
    calculation = CalculationService(airports, performance_repository)
    outcome = calculation.calculate(project, FakeWeatherProvider())
    assert project.selected_forecast_run_id is None
    repository = LocalProjectRepository(tmp_path)
    projects = ProjectService(repository)
    adopted = projects.apply_calculation_outcome(project, outcome)
    assert adopted.selected_forecast_run_id == outcome.selected_forecast_run_id
    saved = projects.save(adopted).project
    restored = projects.load(saved.id)
    assert restored.revision == saved.revision
    assert restored.selected_forecast_run_id == outcome.selected_forecast_run_id


def test_weather_warning_does_not_mask_an_unrelated_blocker_status(project) -> None:
    issues = [
        Issue(
            code="WEATHER_OBSERVATION_STALE",
            severity=IssueSeverity.WARNING,
            message="weather warning",
        ),
        Issue(
            code="AIRPORT_DATA_UNAVAILABLE",
            severity=IssueSeverity.BLOCKER,
            message="manual action required",
        ),
    ]

    assert CalculationService._status(project, issues) == ProjectStatus.MANUAL_INPUT_REQUIRED


def test_unverified_performance_is_blocking(airports, project) -> None:
    from autonavlog.performance.repository import PerformanceRepository
    from autonavlog.performance.schemas import PerformanceManifest

    unverified = PerformanceRepository(
        PerformanceManifest(
            aircraft="SR22 G6",
            source_document="pending",
            source_revision="pending",
            verified_against="pending",
        ),
        [],
        [],
    )
    outcome = CalculationService(airports, unverified).calculate(
        project,
        FakeWeatherProvider(),
    )
    assert any(issue.code == "PERFORMANCE_DATA_UNAVAILABLE" for issue in outcome.blockers)
    assert outcome.status == ProjectStatus.MANUAL_INPUT_REQUIRED


def test_unresolved_aircraft_profile_fails_before_weather_access(
    airports,
    performance_repository,
    project,
) -> None:
    incompatible = project.model_copy(
        deep=True,
        update={"aircraft_profile_id": "C172"},
    )
    provider = FakeWeatherProvider()
    outcome = CalculationService(
        airports,
        performance_repository,
    ).calculate(incompatible, provider)

    issue = next(item for item in outcome.blockers if item.code == "PERFORMANCE_DATA_UNAVAILABLE")
    assert issue.metadata["reason"] == "RUNTIME_READINESS"
    assert issue.metadata["aircraft_profile_id"] == "C172"
    assert provider.prepared == {}
    assert provider.query_history == []


def test_performance_hash_mismatch_fails_before_weather_access(
    tmp_path: Path,
    airports,
    project,
) -> None:
    from autonavlog.application.readiness import IssueProducer
    from autonavlog.application.readiness_service import ReadinessService
    from autonavlog.performance.repository import PerformanceRepository

    root = tmp_path / "performance"
    shutil.copytree(Path("data/performance"), root)
    climb = root / "climb_time_fuel_distance.csv"
    climb.write_bytes(climb.read_bytes() + b"\n")
    performance = PerformanceRepository.from_directory_for_application(root)
    provider = FakeWeatherProvider()
    calculation = CalculationService(airports, performance)

    outcome = calculation.calculate(project, provider)

    issue = next(item for item in outcome.blockers if item.code == "PERFORMANCE_DATA_UNVERIFIED")
    assert issue.metadata["reason"] == "HASH_MISMATCH"
    assert provider.prepared == {}
    assert provider.query_history == []
    evaluation = (
        ReadinessService(
            calculation,
            msm_package_version=None,
        )
        .evaluate(project, outcome)
        .evaluation
    )
    effective = [
        item
        for item in evaluation.effective_issues
        if item.ctx.code == "PERFORMANCE_DATA_UNVERIFIED"
    ]
    assert len(effective) == 1
    assert effective[0].producers == {
        IssueProducer.OUTCOME,
        IssueProducer.REFERENCE_DATA,
    }
