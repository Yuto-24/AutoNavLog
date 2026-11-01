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


@pytest.mark.parametrize("departure_origin", ["AIRPORT", "VOR"])
def test_route_edit_keeps_reference_occurrences_and_departure_origin(local, departure_origin):
    selected = reference_point()
    request = payload([
        point("FROM", airport="RJFM"), selected, selected,
        point("WP1", 32.5, 131.5), point("TO", airport="RJFO"),
    ])
    request["departure_origin"] = departure_origin
    initial = json.loads(local.dispatch("confirmRoute", request))
    project = initial["project"]
    edit = edit_payload(project)
    edit["departure_origin"] = departure_origin
    for selected_point, node in zip(edit["map_points"], project["route_nodes"], strict=True):
        selected_point["reference_snapshot"] = node["reference_snapshot"]
    unchanged = json.loads(local.dispatch("confirmRoute", edit))
    assert unchanged["project"] == project
    edit["map_points"].insert(-1, reference_point())
    changed = json.loads(local.dispatch("confirmRoute", edit))["project"]
    assert changed["route_nodes"][0] == project["route_nodes"][0]
    assert (changed["metadata"]["ui_state"]["departure_vor"]
            == project["metadata"]["ui_state"]["departure_vor"])
    assert changed["route_nodes"][1:3] == project["route_nodes"][1:3]
    assert changed["route_nodes"][-2]["reference_snapshot"] == selected["reference_snapshot"]
    assert len({node["id"] for node in changed["route_nodes"]}) == len(changed["route_nodes"])


def test_route_edit_preserves_user_renamed_reference_snapshot(local):
    selected = reference_point()
    initial = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFM"), selected, point("TO", airport="RJFO"),
    ])))
    node = initial["project"]["route_nodes"][1]
    renamed = json.loads(local.dispatch("renameRouteNode", {
        "node_id": node["id"], "name": "訓練地点",
    }))
    project = renamed["project"]
    edit = edit_payload(project)
    edit["map_points"][1]["reference_snapshot"] = node["reference_snapshot"]
    unchanged = json.loads(local.dispatch("confirmRoute", edit))
    assert unchanged["project"] == project
    malformed = json.loads(json.dumps(edit))
    malformed["map_points"][1]["name"] = "偽装"
    with pytest.raises(ValueError, match="対象のRoute点"):
        local.dispatch("confirmRoute", malformed)


def test_route_edit_promoted_airport_uses_saved_vor_before_matching_legs(local):
    initial = json.loads(local.dispatch("confirmRoute", {
        **payload([point("FROM", airport="RJOB"), point("again", airport="RJOB"),
                   point("CP", 34.5, 133.5), point("TO", airport="RJFO")]),
        "departure_origin": "VOR",
    }))
    project = initial["project"]
    edit = edit_payload(project)
    edit["departure_origin"] = "VOR"
    edit["map_points"].pop(0)
    edit["map_points"][0]["airport_id"] = "RJOB"
    changed = json.loads(local.dispatch("confirmRoute", edit))["project"]
    vor = project["metadata"]["ui_state"]["departure_vor"]
    first = changed["route_nodes"][0]
    assert ((first["latitude_deg"], first["longitude_deg"])
            == (vor["latitude_deg"], vor["longitude_deg"]))
    assert first["name"] == vor["identifier"]
    assert first["id"] != project["route_nodes"][1]["id"]
    assert changed["sections"][0]["id"] != project["sections"][1]["id"]
    assert changed["metadata"]["ui_state"]["departure_vor"] == vor


def test_route_edit_keeps_adopted_airports_across_catalog_updates(local):
    initial = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJOB"), point("CP", 34.5, 133.5),
        point("TO", airport="RJFO"),
    ])))
    recovery = initial["workingRecovery"]
    project = recovery["project"]
    adopted = project["metadata"]["ui_state"]["reference_data_snapshot"]
    for index, side in [(0, "departure_airport"), (-1, "destination_airport")]:
        project["route_nodes"][index]["latitude_deg"] += 0.001
        adopted[side]["latitude_deg"] = project["route_nodes"][index]["latitude_deg"]
        adopted[side]["source_revision"] = "adopted-older-catalog"
    adopted["destination_airport"]["pattern_altitude_ft_msl"] = 2300
    restored = json.loads(local.dispatch("bootstrap", recovery))
    project = restored["project"]
    edit = edit_payload(project)
    unchanged = json.loads(local.dispatch("confirmRoute", edit))
    assert unchanged["project"] == project
    edit["map_points"].insert(-1, point("new", 34.0, 133.0))
    changed = json.loads(local.dispatch("confirmRoute", edit))["project"]
    assert changed["route_nodes"][0] == project["route_nodes"][0]
    assert changed["route_nodes"][-1]["id"] == project["route_nodes"][-1]["id"]
    assert changed["metadata"]["ui_state"]["reference_data_snapshot"] == adopted
    arrival = changed["metadata"]["ui_state"]["arrival_plan"]
    assert arrival["selected_pattern_altitude_ft_msl"] == 2300
    assert changed["sections"][-1]["planned_altitude_ft_msl"] >= 2300


@pytest.mark.parametrize("middle", [1, 2, 3])
def test_appending_destination_reassigns_arrival_tail_without_losing_planning(local, middle):
    state = json.loads(local.dispatch("confirmRoute", payload([
        point("FROM", airport="RJFO"),
        *[point(f"WP{i}", 33.3 - i * 0.1, 131.5 - i * 0.1) for i in range(middle)],
        point("TO", airport="RJFS"),
    ])))
    before = state["project"]
    request = edit_payload(before)
    request["map_points"][-1]["airport_id"] = "RJFS"
    request["map_points"].append(point("new TO", airport="RJFK"))
    after = json.loads(local.dispatch("confirmRoute", request))["project"]
    phases = [section["phase"] for section in after["sections"]]
    assert phases == ["CLIMB", *(["CRUISE"] * (middle - 1)), "DESCENT", "VISUAL_ARRIVAL"]
    for old, retained in zip(before["sections"], after["sections"], strict=False):
        assert retained["id"] == old["id"]
        assert retained["planned_altitude_ft_msl"] == old["planned_altitude_ft_msl"]
        for key, value in old.items():
            if key != "phase":
                assert retained[key] == value
    calculated = json.loads(local.dispatch("calculate"))
    assert not any(issue["code"] == "FLIGHT_PHASE_SEQUENCE_INVALID"
                   for issue in calculated["outcome"]["issues"])
