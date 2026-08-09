from autonavlog.application.calculation_service import CalculationService
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.performance.schemas import (
    ClimbRow,
    ClimbTemperaturePolicy,
    PerformanceManifest,
)
from autonavlog.weather.fake_provider import FakeWeatherProvider


def test_calculation_service_uses_manifest_climb_temperature_policy(
    airports,
    performance_repository,
    project,
) -> None:
    climb_rows = [
        ClimbRow(
            pressure_altitude_ft=altitude,
            temperature_c=isa_oat,
            weight_lb=3_600,
            cumulative_time_min=time,
            cumulative_fuel_gal=fuel,
            cumulative_distance_nm=distance,
            source_page="P/N 13772-006 Reissue A pp. 5-30/5-31",
        )
        for altitude, isa_oat, time, fuel, distance in (
            (0, 15, 0.0, 0.0, 0.0),
            (5_000, 5, 4.7, 1.7, 8.6),
            (6_000, 3, 5.8, 2.1, 10.7),
        )
    ]
    performance = PerformanceRepository(
        PerformanceManifest(
            aircraft="SR22 G6",
            source_document="P/N 13772-006",
            source_revision="Reissue A",
            verified_against="fixture",
            validation_status="VERIFIED",
            climb_temperature_policy=(ClimbTemperaturePolicy.ISA_BASELINE_10_PERCENT_PER_10C_ABOVE),
        ),
        climb_rows,
        performance_repository.cruise_rows,
    )

    outcome = CalculationService(airports, performance).calculate(
        project,
        FakeWeatherProvider(),
    )

    assert not any(issue.code == "CLIMB_PERFORMANCE_UNAVAILABLE" for issue in outcome.blockers)
    climb_section = next(
        section
        for section in outcome.sections
        if section.performance_metadata.get("type") == "climb"
    )
    assert climb_section.performance_metadata["temperature_policy"] == (
        "ISA_BASELINE_10_PERCENT_PER_10C_ABOVE"
    )
    assert climb_section.performance_metadata["temperature_adjustment_factor"] >= 1.0
