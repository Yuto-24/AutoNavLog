"""Departure VOR changes the horizontal origin, never the airport performance inputs."""

import json
from pathlib import Path

import pytest

from autonavlog.domain.airport_vor import AIRPORT_VORS
from autonavlog.domain.planning import load_persisted_ui_state
from autonavlog.local import LocalApplication

ROOT = Path(__file__).resolve().parents[2]
INPUT = json.loads((ROOT / "tests/fixtures/issue_117_ftd.json").read_text())


def route(airport, mode="VOR"):
    destination = "RJFO" if airport != "RJFO" else "RJFM"
    return {
        **INPUT["confirm"],
        "candidate_kind": "map",
        "departure_origin": mode,
        "map_points": [
            {"name": airport, "airport_id": airport, "latitude_deg": 32.0, "longitude_deg": 131.0},
            {"name": "CP", "latitude_deg": 32.5, "longitude_deg": 131.5},
            {
                "name": destination,
                "airport_id": destination,
                "latitude_deg": 33.0,
                "longitude_deg": 131.0,
            },
        ],
    }


def update(project, mode=None):
    result = {
        key: INPUT["confirm"][key]
        for key in (
            "flight_date",
            "departure_time_jst",
            "weather_mode",
            "ftd_weather",
            "default_variation_deg_east",
        )
    }
    result["total_usable_fuel_gal"] = 90
    if mode is not None:
        result["departure_origin"] = mode
    return result


@pytest.mark.parametrize("airport", AIRPORT_VORS)
def test_all_airport_origins_and_roundtrip(airport):
    app = LocalApplication(ROOT / "data")
    try:
        base = json.loads(app.dispatch("confirmRoute", route(airport, "AIRPORT")))
        original = base["project"]
        state = json.loads(app.dispatch("updateProject", update(original, "VOR")))
        project = state["project"]
        vor = AIRPORT_VORS[airport]
        first = project["route_nodes"][0]
        assert first["name"] == vor.identifier
        assert first["latitude_deg"] == vor.latitude_deg
        assert first["longitude_deg"] == vor.longitude_deg
        assert project["route_nodes"][-1] == original["route_nodes"][-1]
        assert project["departure_airport_id"] == airport
        assert (
            project["metadata"]["ui_state"]["reference_data_snapshot"]
            == original["metadata"]["ui_state"]["reference_data_snapshot"]
        )
        assert project["metadata"]["ui_state"]["departure_vor"] == vor.model_dump(mode="json")
        restored = json.loads(app.dispatch("bootstrap", state["workingRecovery"]))
        assert restored["project"]["route_nodes"][0] == first
        unchanged = json.loads(app.dispatch("updateProject", update(restored["project"])))
        assert unchanged["project"]["route_nodes"][0] == first
        returned = json.loads(app.dispatch("updateProject", update(project, "AIRPORT")))
        assert (
            returned["project"]["route_nodes"][0]["latitude_deg"]
            == original["route_nodes"][0]["latitude_deg"]
        )
        assert returned["project"]["metadata"]["ui_state"]["departure_vor"] is None
    finally:
        app.close()


@pytest.mark.parametrize("kind", ["map", "line"])
def test_initial_vor_selection_and_calculation(kind):
    app = LocalApplication(ROOT / "data")
    try:
        if kind == "map":
            request = route("RJOB")  # OYE is off-airport and >0.5 NM from the ARP.
        else:
            app.dispatch(
                "importRoute",
                {
                    "filename": "route.kml",
                    "kml_text": (ROOT / "tests/fixtures/issue_43_golden.kml").read_text(),
                },
            )
            request = {**INPUT["confirm"], "departure_origin": "VOR"}
        state = json.loads(app.dispatch("confirmRoute", request))
        project = state["project"]
        assert project["metadata"]["ui_state"]["departure_vor"] is not None
        result = json.loads(app.dispatch("calculate"))
        assert "ROUTE_AIRPORT_ENDPOINT_MISMATCH" not in {
            issue["code"] for issue in result["outcome"]["issues"]
        }
        assert result["outcome"]["sections"]
        # Switching after a result must require recalculation.
        changed = json.loads(app.dispatch("updateProject", update(project, "AIRPORT")))
        assert not changed["readiness"]["calculationIsCurrent"]
    finally:
        app.close()


def test_schema7_loads_as_airport_and_rejects_new_field():
    old = {"state_schema_version": 7}
    state = load_persisted_ui_state(old)
    assert state.state_schema_version == 8
    assert state.departure_vor is None
    with pytest.raises(ValueError):
        load_persisted_ui_state(
            {**old, "departure_vor": AIRPORT_VORS["RJFM"].model_dump(mode="json")}
        )


def test_vor_mapping_covers_the_registered_airports_and_matches_evidence():
    from autonavlog.storage.reference_data import ReferenceDataCatalogRepository

    catalog = ReferenceDataCatalogRepository(ROOT / "data/reference").open_pack(
        ROOT / "data/reference/default"
    )
    assert set(AIRPORT_VORS) == set(catalog.airports)
    evidence = json.loads((ROOT / "docs/evidence/airport-vor-departure.json").read_text())
    for station in evidence["stations"]:
        adopted = AIRPORT_VORS[station["airport_id"]]
        assert adopted.identifier == station["vor_identifier"]
        assert adopted.latitude_deg == station["latitude_deg"]
        assert adopted.longitude_deg == station["longitude_deg"]
        assert adopted.source_revision == station["source_sha256"]
