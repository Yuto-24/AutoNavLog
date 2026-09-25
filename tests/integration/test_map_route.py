"""Map creation feeds the existing Project and Calculation contracts without KML."""

import json
from pathlib import Path

import pytest

from autonavlog.local import LocalApplication
from autonavlog.web.models import ConfirmRouteRequest

ROOT = Path(__file__).resolve().parents[2]
INPUT = json.loads((ROOT / "tests/fixtures/issue_117_ftd.json").read_text())


def point(name, lat=32.1, lon=130.3, airport=None):
    return dict(name=name, latitude_deg=lat, longitude_deg=lon, airport_id=airport)


def payload(points):
    return {**INPUT["confirm"], "candidate_kind": "map", "map_points": points}


@pytest.fixture
def local():
    app = LocalApplication(ROOT / "data")
    yield app
    app.close()


def test_map_route_planning_calculation_and_hydration(local):
    # Same physical route as the established KML golden; labels do not drive calculation.
    points = [
        point("FROM", airport="RJFM"),
        point("WP1", 32.1156353433264, 130.3376784271216),
        point("WP2", 32.89346338383958, 130.5368732463333),
        point("WP3", 33.02960582095061, 130.4440880275259),
        point("TO", airport="RJFS"),
    ]
    state = json.loads(local.dispatch("confirmRoute", payload(points)))
    project = state["project"]
    assert [node["role"] for node in project["route_nodes"]] == [
        "AIRPORT",
        "ROUTE_POINT",
        "ROUTE_POINT",
        "VISUAL_REPORTING_POINT",
        "DESTINATION",
    ]
    assert project["route_nodes"][0]["name"] == "RJFM"
    assert project["metadata"]["web_import_filename"] is None
    assert state["workingRecovery"]["import_result"] is None
    update = {
        key: INPUT["confirm"][key]
        for key in (
            "flight_date",
            "departure_time_jst",
            "weather_mode",
            "ftd_weather",
            "default_variation_deg_east",
        )
    }
    update.update(
        total_usable_fuel_gal=90,
        selected_pattern_altitude_ft_msl=1000,
        visual_reporting_point_node_id=project["route_nodes"][-2]["id"],
        sections=[
            dict(section_id=section["id"], phase=section["phase"], planned_altitude_ft_msl=altitude)
            for section, altitude in zip(project["sections"], INPUT["altitudes"], strict=True)
        ],
    )
    local.dispatch("updateProject", update)
    calculated = json.loads(local.dispatch("calculate"))
    golden = json.loads((ROOT / "tests/fixtures/issue_117_ftd_golden.json").read_text())
    assert calculated["outcome"]["summary"] == golden["summary"]
    assert calculated["readiness"]["calculationIsCurrent"]
    assert calculated["workingRecovery"]["last_calculation"] is not None
    restored = json.loads(local.dispatch("bootstrap", calculated["workingRecovery"]))
    assert restored["project"]["id"] == project["id"]
    assert restored["outcome"] == calculated["outcome"]


def test_direct_airports_and_repeated_airport_occurrences(local):
    state = json.loads(
        local.dispatch(
            "confirmRoute", payload([point("FROM", airport="RJFM"), point("TO", airport="RJFO")])
        )
    )
    assert len(state["project"]["route_nodes"]) == 2
    assert state["project"]["metadata"]["ui_state"]["arrival_plan"] is None
    local.dispatch(
        "bootstrap",
        {
            "version": 1,
            "project": None,
            "outcome": None,
            "destination_wind": None,
            "import_result": None,
            "import_filename": None,
        },
    )
    state = json.loads(
        local.dispatch(
            "confirmRoute",
            payload(
                [
                    point("FROM", airport="RJFM"),
                    point("via", airport="RJFO"),
                    point("WP1"),
                    point("via", airport="RJFO"),
                    point("TO", airport="RJFM"),
                ]
            ),
        )
    )
    nodes = state["project"]["route_nodes"]
    assert len(nodes) == 5
    assert nodes[1]["name"] == nodes[3]["name"] == "RJFO"
    assert nodes[1]["id"] != nodes[3]["id"]


@pytest.mark.parametrize(
    "points",
    [
        [],
        [point("FROM", airport="RJFM")],
        [point("WP1"), point("TO", airport="RJFM")],
        [point("FROM", airport="RJFM"), point("WP1")],
        [point("FROM", airport="RJFM"), point("bad", float("nan")), point("TO", airport="RJFO")],
    ],
)
def test_invalid_map_routes_rejected(points):
    with pytest.raises(ValueError):
        ConfirmRouteRequest.model_validate(payload(points))
