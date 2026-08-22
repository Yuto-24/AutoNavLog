from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from autonavlog.application.calculation_service import CalculationService
from autonavlog.application.project_service import ProjectService
from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.enums import (
    Availability,
    DisplayCellState,
    FlightPhase,
    RouteNodeRole,
    VisualReferenceRole,
)
from autonavlog.domain.project import (
    Airport,
    NavSection,
    Project,
    RouteNode,
    VisualReference,
)
from autonavlog.domain.weather import WeatherRequest, WeatherResult
from autonavlog.importers.kml import import_kml_text, waypoint_name_slots_from_line
from autonavlog.nav.geodesy import geodesic_leg, point_along_leg
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.storage.airports import AirportRepository
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.weather.destination_taf import DestinationWindForecast
from autonavlog.weather.fake_provider import FakeWeatherProvider

FIXTURE = Path(__file__).parents[1] / "fixtures" / "issue_43_golden.kml"
JST = ZoneInfo("Asia/Tokyo")

PROJECT_ID = UUID("43000000-0000-0000-0000-000000000043")
NODE_IDS = [UUID(f"43000000-0000-0000-0000-{index:012d}") for index in range(1, 6)]
SECTION_IDS = [UUID(f"43000000-0000-0000-0001-{index:012d}") for index in range(1, 5)]
CHECK_POINT_IDS = [UUID(f"43000000-0000-0000-0002-{index:012d}") for index in range(1, 4)]


@pytest.fixture
def golden_airports() -> AirportRepository:
    return AirportRepository(
        [
            Airport(
                id="RJFM",
                icao="RJFM",
                name="宮崎空港",
                latitude_deg=31.87711883769073,
                longitude_deg=131.4484496650159,
                elevation_ft_msl=19,
                pattern_altitude_ft_msl=1019,
                source="Issue #43 Golden",
                source_revision="golden-v1",
            ),
            Airport(
                id="RJFS",
                icao="RJFS",
                name="佐賀空港",
                latitude_deg=33.14970516164441,
                longitude_deg=130.3022276887435,
                elevation_ft_msl=6,
                pattern_altitude_ft_msl=1006,
                source="Issue #43 Golden",
                source_revision="golden-v1",
            ),
        ]
    )


@pytest.fixture
def golden_project() -> Project:
    imported = import_kml_text(FIXTURE.read_text(encoding="utf-8"))
    line = imported.lines[0]
    slots = waypoint_name_slots_from_line(line)
    names = ["RJFM", *(name for name in slots if name is not None), "RJFS"]
    assert names == ["RJFM", "米ノ津", "玉名", "大牟田", "RJFS"]
    roles = [
        RouteNodeRole.AIRPORT,
        RouteNodeRole.TURN_POINT,
        RouteNodeRole.TURN_POINT,
        RouteNodeRole.VISUAL_REPORTING_POINT,
        RouteNodeRole.DESTINATION,
    ]
    nodes = [
        RouteNode(
            id=NODE_IDS[index],
            sequence=index,
            name=name,
            latitude_deg=line.coordinates[index][0],
            longitude_deg=line.coordinates[index][1],
            role=roles[index],
            source="Issue #43 Golden KML",
        )
        for index, name in enumerate(names)
    ]
    phases = [
        FlightPhase.CLIMB,
        FlightPhase.CRUISE,
        FlightPhase.DESCENT,
        FlightPhase.VISUAL_ARRIVAL,
    ]
    # 6500 ft reproduces the Golden RCA at 14.5 NM with the deterministic
    # climb weather/performance inputs below.  The later parent Legs retain
    # their own planning altitudes; Check Points do not create physical Legs.
    altitudes = [6500.0, 7500.0, 7500.0, 2000.0]
    sections = [
        NavSection(
            id=SECTION_IDS[index],
            sequence=index,
            from_node_id=nodes[index].id,
            to_node_id=nodes[index + 1].id,
            phase=phases[index],
            planned_altitude_ft_msl=altitudes[index],
        )
        for index in range(4)
    ]

    def check_point(
        index: int,
        name: str,
        section_index: int,
        along_section_nm: float,
    ) -> VisualReference:
        start = nodes[section_index]
        end = nodes[section_index + 1]
        leg = geodesic_leg(
            start.latitude_deg,
            start.longitude_deg,
            end.latitude_deg,
            end.longitude_deg,
        )
        latitude, longitude = point_along_leg(
            start.latitude_deg,
            start.longitude_deg,
            leg.initial_true_course_deg,
            along_section_nm,
        )
        return VisualReference(
            id=CHECK_POINT_IDS[index],
            name=name,
            latitude_deg=latitude,
            longitude_deg=longitude,
            role=VisualReferenceRole.CHECK_POINT,
            linked_section_id=sections[section_index].id,
            source="Issue #43 Golden fixture",
        )

    return Project(
        id=PROJECT_ID,
        name="Issue #43 Golden NAVLOG",
        pilot_name="GOLDEN",
        ship_identifier="JA43GL",
        flight_date=date(2026, 8, 15),
        planned_departure_time_jst=datetime(2026, 8, 15, 9, 0, tzinfo=JST),
        departure_airport_id="RJFM",
        destination_airport_id="RJFS",
        total_usable_fuel_gal=90,
        default_variation_deg_east=8,
        route_nodes=nodes,
        sections=sections,
        visual_references=[
            check_point(0, "岩瀬ダム", 0, 16.0),
            check_point(1, "えびの", 0, 34.0),
            check_point(2, "合津", 1, 24.5),
        ],
    )


def _golden_weather(request: WeatherRequest) -> WeatherResult:
    values: dict[str, float | str | None]
    if request.request_id == "departure:surface":
        values = {"temperature_c": 22.0, "wind_speed_kt": 0.0, "wind_direction_deg_from": None}
    elif request.request_id == "destination:surface":
        values = {"temperature_c": 27.0, "wind_speed_kt": 0.0, "wind_direction_deg_from": None}
    else:
        phase = request.metadata.get("phase")
        values_by_phase: dict[str, dict[str, float | str | None]] = {
            "CLIMB": {
                "temperature_c": 18.8,
                "wind_direction_deg_from": 154.0,
                "wind_speed_kt": 10.0,
            },
            "CRUISE": {
                "temperature_c": 14.2,
                "wind_direction_deg_from": 149.0,
                "wind_speed_kt": 8.0,
            },
            "DESCENT": {
                "temperature_c": 17.4,
                "wind_direction_deg_from": 142.0,
                "wind_speed_kt": 7.0,
            },
            "VISUAL_ARRIVAL": {
                "temperature_c": 25.0,
                "wind_direction_deg_from": 200.0,
                "wind_speed_kt": 8.0,
            },
        }
        values = values_by_phase[str(phase)]
    return WeatherResult(
        request_id=request.request_id,
        availability=Availability.AVAILABLE,
        kind=request.kind,
        values=values,
    )


def _calculate_golden(
    golden_airports: AirportRepository,
    golden_project: Project,
) -> CalculationOutcome:
    performance = PerformanceRepository.from_directory("data/performance")
    destination_wind = DestinationWindForecast(
        airport_icao="RJFS",
        valid_time_utc=None,
        availability=Availability.AVAILABLE,
        wind_direction_deg_from=200,
        wind_speed_kt=8,
        source_label="Issue #43 deterministic TAF",
    )
    return CalculationService(golden_airports, performance).calculate(
        golden_project,
        FakeWeatherProvider(result_factory=_golden_weather),
        destination_wind,
    )


@pytest.fixture
def golden_outcome(
    golden_airports: AirportRepository,
    golden_project: Project,
) -> CalculationOutcome:
    return _calculate_golden(golden_airports, golden_project)


def test_issue_43_golden_kml_physical_legs_and_course_policy(
    golden_project: Project,
    golden_outcome: CalculationOutcome,
) -> None:
    assert not golden_outcome.blockers
    assert [node.name for node in golden_project.ordered_nodes()] == [
        "RJFM",
        "米ノ津",
        "玉名",
        "大牟田",
        "RJFS",
    ]
    parent_rows = [
        row
        for row in golden_outcome.display_rows
        if row.row_type == "PHYSICAL_LEG_SUMMARY"
    ]
    assert [(row.from_name, row.to_name) for row in parent_rows] == [
        ("RJFM", "米ノ津"),
        ("米ノ津", "玉名"),
        ("玉名", "大牟田"),
        ("大牟田", "RJFS"),
    ]
    assert [row.distance.text.split(" / ")[0] for row in parent_rows] == [
        "58.5",
        "47.5",
        "9.5",
        "10.0",
    ]
    assert [row.tc.text for row in parent_rows] == ["284", "012", "330", "315"]
    assert [row.variation.text for row in parent_rows] == ["+7", "+8", "+8", "+8"]
    assert [row.mc.text for row in parent_rows] == ["291", "020", "338", "323"]
    for result in golden_outcome.sections:
        tc = result.true_course_deg.adopted()
        variation = result.variation_deg_east.adopted()
        mc = result.magnetic_course_deg.adopted()
        wca = result.wca_deg.adopted()
        mh = result.magnetic_heading_deg.adopted()
        assert tc is not None and variation is not None and mc is not None
        assert wca is not None and mh is not None
        assert mc == pytest.approx((tc + variation) % 360.0, abs=1e-9)
        assert mh == pytest.approx((mc + wca) % 360.0, abs=1e-9)
    rca = next(
        point for point in golden_outcome.derived_points if point.type.value == "RCA"
    )
    assert rca.along_route_distance_nm == pytest.approx(14.5332565102, abs=1e-9)


def test_issue_43_golden_display_structure_inheritance_and_destination(
    golden_project: Project,
    golden_outcome: CalculationOutcome,
) -> None:
    rows = golden_outcome.display_rows
    assert [row.row_type for row in rows].count("LEG_SEPARATOR") == 4
    displayed_cumulative_distance = 0.0
    displayed_cumulative_ete = 0.0
    for section in golden_project.ordered_sections()[:-1]:
        group = [row for row in rows if row.section_id == section.id]
        assert group[0].row_type == "PHYSICAL_LEG_SUMMARY"
        details = [row for row in group if row.row_type == "CALCULATION_ZONE"]
        assert details
        displayed_zone_distance = sum(float(row.distance.text or "nan") for row in details)
        displayed_zone_ete = sum(float(row.ete.text or "nan") for row in details)
        displayed_cumulative_distance += displayed_zone_distance
        displayed_cumulative_ete += displayed_zone_ete
        assert group[0].distance.text == (
            f"{displayed_zone_distance:.1f} / {displayed_cumulative_distance:.1f}"
        )
        assert group[0].ete.text == (
            f"{displayed_zone_ete:.1f} / {displayed_cumulative_ete:.1f}"
        )

    check_point_names = [
        row.to_name
        for row in rows
        if row.row_type == "CALCULATION_ZONE"
        and row.to_name in {"岩瀬ダム", "えびの", "合津"}
    ]
    assert check_point_names == ["岩瀬ダム", "えびの", "合津"]
    assert all("CP:" not in row.to_name for row in rows)

    departure_parent = next(
        row
        for row in rows
        if row.section_id == SECTION_IDS[0]
        and row.row_type == "PHYSICAL_LEG_SUMMARY"
    )
    assert departure_parent.toat.text == "22.0"

    departure_details = [
        row
        for row in rows
        if row.section_id == SECTION_IDS[0]
        and row.row_type == "CALCULATION_ZONE"
    ]
    assert [row.to_name for row in departure_details] == [
        "RCA",
        "岩瀬ダム",
        "えびの",
        "米ノ津",
    ]
    assert departure_details[0].distance.text == "14.5"
    assert departure_details[0].pa.text == "↗"
    assert departure_details[0].wca.text == "-4"
    assert departure_details[0].mh.text == "287"
    assert departure_details[1].distance.text == "1.5"
    assert departure_details[1].pa.text == "6500"

    cruise_group = [row for row in rows if row.section_id == SECTION_IDS[1]]
    cruise_parent = cruise_group[0]
    assert cruise_parent.pa.text == "7500"
    assert cruise_parent.toat.text == "14.2"
    assert cruise_parent.wca.text == "+2"
    assert cruise_parent.mh.text == "022"
    eoc_row = next(row for row in cruise_group if row.to_name == "EOC")
    assert eoc_row.toat.state == DisplayCellState.INHERIT
    assert eoc_row.toat.text is None
    before_eoc = next(row for row in cruise_group if row.to_name == "合津")
    assert before_eoc.toat.state == DisplayCellState.INHERIT
    after_eoc = next(row for row in cruise_group if row.to_name == "玉名")
    assert after_eoc.toat.state == DisplayCellState.DISPLAY_VALUE

    assert any(row.pa.text == "↗" for row in rows)
    assert any(row.pa.text == "↘" for row in rows)
    assert any(
        row.pa_display_kind.value == "ESTIMATED"
        and row.pa.text is not None
        and row.pa.text.startswith("(")
        for row in rows
    )
    assert any(row.wca.text is not None and row.wca.text.startswith("+") for row in rows)

    vrep_row = next(
        row
        for row in rows
        if row.section_id == SECTION_IDS[2]
        and row.row_type == "CALCULATION_ZONE"
        and row.to_name == "大牟田"
    )
    assert vrep_row.pa.text == "2000"

    final_group = [row for row in rows if row.section_id == SECTION_IDS[-1]]
    assert [row.row_type for row in final_group] == [
        "PHYSICAL_LEG_SUMMARY",
        "DESTINATION_INFO",
    ]
    final_parent, destination = final_group
    assert final_parent.wind.text == "CALM"
    assert final_parent.wca.text == "0"
    assert final_parent.gs.text == final_parent.tas.text
    assert destination.to_name == "RJFS"
    assert destination.pa.text == "6"
    assert destination.toat.text == "27.0"
    assert destination.wind.text == "200/8"
    for field in (
        "cas",
        "tas",
        "tc",
        "variation",
        "mc",
        "wca",
        "mh",
        "distance",
        "gs",
        "ete",
        "eto",
        "ato",
        "ate",
        "fuel",
    ):
        cell = getattr(destination, field)
        assert cell.state == DisplayCellState.BLANK
        assert cell.text is None


def test_issue_43_saved_golden_project_regenerates_identical_display_rows_after_switch(
    golden_airports: AirportRepository,
    golden_project: Project,
    tmp_path: Path,
) -> None:
    projects = ProjectService(LocalProjectRepository(tmp_path))
    saved_golden = projects.save(golden_project).project

    other_id = UUID("43000000-0000-0000-0003-000000000001")
    other_payload = golden_project.model_dump()
    other_payload |= {"id": other_id, "name": "別Project", "revision": 0}
    other_project = Project.model_validate(other_payload)
    saved_other = projects.save(other_project).project

    selected_other = projects.load(saved_other.id)
    reselected_golden = projects.load(saved_golden.id)
    assert selected_other.id == saved_other.id
    assert [node.model_dump() for node in reselected_golden.ordered_nodes()] == [
        node.model_dump() for node in saved_golden.ordered_nodes()
    ]
    assert [
        (section.planned_altitude_ft_msl, section.phase)
        for section in reselected_golden.ordered_sections()
    ] == [
        (section.planned_altitude_ft_msl, section.phase)
        for section in saved_golden.ordered_sections()
    ]
    assert [reference.name for reference in reselected_golden.visual_references] == [
        "岩瀬ダム",
        "えびの",
        "合津",
    ]

    before_switch = _calculate_golden(golden_airports, saved_golden)
    after_reselect = _calculate_golden(golden_airports, reselected_golden)
    assert [row.model_dump(mode="json") for row in after_reselect.display_rows] == [
        row.model_dump(mode="json") for row in before_switch.display_rows
    ]


def test_issue_43_golden_eoc_uses_vertical_time_plus_one_minute_and_raw_order(
    golden_outcome: CalculationOutcome,
) -> None:
    descent = next(
        section
        for section in golden_outcome.sections
        if section.phase == FlightPhase.DESCENT
    )
    metadata = descent.performance_metadata
    altitude_difference = (
        float(metadata["cruise_altitude_ft_msl"])
        - float(metadata["target_altitude_ft_msl"])
    )
    assert metadata["planned_duration_seconds"] == pytest.approx(
        altitude_difference / 500.0 * 60.0 + 60.0,
        abs=1e-9,
    )
    second_leg = [
        row
        for row in golden_outcome.display_rows
        if row.section_id == SECTION_IDS[1] and row.row_type == "CALCULATION_ZONE"
    ]
    labels = [row.to_name for row in second_leg]
    assert "EOC" in labels and "合津" in labels
    assert labels.index("合津") < labels.index("EOC")
    assert next(row for row in second_leg if row.to_name == "合津").distance.text == "24.5"
    eoc = next(point for point in golden_outcome.derived_points if point.type.value == "EOC")
    check_point = next(
        point
        for point in golden_outcome.check_point_projections
        if point.checkpoint_id == CHECK_POINT_IDS[2]
    )
    assert check_point.cumulative_distance_nm - eoc.along_route_distance_nm == pytest.approx(
        -1.5,
        abs=0.35,
    )
    assert check_point.cumulative_distance_nm < eoc.along_route_distance_nm


def test_issue_43_eoc_does_not_snap_to_a_nearby_check_point(
    golden_airports: AirportRepository,
    golden_project: Project,
    golden_outcome: CalculationOutcome,
) -> None:
    eoc = next(
        point for point in golden_outcome.derived_points if point.type.value == "EOC"
    )
    first_leg = geodesic_leg(
        golden_project.route_nodes[0].latitude_deg,
        golden_project.route_nodes[0].longitude_deg,
        golden_project.route_nodes[1].latitude_deg,
        golden_project.route_nodes[1].longitude_deg,
    )
    second_leg = geodesic_leg(
        golden_project.route_nodes[1].latitude_deg,
        golden_project.route_nodes[1].longitude_deg,
        golden_project.route_nodes[2].latitude_deg,
        golden_project.route_nodes[2].longitude_deg,
    )
    near_eoc_distance = eoc.along_route_distance_nm - first_leg.distance_nm + 0.25
    latitude, longitude = point_along_leg(
        golden_project.route_nodes[1].latitude_deg,
        golden_project.route_nodes[1].longitude_deg,
        second_leg.initial_true_course_deg,
        near_eoc_distance,
    )
    near_project = golden_project.model_copy(deep=True)
    near_check_point = next(
        reference
        for reference in near_project.visual_references
        if reference.name == "合津"
    )
    near_check_point.latitude_deg = latitude
    near_check_point.longitude_deg = longitude

    outcome = _calculate_golden(golden_airports, near_project)

    recalculated_eoc = next(
        point for point in outcome.derived_points if point.type.value == "EOC"
    )
    projected_check_point = next(
        point
        for point in outcome.check_point_projections
        if point.checkpoint_id == near_check_point.id
    )
    assert (
        projected_check_point.cumulative_distance_nm
        - recalculated_eoc.along_route_distance_nm
    ) == pytest.approx(0.25, abs=1e-5)
    labels = [
        row.to_name
        for row in outcome.display_rows
        if row.section_id == SECTION_IDS[1]
        and row.row_type == "CALCULATION_ZONE"
    ]
    assert labels.index("EOC") < labels.index("合津")
    assert not any("合津 / EOC" in label for label in labels)
