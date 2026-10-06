from __future__ import annotations

from datetime import UTC, timedelta

from autonavlog.application.calculation_service import CalculationService
from autonavlog.domain.enums import FlightPhase, WeatherRequestKind
from autonavlog.weather.fake_provider import FakeWeatherProvider, _default_result


def test_final_samples_cover_unused_phases_and_exact_destination_time(
    airports, performance_repository, project,
):
    project.sections[0] = project.sections[0].model_copy(update={
        "manual_wind_direction_deg": 111,
        "manual_wind_speed_kt": 11.0,
    })

    def weather(request):
        result = _default_result(request)
        if request.kind == WeatherRequestKind.ALOFT:
            result.values.update(wind_direction_deg_from=264.1234567891234, wind_speed_kt=20.0)
        else:
            result.values["temperature_c"] = request.valid_time_utc.timestamp() / 1e8
        return result

    provider = FakeWeatherProvider(result_factory=weather)
    service = CalculationService(airports, performance_repository)
    outcome = service.calculate(project, provider)
    assert not outcome.blockers
    snapshot = service.adopted_weather_snapshot(
        weather_mode=project.weather_mode,
        forecast_run_id=outcome.selected_forecast_run_id,
        calculation_fingerprint="a" * 64,
    )
    assert snapshot is not None
    assert len(snapshot.samples) == 8
    assert len(snapshot.samples) < len(service.last_weather_results)
    phases = {
        sample.request.metadata["phase"] for sample in snapshot.samples
        if sample.request.kind == WeatherRequestKind.ALOFT
    }
    assert phases == {"CLIMB", "CRUISE", "DESCENT"}
    assert FlightPhase.DESCENT not in {zone.phase for zone in outcome.sections}
    # The snapshot keeps provider conditions; the accompanying calculation-time
    # Project separately retains the manual override that actually won.
    aloft = next(s for s in snapshot.samples if s.request.kind == WeatherRequestKind.ALOFT)
    assert aloft.result.values["wind_direction_deg_from"] == 264.1234567891234
    assert outcome.sections[0].wind_direction_deg_from.adopted() == 111
    destination = next(s for s in snapshot.samples if s.request.request_id == "destination:surface")
    expected_arrival = project.planned_departure_time_jst.astimezone(UTC) + timedelta(
        seconds=outcome.sections[-1].cumulative_ete_seconds.adopted(),
    )
    assert destination.request.valid_time_utc == expected_arrival
    assert destination.result.values["temperature_c"] == expected_arrival.timestamp() / 1e8
    service.last_weather_results[-1].values["temperature_c"] = -80
    assert destination.result.values["temperature_c"] == expected_arrival.timestamp() / 1e8


def test_early_blocked_calculation_cannot_reuse_preceding_samples(
    airports, performance_repository, project,
):
    service = CalculationService(airports, performance_repository)
    service.calculate(project, FakeWeatherProvider())
    assert service.last_adopted_weather_samples
    invalid = project.model_copy(update={"route_nodes": []})
    assert service.calculate(invalid, FakeWeatherProvider()).blockers
    assert not service.last_adopted_weather_samples
    assert service.adopted_weather_snapshot(
        weather_mode=project.weather_mode,
        forecast_run_id="20260728000000",
        calculation_fingerprint="a" * 64,
    ) is None
