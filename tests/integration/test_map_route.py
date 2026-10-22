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


@pytest.mark.parametrize("import_candidate", [False, True])
def test_map_route_planning_calculation_and_hydration(local, import_candidate):
    # Same physical route as the established KML golden; labels do not drive calculation.
    points = [
        point("FROM", airport="RJFM"),
        point("WP1", 32.1156353433264, 130.3376784271216),
        point("WP2", 32.89346338383958, 130.5368732463333),
        point("WP3", 33.02960582095061, 130.4440880275259),
        point("TO", airport="RJFS"),
    ]
    if import_candidate:
        imported = json.loads(local.dispatch("importRoute", {
            "filename": "route.kml",
            "kml_text": (ROOT / "tests/fixtures/issue_43_golden.kml").read_text(),
        }))
        candidate = imported["import"]["candidates"][0]
        assert candidate["departureAirportId"] == "RJFM"
        assert candidate["destinationAirportId"] == "RJFS"
        # Exercise the parser DTO -> map confirmation geometry used by #176.
        points = [point(f"WP{index}", latitude, longitude)
                  for index, (latitude, longitude) in enumerate(candidate["coordinates"])]
        points[0]["airport_id"] = candidate["departureAirportId"]
        points[-1]["airport_id"] = candidate["destinationAirportId"]
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
    if not import_candidate:
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
    unchanged = json.loads(local.dispatch("confirmRoute", edit_payload(calculated["project"])))
    assert unchanged["project"] == calculated["project"]
    assert unchanged["outcome"] == calculated["outcome"]
    assert (unchanged["workingRecovery"]["last_calculation"]
            == calculated["workingRecovery"]["last_calculation"])
    changed_request = edit_payload(calculated["project"])
    changed_request["map_points"].pop(2)
    changed = json.loads(local.dispatch("confirmRoute", changed_request))
    assert not changed["readiness"]["calculationIsCurrent"]
    assert (changed["workingRecovery"]["last_calculation"]
            == calculated["workingRecovery"]["last_calculation"])
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


def test_map_route_removes_only_consecutive_duplicates(local):
    state = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFM"), point("repeat", airport="RJFM"),
        point("WP1"), point("WP2"),
        point("via", airport="RJFO"), point("return", airport="RJFM"),
        point("TO", airport="RJFO"), point("repeat TO", airport="RJFO"),
    ])))
    nodes = state["project"]["route_nodes"]
    assert [node["name"] for node in nodes] == ["RJFM", "WP1", "RJFO", "RJFM", "RJFO"]
    assert all(
        (a["latitude_deg"], a["longitude_deg"]) != (b["latitude_deg"], b["longitude_deg"])
        for a, b in zip(nodes, nodes[1:], strict=False)
    )


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


def test_duplicate_destination_coordinate_keeps_airport_identity(local):
    destination = local.app.airports.get("RJFO")
    state = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFM"),
        point("WP1", float(destination.latitude_deg), float(destination.longitude_deg)),
        point("TO", airport="RJFO"),
    ])))
    assert [node["name"] for node in state["project"]["route_nodes"]] == ["RJFM", "RJFO"]


def edit_payload(project):
    nodes = sorted(project["route_nodes"], key=lambda node: node["sequence"])
    points = [
        dict(
            point(
                node["name"],
                node["latitude_deg"],
                node["longitude_deg"],
                project["departure_airport_id"]
                if index == 0
                else project["destination_airport_id"]
                if index == len(nodes) - 1
                else None,
            ),
            original_node_id=node["id"],
        )
        for index, node in enumerate(nodes)
    ]
    return dict(payload(points), edit_project_id=project["id"])


def test_route_edit_noop_keeps_project_and_last_calculation(local):
    state = json.loads(
        local.dispatch(
            "confirmRoute",
            payload(
                [
                    point("FROM", airport="RJFO"),
                    point("WP1"),
                    point("WP2", 32.3, 130.5),
                    point("TO", airport="RJFS"),
                ]
            ),
        )
    )
    before = state["project"]
    after = json.loads(local.dispatch("confirmRoute", edit_payload(before)))
    assert after["project"] == before
    assert (
        after["workingRecovery"]["last_calculation"] == state["workingRecovery"]["last_calculation"]
    )


def test_route_edit_preserves_unchanged_directed_sections_and_their_checkpoints(local):
    state = json.loads(
        local.dispatch(
            "confirmRoute",
            payload(
                [
                    point("FROM", airport="RJFO"),
                    point("WP1"),
                    point("WP2", 32.3, 130.5),
                    point("TO", airport="RJFS"),
                ]
            ),
        )
    )
    sections = state["project"]["sections"]
    state = json.loads(
        local.dispatch(
            "replaceCheckPoints",
            {
                "check_points": [
                    dict(
                        name=f"CP{index}",
                        latitude_deg=32.2,
                        longitude_deg=130.4,
                        linked_section_id=section["id"],
                    )
                    for index, section in enumerate(sections)
                ]
            },
        )
    )
    before = state["project"]
    request = edit_payload(before)
    request["map_points"].pop(2)
    after = json.loads(local.dispatch("confirmRoute", request))["project"]
    assert after["id"] == before["id"]
    assert after["flight_date"] == before["flight_date"]
    assert after["total_usable_fuel_gal"] == before["total_usable_fuel_gal"]
    assert after["sections"][0] == before["sections"][0]
    assert after["sections"][1]["id"] not in {section["id"] for section in before["sections"]}
    assert [reference["name"] for reference in after["visual_references"]] == ["CP0"]


@pytest.mark.parametrize("invalid", ["project", "unknown_node", "duplicate_node"])
def test_route_edit_rejects_wrong_identity_without_mutation(local, invalid):
    from uuid import uuid4

    from autonavlog.web.facade import WebApplicationError

    before = json.loads(
        local.dispatch(
            "confirmRoute",
            payload(
                [
                    point("FROM", airport="RJFO"),
                    point("WP1"),
                    point("TO", airport="RJFS"),
                ]
            ),
        )
    )["project"]
    request = edit_payload(before)
    if invalid == "project":
        request["edit_project_id"] = str(uuid4())
    elif invalid == "unknown_node":
        request["map_points"][1]["original_node_id"] = str(uuid4())
    else:
        request["map_points"][1]["original_node_id"] = request["map_points"][0]["original_node_id"]
    with pytest.raises(WebApplicationError):
        local.dispatch("confirmRoute", request)
    assert json.loads(local.dispatch("bootstrap"))["project"] == before


def test_noop_edit_preserves_explicit_no_vrep_policy(local):
    request = payload([
        point("FROM", airport="RJFO"), point("WP1"), point("TO", airport="RJFS"),
    ])
    request["use_penultimate_as_vrep"] = False
    before = json.loads(local.dispatch("confirmRoute", request))["project"]
    reconfirm = edit_payload(before)
    reconfirm["use_penultimate_as_vrep"] = True
    after = json.loads(local.dispatch("confirmRoute", reconfirm))["project"]
    assert after == before
    assert after["metadata"]["ui_state"]["arrival_plan"] is None


def test_existing_long_name_survives_reload_noop_and_rebuild(local):
    local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFO"), point("WP1"),
        point("WP2", 32.3, 130.5), point("TO", airport="RJFS"),
    ]))
    long_name = "既存" * 100
    local.session.project.route_nodes[1].name = long_name
    state = json.loads(local.dispatch("bootstrap"))
    restored = json.loads(local.dispatch("bootstrap", state["workingRecovery"]))
    before = restored["project"]
    assert before["route_nodes"][1]["name"] == long_name
    assert json.loads(local.dispatch("confirmRoute", edit_payload(before)))["project"] == before
    request = edit_payload(before)
    request["map_points"].pop(2)
    after = json.loads(local.dispatch("confirmRoute", request))["project"]
    assert after["route_nodes"][1]["name"] == long_name
    assert after["route_nodes"][1]["id"] == before["route_nodes"][1]["id"]


@pytest.mark.parametrize("identity", ["none", "unknown", "existing"])
def test_long_new_names_cannot_borrow_original_identity(local, identity):
    from uuid import uuid4

    from autonavlog.web.facade import WebApplicationError

    before = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFO"), point("WP1"), point("TO", airport="RJFS"),
    ])))["project"]
    request = edit_payload(before)
    request["map_points"][1]["name"] = "x" * 101
    if identity == "none":
        request["map_points"][1].pop("original_node_id")
    elif identity == "unknown":
        request["map_points"][1]["original_node_id"] = str(uuid4())
    with pytest.raises((ValueError, WebApplicationError)):
        local.dispatch("confirmRoute", request)
    assert json.loads(local.dispatch("bootstrap"))["project"] == before


def test_fresh_map_cannot_use_forged_identity_for_long_name(local):
    from uuid import uuid4

    from autonavlog.web.facade import WebApplicationError

    points = [point("FROM", airport="RJFO"), point("x" * 101), point("TO", airport="RJFS")]
    with pytest.raises(ValueError):
        ConfirmRouteRequest.model_validate(payload(points))
    points[1]["original_node_id"] = str(uuid4())
    with pytest.raises(WebApplicationError):
        local.dispatch("confirmRoute", payload(points))
    assert json.loads(local.dispatch("bootstrap"))["project"] is None
