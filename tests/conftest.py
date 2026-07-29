from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from autonavlog.domain.enums import FlightPhase, RouteNodeRole
from autonavlog.domain.project import Airport, NavSection, Project, RouteNode
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.performance.schemas import (
    ClimbRow,
    CruiseRow,
    PerformanceManifest,
)
from autonavlog.storage.airports import AirportRepository

JST = ZoneInfo("Asia/Tokyo")


@pytest.fixture
def performance_repository() -> PerformanceRepository:
    climb_rows = []
    for altitude, time, fuel, distance in (
        (0.0, 0.0, 0.0, 0.0),
        (5000.0, 10.0, 4.0, 15.0),
        (6000.0, 12.0, 4.8, 18.0),
    ):
        for temperature in (0.0, 20.0):
            climb_rows.append(
                ClimbRow(
                    pressure_altitude_ft=altitude,
                    temperature_c=temperature,
                    weight_lb=3400,
                    cumulative_time_min=time + temperature / 100,
                    cumulative_fuel_gal=fuel + temperature / 200,
                    cumulative_distance_nm=distance + temperature / 50,
                    source_page="fixture",
                )
            )
    cruise_rows = []
    for altitude in (4000.0, 6000.0):
        for isa_deviation in (-15.0, 15.0):
            cruise_rows.extend(
                [
                    CruiseRow(
                        pressure_altitude_ft=altitude,
                        isa_deviation_c=isa_deviation,
                        rpm=2300,
                        map_in_hg=20,
                        power_percent=65,
                        ktas=150 - (altitude - 4000) / 2000,
                        gph=15 + (isa_deviation + 10) / 20,
                        source_page="fixture",
                    ),
                    CruiseRow(
                        pressure_altitude_ft=altitude,
                        isa_deviation_c=isa_deviation,
                        rpm=2350,
                        map_in_hg=21,
                        power_percent=70,
                        ktas=155,
                        gph=16.5,
                        source_page="fixture",
                    ),
                ]
            )
    return PerformanceRepository(
        PerformanceManifest(
            aircraft="SR22 G6",
            source_document="fixture",
            source_revision="fixture-v1",
            verified_against="fixture",
            validation_status="VERIFIED",
        ),
        climb_rows,
        cruise_rows,
    )


@pytest.fixture
def airports() -> AirportRepository:
    return AirportRepository(
        [
            Airport(
                id="RJFM",
                icao="RJFM",
                name="Miyazaki",
                latitude_deg=31.877,
                longitude_deg=131.449,
                elevation_ft_msl=20,
                pattern_altitude_ft_msl=1020,
                source="fixture",
                source_revision="fixture-v1",
            ),
            Airport(
                id="RJFO",
                icao="RJFO",
                name="Oita",
                latitude_deg=33.479,
                longitude_deg=131.737,
                elevation_ft_msl=19,
                pattern_altitude_ft_msl=1019,
                source="fixture",
                source_revision="fixture-v1",
            ),
        ]
    )


@pytest.fixture
def project() -> Project:
    departure = RouteNode(
        sequence=0,
        name="RJFM",
        latitude_deg=31.877,
        longitude_deg=131.449,
        role=RouteNodeRole.AIRPORT,
    )
    turn = RouteNode(
        sequence=1,
        name="TP1",
        latitude_deg=32.45,
        longitude_deg=131.55,
        role=RouteNodeRole.TURN_POINT,
    )
    destination = RouteNode(
        sequence=2,
        name="RJFO",
        latitude_deg=33.479,
        longitude_deg=131.737,
        role=RouteNodeRole.DESTINATION,
    )
    sections = [
        NavSection(
            sequence=0,
            from_node_id=departure.id,
            to_node_id=turn.id,
            phase=FlightPhase.CLIMB,
            planned_altitude_ft_msl=5000,
        ),
        NavSection(
            sequence=1,
            from_node_id=turn.id,
            to_node_id=destination.id,
            phase=FlightPhase.CRUISE,
            planned_altitude_ft_msl=5000,
        ),
    ]
    return Project(
        name="NAV2 fixture",
        pilot_name="STUDENT",
        ship_identifier="JA00XX",
        flight_date=date(2026, 7, 29),
        planned_departure_time_jst=datetime(2026, 7, 29, 9, 0, tzinfo=JST),
        departure_airport_id="RJFM",
        destination_airport_id="RJFO",
        total_usable_fuel_gal=81,
        default_variation_deg_east=8,
        route_nodes=[departure, turn, destination],
        sections=sections,
    )
