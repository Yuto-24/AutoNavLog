"""Issue #204 representative weather replay through the shared MSM projection."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.rjfm_departure_plan import (
    RjfmPlanReferences,
    apply_rjfm_departure_exception,
)
from autonavlog.domain.enums import AdoptedSource, FlightPhase, IssueSeverity, RouteNodeRole
from autonavlog.domain.planning import (
    AirportSelection,
    ArrivalPlan,
    PatternAltitudeValidationStatus,
    PersistedUiState,
    ReferenceDataSnapshot,
    RjfmCoordinate,
)
from autonavlog.domain.project import ManualWind, NavSection, RouteNode
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.weather.fake_provider import FakeWeatherProvider, _default_result
from autonavlog.weather.msm_adapter import MsmWeatherProvider


@pytest.mark.parametrize("fixed_rca", [False, True])
@pytest.mark.parametrize("manual_speed", [None, 1.0])
def test_calm_replay_restores_tas_time_fuel_and_phase_boundaries(
    airports,
    project,
    tmp_path,
    fixed_rca,
    manual_speed,
) -> None:
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/issue_204_calm_weather.json").read_text()
    )
    performance_repository = PerformanceRepository.from_directory_for_application(
        "data/performance"
    )
    working = project.model_copy(deep=True)
    working.planned_departure_time_jst = datetime.fromisoformat(fixture["departure_jst"])
    working.flight_date = working.planned_departure_time_jst.date()
    working.selected_forecast_run_id = fixture["run"]
    working.sections = []
    working.route_nodes = [
        RouteNode(
            sequence=i,
            name=str(i),
            longitude_deg=x,
            latitude_deg=y,
            role=RouteNodeRole.AIRPORT
            if i == 0
            else RouteNodeRole.DESTINATION
            if i == 7
            else RouteNodeRole.VISUAL_REPORTING_POINT
            if i == 6
            else RouteNodeRole.ROUTE_POINT,
        )
        for i, (x, y) in enumerate(fixture["coordinates"])
    ]
    working.sections = [
        NavSection(
            sequence=i,
            from_node_id=a.id,
            to_node_id=b.id,
            phase=FlightPhase.CLIMB
            if i == 0
            else FlightPhase.DESCENT
            if i == 5
            else FlightPhase.VISUAL_ARRIVAL
            if i == 6
            else FlightPhase.CRUISE,
            planned_altitude_ft_msl=2200 if i == 6 else 5500,
        )
        for i, (a, b) in enumerate(zip(working.route_nodes, working.route_nodes[1:], strict=False))
    ]
    selections = [
        AirportSelection(
            **airports.get(icao).model_dump(
                include={
                    "id",
                    "icao",
                    "name",
                    "latitude_deg",
                    "longitude_deg",
                    "elevation_ft_msl",
                    "source",
                    "source_revision",
                }
            ),
            pattern_altitude_ft_msl=1000.0,
            pattern_altitude_source="fixture",
            pattern_altitude_source_revision="fixture",
            pattern_altitude_validation_status=PatternAltitudeValidationStatus.VERIFIED,
        )
        for icao in ("RJFM", "RJFO")
    ]
    state = PersistedUiState(
        arrival_plan=ArrivalPlan(
            visual_reporting_point_node_id=working.route_nodes[-2].id,
            selected_pattern_altitude_ft_msl=1000,
            selected_pattern_altitude_source=AdoptedSource.AUTOMATIC,
        ),
        reference_data_snapshot=ReferenceDataSnapshot(
            departure_airport=selections[0],
            destination_airport=selections[1],
        ),
    )
    working.metadata["ui_state"] = state.model_dump(mode="json")
    refs = RjfmPlanReferences(
        revision="fixture-rjfm-v1",
        content_fingerprint="b" * 64,
        **{
            name: RjfmCoordinate(
                latitude_deg=fixture["coordinates"][i][1],
                longitude_deg=fixture["coordinates"][i][0],
                source="fixture",
                estimated_error_nm=0.2,
            )
            for name, i in [("umk", 1), ("omaru", 2), ("over_field", 0)]
        },
    )
    if fixed_rca:
        assert apply_rjfm_departure_exception(working, refs) is not None
    first = working.ordered_sections()[0]
    if manual_speed is not None:
        first.manual_wind_by_phase[FlightPhase.CLIMB] = ManualWind(
            direction_deg_from=282.0,
            speed_kt=manual_speed,
        )
    adapter = MsmWeatherProvider(tmp_path, client=object())

    def weather(request):
        if request.metadata.get("phase") != "CLIMB":
            return _default_result(request)
        first_leg = request.request_id.startswith(f"section:{first.id}:")
        raw = SimpleNamespace(
            availability=adapter._msm.Availability.AVAILABLE,
            values=fixture["first_climb" if first_leg else "downstream_climb"],
            warnings=() if first_leg else ("CALM_WIND_DIRECTION_UNDEFINED",),
            reason_code=None,
            provenance={"run": fixture["run"]},
        )
        return adapter._from_result(request, raw)

    service = CalculationService(
        airports,
        performance_repository,
        expected_rjfm_reference_revision=refs.revision,
        expected_rjfm_reference_content_fingerprint=refs.content_fingerprint,
    )
    result = service.calculate(
        working,
        FakeWeatherProvider(
            runs=(fixture["run"],),
            result_factory=weather,
        ),
    )
    assert result.converged, [(i.code, i.message) for i in result.issues]
    assert not [i for i in result.issues if i.severity == IssueSeverity.BLOCKER]
    assert all(r.tas_kt.adopted() > 0 for r in result.sections)
    assert all(r.zone_ete_seconds.adopted() > 0 for r in result.sections)
    assert all(r.section_fuel_gal.adopted() > 0 for r in result.sections)
    assert all(r.cumulative_ete_seconds.adopted() > 0 for r in result.sections)
    assert all(r.remaining_fuel_gal.adopted() > 0 for r in result.sections)
    assert result.sections[0].wind_direction_deg_from.adopted() == 282.0
    assert result.sections[0].wind_speed_kt.adopted() == pytest.approx(
        manual_speed if manual_speed is not None else fixture["first_climb"]["wind_speed_kt"],
    )
    assert any("RCA" in r.to_name for r in result.sections)
    assert any("EOC" in r.to_name for r in result.sections)
