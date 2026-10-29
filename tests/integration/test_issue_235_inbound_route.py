"""UMK completion uses the shared Local/Legacy facade and authoritative NAV LOG."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.local import LocalApplication
from autonavlog.nav.geodesy import geodesic_leg

ROOT = Path(__file__).resolve().parents[2]
INPUT = json.loads((ROOT / "tests/fixtures/issue_117_ftd.json").read_text())
KML = (ROOT / "tests/fixtures/issue_235_inbound_missing_umk.kml").read_text()


@pytest.mark.parametrize("mode", ["line", "map"])
def test_missing_inbound_umk_reaches_navlog_and_survives_restore_and_recalculation(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Independent diagnostic solver is outside the route-completion contract.
    monkeypatch.setattr("autonavlog.web.facade.build_rjfm_inbound_guidance", lambda *a, **k: None)
    local = LocalApplication(ROOT / "data")
    try:
        if mode == "line":
            local.dispatch("importRoute", {"filename": "inbound.kml", "kml_text": KML})
        confirmed = json.loads(
            local.dispatch(
                "confirmRoute",
                {
                    **INPUT["confirm"],
                    "candidate_kind": mode,
                    "all_leg_altitude_ft_msl": 5500,
                    **(
                        {
                            "map_points": [
                                {
                                    "name": "RJFO",
                                    "airport_id": "RJFO",
                                    "latitude_deg": 0,
                                    "longitude_deg": 0,
                                },
                                {
                                    "name": "小丸",
                                    "latitude_deg": 32.16255070087476,
                                    "longitude_deg": 131.47036916946163,
                                },
                                {
                                    "name": "有田",
                                    "latitude_deg": 31.94977931375614,
                                    "longitude_deg": 131.3535165268158,
                                },
                                {
                                    "name": "RJFM",
                                    "airport_id": "RJFM",
                                    "latitude_deg": 0,
                                    "longitude_deg": 0,
                                },
                            ]
                        }
                        if mode == "map"
                        else {}
                    ),
                },
            )
        )
        project = confirmed["project"]
        nodes = project["route_nodes"]
        assert len(nodes) == 5
        assert [node["name"] for node in nodes][1:3] == [
            "小丸" if mode == "line" else "OMARU",
            "UMK",
        ]
        assert nodes[-2]["role"] == "VISUAL_REPORTING_POINT"
        assert nodes[2]["role"] == "ROUTE_POINT"
        assert nodes[-2]["latitude_deg"] == 31.94977931375614
        assert (
            project["metadata"]["ui_state"]["arrival_plan"]["visual_reporting_point_node_id"]
            == nodes[-2]["id"]
        )
        assert project["sections"][1]["planned_altitude_ft_msl"] == 4500
        identities = [node["id"] for node in nodes]
        update = {
            **{
                key: INPUT["confirm"][key]
                for key in (
                    "flight_date",
                    "departure_time_jst",
                    "weather_mode",
                    "ftd_weather",
                    "default_variation_deg_east",
                )
            },
            "total_usable_fuel_gal": 90,
            "selected_pattern_altitude_ft_msl": 1000,
            "visual_reporting_point_node_id": nodes[-2]["id"],
            "defaults_confirmed": True,
            "sections": [
                {
                    "section_id": section["id"],
                    "phase": section["phase"],
                    "planned_altitude_ft_msl": section["planned_altitude_ft_msl"],
                }
                for section in project["sections"]
            ],
        }
        local.dispatch("updateProject", update)
        calculated = json.loads(local.dispatch("calculate"))
        outcome = CalculationOutcome.model_validate_json(json.dumps(calculated["outcome"]))
        assert calculated["readiness"]["calculationIsCurrent"]
        assert [node["id"] for node in calculated["project"]["route_nodes"]] == identities
        rows = [
            row
            for row in calculated["outcome"]["display_rows"]
            if row["row_type"] == "PHYSICAL_LEG_SUMMARY"
        ]
        for start, end in zip(nodes[1:3], nodes[2:4], strict=True):
            row = next(
                row
                for row in rows
                if row["from_node_id"] == start["id"] and row["to_node_id"] == end["id"]
            )
            geometry = geodesic_leg(
                start["latitude_deg"],
                start["longitude_deg"],
                end["latitude_deg"],
                end["longitude_deg"],
            )
            assert row["tc"]["effective_value"] == pytest.approx(geometry.initial_true_course_deg)
            assert sum(
                zone.zone_distance_nm.adopted() or 0
                for zone in outcome.sections
                if str(zone.section_id) == row["section_id"]
            ) == pytest.approx(geometry.distance_nm)
        eoc = [point for point in calculated["outcome"]["derived_points"] if point["type"] == "EOC"]
        assert len(eoc) == 1
        assert eoc[0]["latitude_deg"] == nodes[2]["latitude_deg"]
        assert eoc[0]["longitude_deg"] == nodes[2]["longitude_deg"]

        restored = json.loads(local.dispatch("bootstrap", calculated["workingRecovery"]))
        assert restored["readiness"]["calculationIsCurrent"]
        assert restored["outcome"] == calculated["outcome"]
        assert [node["id"] for node in restored["project"]["route_nodes"]] == identities
        local.dispatch("updateProject", {**update, "total_usable_fuel_gal": 89})
        stale = json.loads(local.dispatch("state"))
        assert not stale["readiness"]["calculationIsCurrent"]
        recalculated = json.loads(local.dispatch("calculate"))
        assert recalculated["readiness"]["calculationIsCurrent"]
        assert [node["id"] for node in recalculated["project"]["route_nodes"]] == identities
        assert recalculated["outcome"]["summary"] == calculated["outcome"]["summary"]
    finally:
        local.close()
