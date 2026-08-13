import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.domain.enums import (
    AdoptedSource,
    Availability,
    FlightPhase,
    RouteNodeRole,
    WeatherRequestKind,
)
from autonavlog.domain.project import NavSection, RouteNode
from autonavlog.domain.weather import WeatherResult
from autonavlog.weather.fake_provider import FakeWeatherProvider


def _four_phase_project(project):
    routed = project.model_copy(deep=True)
    departure = RouteNode(
        sequence=0,
        name="RJFM",
        latitude_deg=31.877,
        longitude_deg=131.449,
        role=RouteNodeRole.AIRPORT,
    )
    climb_end = RouteNode(
        sequence=1,
        name="RCA-side",
        latitude_deg=32.15,
        longitude_deg=131.49,
        role=RouteNodeRole.ROUTE_POINT,
    )
    cruise_end = RouteNode(
        sequence=2,
        name="EOC-side",
        latitude_deg=32.65,
        longitude_deg=131.59,
        role=RouteNodeRole.ROUTE_POINT,
    )
    visual_reporting_point = RouteNode(
        sequence=3,
        name="VRP",
        latitude_deg=33.20,
        longitude_deg=131.68,
        role=RouteNodeRole.VISUAL_REPORTING_POINT,
    )
    destination = RouteNode(
        sequence=4,
        name="RJFO",
        latitude_deg=33.479,
        longitude_deg=131.737,
        role=RouteNodeRole.DESTINATION,
    )
    nodes = (
        departure,
        climb_end,
        cruise_end,
        visual_reporting_point,
        destination,
    )
    phases_and_altitudes = (
        (FlightPhase.CLIMB, 6_000),
        (FlightPhase.CRUISE, 6_000),
        (FlightPhase.DESCENT, 6_000),
        (FlightPhase.VISUAL_ARRIVAL, 2_000),
    )
    sections = [
        NavSection(
            sequence=index,
            from_node_id=start.id,
            to_node_id=end.id,
            phase=phase,
            planned_altitude_ft_msl=altitude,
        )
        for index, ((start, end), (phase, altitude)) in enumerate(
            zip(
                zip(nodes, nodes[1:], strict=False),
                phases_and_altitudes,
                strict=True,
            )
        )
    ]
    return routed.__class__.model_validate(
        routed.model_dump()
        | {
            "route_nodes": list(nodes),
            "sections": sections,
        }
    )


def test_weather_requests_use_cac_phase_representative_altitudes(
    airports,
    performance_repository,
    project,
) -> None:
    service = CalculationService(airports, performance_repository)
    routed = _four_phase_project(project)
    issues = []
    geometries = service._build_geometry(routed, issues)

    requests = service._weather_requests(
        routed,
        airports.get("RJFM"),
        airports.get("RJFO"),
        geometries,
        {},
    )

    assert not issues
    assert not any(
        request.kind == WeatherRequestKind.ESTIMATED_QNH for request in requests
    )
    aloft = {
        request.metadata["phase"]: request
        for request in requests
        if request.kind == WeatherRequestKind.ALOFT
        and request.request_id.endswith(":aloft")
    }
    assert aloft["CLIMB"].altitude_ft_msl == 3_010
    assert aloft["CRUISE"].altitude_ft_msl == 6_000
    assert aloft["DESCENT"].altitude_ft_msl == 4_000
    assert aloft["VISUAL_ARRIVAL"].altitude_ft_msl == 1_009.5

    for phase in ("CLIMB", "DESCENT", "VISUAL_ARRIVAL"):
        metadata = aloft[phase].metadata
        assert metadata["representative_altitude_policy"] == (
            "ARITHMETIC_MEAN_OF_ENDPOINT_ALTITUDES_MSL"
        )
        assert (
            metadata["representative_altitude_ft_msl"]
            == (metadata["lower_altitude_ft_msl"] + metadata["upper_altitude_ft_msl"]) / 2
        )
        assert metadata["source_rule"] == "CAC_REV19_8-(3)_5_AND_6"
    assert aloft["CRUISE"].metadata["representative_altitude_policy"] == (
        "SECTION_PLANNED_CRUISE_ALTITUDE_MSL"
    )


def test_manual_temperature_override_keeps_automatic_request_provenance(
    airports,
    performance_repository,
    project,
) -> None:
    manual = project.model_copy(deep=True)
    manual.sections[0].manual_temperature_c = 4.0
    service = CalculationService(airports, performance_repository)

    outcome = service.calculate(manual, FakeWeatherProvider())

    assert not outcome.blockers
    climb = next(section for section in outcome.sections if section.phase == FlightPhase.CLIMB)
    assert climb.temperature_c.automatic_value == 15.0
    assert climb.temperature_c.manual_override == 4.0
    assert climb.temperature_c.adopted() == 4.0
    assert climb.temperature_c.adopted_source == AdoptedSource.MANUAL
    request_metadata = climb.temperature_c.automatic_metadata["request_metadata"]
    assert request_metadata["representative_altitude_ft_msl"] == 2_510
    assert request_metadata["representative_altitude_policy"] == (
        "ARITHMETIC_MEAN_OF_ENDPOINT_ALTITUDES_MSL"
    )


def test_qnh_is_not_requested_or_required_for_navlog_calculation(
    airports,
    performance_repository,
    project,
) -> None:
    seen_kinds = []

    def weather_result(request):
        seen_kinds.append(request.kind)
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values={
                "u_ms": 0.0,
                "v_ms": 0.0,
                "wind_speed_kt": 0.0,
                "wind_direction_deg_from": None,
                "temperature_c": 15.0,
            },
        )

    outcome = CalculationService(
        airports,
        performance_repository,
    ).calculate(
        project,
        FakeWeatherProvider(result_factory=weather_result),
    )

    assert not outcome.blockers
    assert outcome.qnh_hpa.adopted() is None
    assert WeatherRequestKind.ESTIMATED_QNH not in seen_kinds
    assert all(
        section.pressure_altitude_exact_ft.adopted()
        == section.pressure_altitude_planning_ft.adopted()
        for section in outcome.sections
    )


def test_legacy_manual_qnh_does_not_change_navlog_values(
    airports,
    performance_repository,
    project,
) -> None:
    service = CalculationService(airports, performance_repository)
    baseline = service.calculate(project, FakeWeatherProvider())
    with_qnh = project.model_copy(deep=True)
    with_qnh.manual_qnh_hpa = 980.0
    compared = service.calculate(with_qnh, FakeWeatherProvider())

    assert not baseline.blockers
    assert not compared.blockers
    for left, right in zip(baseline.sections, compared.sections, strict=True):
        assert left.pressure_altitude_planning_ft.adopted() == (
            right.pressure_altitude_planning_ft.adopted()
        )
        assert left.tas_kt.adopted() == pytest.approx(right.tas_kt.adopted())
        assert left.zone_ete_seconds.adopted() == pytest.approx(
            right.zone_ete_seconds.adopted()
        )
