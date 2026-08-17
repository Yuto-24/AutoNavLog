from __future__ import annotations

import json
import shutil
from datetime import timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from autonavlog.application.calculation_service import (
    CalculationPolicies,
    CalculationService,
)
from autonavlog.application.project_service import ProjectService
from autonavlog.domain.calculation import Issue
from autonavlog.domain.enums import (
    AdoptedSource,
    Availability,
    DisplayCellState,
    FlightPhase,
    IssueSeverity,
    ProjectStatus,
    RouteNodeRole,
    WeatherRequestKind,
)
from autonavlog.domain.project import ManualWind, NavSection, RouteNode
from autonavlog.domain.snapshot import CalculationSnapshot
from autonavlog.domain.weather import WeatherResult
from autonavlog.nav.airspeed import tas_from_cas
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
from autonavlog.presentation.clearcopy import render_clearcopy_html
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


def test_full_calculation_iteration_and_clearcopy(
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
    assert not outcome.blockers
    assert outcome.status == ProjectStatus.READY_FOR_COPY
    assert outcome.derived_points[0].type.value == "RCA"
    assert outcome.sections[0].wind_speed_kt.adopted() == 0
    assert outcome.sections[0].wind_direction_deg_from.adopted() is None
    assert outcome.sections[-1].remaining_fuel_gal.adopted() is not None
    assert len(provider.query_history) >= 2
    assert all(run_id == "20260728000000" for run_id, _ in provider.query_history)
    html = render_clearcopy_html(aligned_project, outcome)
    assert "PILOT" in html
    assert "ZONE / CUM" in html
    assert "QNH" not in html


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
        timezone.utc
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
        timezone.utc
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


@pytest.mark.parametrize(
    "safe_value",
    [None, 2000.0, 8000.0],
)
def test_legacy_safe_enroute_altitude_is_not_used_for_status_or_timing(
    airports,
    performance_repository,
    project,
    safe_value: float | None,
) -> None:
    service = CalculationService(airports, performance_repository)
    baseline = service.calculate(project, FakeWeatherProvider())
    legacy = project.model_copy(deep=True)
    for section in legacy.sections:
        section.safe_enroute_altitude_ft_msl = safe_value

    outcome = service.calculate(
        legacy,
        FakeWeatherProvider(),
    )

    assert [section.safe_enroute_altitude_ft_msl for section in legacy.sections] == [
        safe_value,
        safe_value,
    ]
    assert all(
        result.safe_enroute_altitude_ft_msl.adopted() is None and result.eto_utc.adopted() is None
        for result in outcome.sections
    )
    assert outcome.status == baseline.status
    assert not outcome.blockers
    assert not any(
        issue.code
        in {
            "SAFE_ENROUTE_ALTITUDE_REQUIRED",
            "PLANNED_ALTITUDE_BELOW_SAFE_ENROUTE",
        }
        for issue in outcome.issues
    )
    assert all(
        section.zone_ete_seconds.adopted() == baseline.sections[index].zone_ete_seconds.adopted()
        for index, section in enumerate(outcome.sections)
    )
    assert all(
        section.section_fuel_gal.adopted() == baseline.sections[index].section_fuel_gal.adopted()
        for index, section in enumerate(outcome.sections)
    )


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
        "PWR_LINEAR_THEN_ISA_LINEAR_THEN_ALTITUDE_LINEAR_NO_EXTRAPOLATION"
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
    assert summary.zone_distance_nm_exact == pytest.approx(
        sum(row.zone_distance_nm_exact or 0.0 for row in details)
    )
    assert summary.zone_ete_seconds_exact == pytest.approx(
        sum(row.zone_ete_seconds_exact or 0.0 for row in details)
    )
    assert all(row.cumulative_distance_nm_exact is None for row in details)
    assert all(row.cumulative_ete_seconds_exact is None for row in details)
    assert summary.counts_toward_totals is False
    assert all(row.counts_toward_totals for row in details)
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
    ac_on = service.calculate(project, FakeWeatherProvider())
    ac_off_project = project.model_copy(deep=True)
    ac_off_project.air_conditioning_enabled = False
    ac_off = service.calculate(ac_off_project, FakeWeatherProvider())

    source_section_id = str(project.sections[1].id)

    def source_cruise(outcome):
        return next(
            section
            for section in outcome.sections
            if section.phase == FlightPhase.CRUISE
            and section.performance_metadata["phase_segment"]["source_section_id"]
            == source_section_id
        )

    on_section = source_cruise(ac_on)
    off_section = source_cruise(ac_off)
    on_metadata = on_section.performance_metadata
    off_metadata = off_section.performance_metadata
    table_ktas = on_metadata["poh_table_ktas"]
    assert on_section.tas_kt.adopted() == pytest.approx(table_ktas - 12.0)
    assert off_section.tas_kt.adopted() == pytest.approx(table_ktas - 10.0)
    assert on_metadata["selected_cell"]["gph"] == off_metadata["selected_cell"]["gph"]
    assert on_metadata["nose_fairing_adjustment_ktas"] == -10.0
    assert on_metadata["air_conditioning_adjustment_ktas"] == -2.0
    assert off_metadata["air_conditioning_adjustment_ktas"] == 0.0
    assert off_section.ground_speed_kt.adopted() > on_section.ground_speed_kt.adopted()
    assert off_section.zone_ete_seconds.adopted() < on_section.zone_ete_seconds.adopted()
    assert off_section.section_fuel_gal.adopted() < on_section.section_fuel_gal.adopted()

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



@pytest.mark.parametrize(
    ("descent_distance_nm", "cruise_altitude_ft", "expected_eoc_distance_nm"),
    [
        (4.0, 5_000.0, 18.0),
        (2.0, 5_000.0, 16.0),
        (2.0, 12_000.0, None),
    ],
)
def test_eoc_uses_cruise_to_vrep_time_and_carries_into_previous_leg(
    airports,
    performance_repository,
    project,
    descent_distance_nm,
    cruise_altitude_ft,
    expected_eoc_distance_nm,
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
            project_id=routed.id,
            sequence=0,
            from_node_id=departure.id,
            to_node_id=turn.id,
            phase=FlightPhase.CRUISE,
            planned_altitude_ft_msl=cruise_altitude_ft,
                manual_wind_direction_deg=360,
            manual_wind_speed_kt=0.0,
        ),
        NavSection(
            project_id=routed.id,
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
            project_id=routed.id,
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
        assert "EOC_BEFORE_SUPPORTED_LEG" in {
            issue.code for issue in outcome.blockers
        }
        assert not any(point.type.value == "EOC" for point in outcome.derived_points)
        return
    assert not outcome.blockers
    eoc = next(point for point in outcome.derived_points if point.type.value == "EOC")
    assert eoc.along_route_distance_nm == pytest.approx(expected_eoc_distance_nm)
    descent = next(section for section in outcome.sections if section.phase == FlightPhase.DESCENT)
    assert descent.performance_metadata["cruise_altitude_ft_msl"] == cruise_altitude_ft
    assert descent.performance_metadata["target_altitude_ft_msl"] == 1_500
    assert descent.performance_metadata["planned_duration_seconds"] == pytest.approx(480.0)
    assert descent.performance_metadata["vertical_descent_duration_seconds"] == pytest.approx(
        420.0
    )
    assert descent.performance_metadata["operational_addition_seconds"] == 60.0


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
            project_id=project.id,
            sequence=0,
            from_node_id=departure.id,
            to_node_id=first_turn.id,
            phase=FlightPhase.CLIMB,
            planned_altitude_ft_msl=5000,
        ),
        NavSection(
            project_id=project.id,
            sequence=1,
            from_node_id=first_turn.id,
            to_node_id=vrep.id,
            phase=FlightPhase.DESCENT,
            planned_altitude_ft_msl=5000,
        ),
        NavSection(
            project_id=project.id,
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


@pytest.mark.parametrize(
    "loss_value",
    [0.0, 60.0, 180.0],
)
def test_legacy_loss_time_is_not_used_for_status_or_timing(
    airports,
    performance_repository,
    project,
    loss_value: float,
) -> None:
    service = CalculationService(airports, performance_repository)
    baseline = service.calculate(project, FakeWeatherProvider())
    legacy = project.model_copy(deep=True)
    for section in legacy.sections:
        section.loss_time_seconds = loss_value

    outcome = service.calculate(
        legacy,
        FakeWeatherProvider(),
    )

    assert outcome.status == baseline.status
    assert not outcome.blockers
    assert not baseline.blockers
    assert all(
        point.estimated_time_utc == baseline_point.estimated_time_utc
        for point, baseline_point in zip(
            outcome.derived_points,
            baseline.derived_points,
            strict=True,
        )
    )
    assert all(
        section.zone_ete_seconds.adopted() == baseline.sections[index].zone_ete_seconds.adopted()
        for index, section in enumerate(outcome.sections)
    )
    assert all(
        section.section_fuel_gal.adopted() == baseline.sections[index].section_fuel_gal.adopted()
        for index, section in enumerate(outcome.sections)
    )
    assert all(
        section.cumulative_ete_seconds.adopted()
        == baseline.sections[index].cumulative_ete_seconds.adopted()
        for index, section in enumerate(outcome.sections)
    )
    assert all(
        section.remaining_fuel_gal.adopted()
        == baseline.sections[index].remaining_fuel_gal.adopted()
        for index, section in enumerate(outcome.sections)
    )
    assert outcome.derived_points[0].along_route_distance_nm == pytest.approx(
        baseline.derived_points[0].along_route_distance_nm,
        abs=1e-9,
    )


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


def test_outcome_adoption_and_snapshot_round_trip(
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
    snapshot_path = projects.snapshot(
        saved,
        outcome,
        calculation,
        msm_package_version=None,
    )
    restored = repository.load_snapshot(saved.id, UUID(snapshot_path.stem))
    assert restored.calculation_results.model_dump(mode="json") == outcome.model_dump(mode="json")
    assert restored.input_data.revision == saved.revision

    legacy_qnh_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    legacy_qnh_payload["input_data"]["schema_version"] = 1
    legacy_qnh_payload["input_data"]["manual_qnh_hpa"] = 1013.0
    legacy_qnh_payload["input_data"].pop("run_up_included")
    legacy_qnh_payload["input_data"].pop("air_conditioning_enabled")
    legacy_qnh_payload["calculation_results"]["qnh_hpa"] = {
        "automatic": 1013.0,
        "manual": None,
        "adopted_source": "AUTOMATIC",
        "automatic_metadata": {},
    }
    legacy_qnh_payload["weather_requests"].append(
        {
            "request_id": "project:qnh",
            "kind": "ESTIMATED_QNH",
            "latitude_deg": 31.877,
            "longitude_deg": 131.449,
            "valid_time_utc": "2026-08-17T00:00:00Z",
        }
    )
    legacy_qnh_payload["weather_results"].append(
        {
            "request_id": "project:qnh",
            "kind": "ESTIMATED_QNH",
            "availability": "AVAILABLE",
            "values": {"qnh_hpa": 1013.0},
            "metadata": {},
        }
    )
    snapshot_path.write_text(json.dumps(legacy_qnh_payload), encoding="utf-8")
    migrated_snapshot = repository.load_snapshot(saved.id, UUID(snapshot_path.stem))
    migrated_json = migrated_snapshot.model_dump(mode="json")
    assert migrated_snapshot.input_data.run_up_included is True
    assert migrated_snapshot.input_data.air_conditioning_enabled is True
    assert "qnh_hpa" not in migrated_json["calculation_results"]
    assert all(item["kind"] != "ESTIMATED_QNH" for item in migrated_json["weather_requests"])
    assert all(item["kind"] != "ESTIMATED_QNH" for item in migrated_json["weather_results"])
    snapshot_path.write_text(migrated_snapshot.model_dump_json(), encoding="utf-8")

    old_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    old_payload["calculation_results"].pop("display_rows")
    old_snapshot = CalculationSnapshot.model_validate_json(
        json.dumps(old_payload, ensure_ascii=False),
        strict=True,
    )
    assert old_snapshot.calculation_results.display_rows == []

    # A short-lived dev schema persisted display rows by inheriting
    # SectionResult.  Accept the snapshot but discard that projection: Project
    # inputs, not a saved display_rows list, are the source for regeneration.
    old_payload["calculation_results"]["display_rows"] = [
        outcome.sections[0].model_dump(mode="json")
        | {
            "row_type": "CALCULATION_ZONE",
            "counts_toward_totals": True,
        }
    ]
    legacy_snapshot = CalculationSnapshot.model_validate_json(
        json.dumps(old_payload, ensure_ascii=False),
        strict=True,
    )
    assert legacy_snapshot.calculation_results.display_rows == []


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
