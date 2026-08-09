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
    qnh = next(request for request in requests if request.kind == WeatherRequestKind.ESTIMATED_QNH)
    assert qnh.metadata == {
        "station_icao": "RJFM",
        "source_rule": "AUTOMATIC_QNH_PROVIDER",
        "airport_id": "RJFM",
        "airport_elevation_ft_msl": 20,
    }
    aloft = {
        request.metadata["phase"]: request
        for request in requests
        if request.kind == WeatherRequestKind.ALOFT
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


@pytest.mark.parametrize("label_location", ("values", "metadata"))
def test_observed_metar_qnh_keeps_provider_label_provenance_and_warnings(
    airports,
    performance_repository,
    project,
    label_location,
) -> None:
    def metar_result(request):
        if request.kind == WeatherRequestKind.ESTIMATED_QNH:
            values = {"qnh_hpa": 1007.8}
            metadata = {
                "provider": "metar",
                "provenance": {
                    "station_icao": "RJFM",
                    "observation_time_utc": "2026-07-29T00:00:00Z",
                },
            }
            if label_location == "values":
                values["label"] = "METAR観測QNH"
                metadata["label"] = "metadata側の予備ラベル"
            else:
                metadata["label"] = "METAR観測QNH"
            return WeatherResult(
                request_id=request.request_id,
                availability=Availability.AVAILABLE,
                kind=request.kind,
                values=values,
                warnings=("METAR_QNH_OBSERVATION",),
                metadata=metadata,
            )
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
        FakeWeatherProvider(result_factory=metar_result),
    )

    assert not outcome.blockers
    assert outcome.qnh_hpa.adopted() == pytest.approx(1007.8)
    assert outcome.qnh_hpa.adopted_source == AdoptedSource.AUTOMATIC
    assert outcome.qnh_hpa.automatic_metadata["label"] == "METAR観測QNH"
    assert "MSM推定" not in outcome.qnh_hpa.automatic_metadata["label"]
    assert outcome.qnh_hpa.automatic_metadata["provenance"] == {
        "station_icao": "RJFM",
        "observation_time_utc": "2026-07-29T00:00:00Z",
    }
    assert outcome.qnh_hpa.automatic_metadata["request_metadata"] == {
        "station_icao": "RJFM",
        "source_rule": "AUTOMATIC_QNH_PROVIDER",
        "airport_id": "RJFM",
        "airport_elevation_ft_msl": 20,
    }
    assert outcome.qnh_hpa.warnings == ("METAR_QNH_OBSERVATION",)
