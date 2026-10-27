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


def test_map_route_preserves_every_click_including_consecutive_airports(local):
    state = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFM"), point("repeat", airport="RJFM"),
        point("WP1"), point("WP2"),
        point("via", airport="RJFO"), point("return", airport="RJFM"),
        point("TO", airport="RJFO"), point("repeat TO", airport="RJFO"),
    ])))
    nodes = state["project"]["route_nodes"]
    assert [node["name"] for node in nodes] == [
        "RJFM", "RJFM", "WP1", "WP2", "RJFO", "RJFM", "RJFO", "RJFO",
    ]
    assert len({node["id"] for node in nodes}) == 8


def test_identical_airports_rejected_before_creating_project(local):
    from autonavlog.web.facade import WebApplicationError
    with pytest.raises(WebApplicationError) as failure:
        local.dispatch("confirmRoute", payload([
            point("FROM", airport="RJFM"), point("TO", airport="RJFM"),
        ]))
    assert failure.value.code == "ROUTE_INCOMPLETE"
    assert json.loads(local.dispatch("bootstrap"))["project"] is None


def test_map_reference_slots_keep_generated_provenance(local):
    from autonavlog.storage.rjfm_reference import RjfmReferencePack
    pack = RjfmReferencePack.from_directory(ROOT / "data/reference/rjfm")
    umk = pack.points["UMK"].position
    omaru = pack.points["OMARU"].position
    state = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFM"),
        point("WP1", float(umk.latitude_deg), float(umk.longitude_deg)),
        point("WP2", float(omaru.latitude_deg), float(omaru.longitude_deg)),
        point("TO", airport="RJFO"),
    ])))
    nodes = state["project"]["route_nodes"]
    assert [node["name"] for node in nodes][1:3] == ["UMK", "OMARU"]
    assert all(node["name_source"] == "GENERATED" for node in nodes)
    assert all(node["source"] != "KML/KMZ Point" for node in nodes)


def test_exact_arbitrary_destination_coordinate_does_not_snap_or_drop(local):
    destination = local.app.airports.get("RJFO")
    state = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFM"),
        point("WP1", float(destination.latitude_deg), float(destination.longitude_deg)),
        point("TO", airport="RJFO"),
    ])))
    assert [node["name"] for node in state["project"]["route_nodes"]] == ["RJFM", "WP1", "RJFO"]


def reference_point():
    snapshot = json.loads((ROOT / "data/reference/vrep.json").read_text())[0]
    return {
        **point(snapshot["name"], snapshot["latitude_deg"], snapshot["longitude_deg"]),
        "reference_snapshot": snapshot,
    }


def test_reference_occurrences_snapshots_save_reload_and_master_independence(local):
    selected = reference_point()
    state = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFC"), selected, selected,
        point("WP1", 30.5, 130.5), selected, point("TO", airport="RJFG"),
    ])))
    nodes = state["project"]["route_nodes"]
    assert len(nodes) == 6
    occurrences = [node for node in nodes if node["reference_snapshot"]]
    assert len({node["id"] for node in occurrences}) == 3
    assert all(node["reference_snapshot"] == selected["reference_snapshot"] for node in occurrences)
    # A saved snapshot may be older than today's catalog; hydration must never resolve it anew.
    recovery = state["workingRecovery"]
    for node in recovery["project"]["route_nodes"]:
        if node["reference_snapshot"]:
            node["reference_snapshot"]["source"]["table_effective_date"] = "2000-01-01"
    restored = json.loads(local.dispatch("bootstrap", recovery))
    restored_nodes = restored["project"]["route_nodes"]
    assert [node["id"] for node in restored_nodes] == [node["id"] for node in nodes]
    for node in restored_nodes:
        if node["reference_snapshot"]:
            assert node["reference_snapshot"]["source"]["table_effective_date"] == "2000-01-01"
            assert node["latitude_deg"] == selected["latitude_deg"]
            assert node["longitude_deg"] == selected["longitude_deg"]


@pytest.mark.parametrize("change", [
    {"airport_id": "RJFC"}, {"latitude_deg": 1}, {"name": "wrong"},
    {"longitude_deg": 1},
])
def test_reference_snapshot_mismatch_rejected(change):
    with pytest.raises(ValueError):
        ConfirmRouteRequest.model_validate(payload([
            point("FROM", airport="RJFC"), {**reference_point(), **change},
            point("TO", airport="RJFG"),
        ]))


def test_reference_cannot_be_an_endpoint():
    selected = reference_point()
    for points in ([selected, point("TO", airport="RJFG")],
                   [point("FROM", airport="RJFC"), selected]):
        with pytest.raises(ValueError):
            ConfirmRouteRequest.model_validate(payload(points))


def test_v1_project_without_snapshot_remains_readable(local):
    """v1.22.9 serialized nodes do not have reference_snapshot at all."""
    state = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFM"), point("WP1"), point("TO", airport="RJFO"),
    ])))
    recovery = state["workingRecovery"]
    for node in recovery["project"]["route_nodes"]:
        del node["reference_snapshot"]
    restored = json.loads(local.dispatch("bootstrap", recovery))
    assert restored["project"]["id"] == state["project"]["id"]
    assert all(node["reference_snapshot"] is None for node in restored["project"]["route_nodes"])
    assert [node["id"] for node in restored["project"]["route_nodes"]] == [
        node["id"] for node in state["project"]["route_nodes"]
    ]


def test_snapshot_addition_does_not_relax_unknown_field_validation(local):
    from autonavlog.domain.project import Project

    state = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFC"), reference_point(), point("TO", airport="RJFG"),
    ])))
    state["project"]["route_nodes"][1]["unexpected_reference"] = "must not be ignored"
    with pytest.raises(ValueError, match="extra_forbidden"):
        Project.model_validate(state["project"])
