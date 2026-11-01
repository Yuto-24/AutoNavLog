"""UMK completion uses the shared Local/Legacy facade and authoritative NAV LOG."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.local import LocalApplication

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
        parent = next(row for row in rows if row["from_node_id"] == nodes[1]["id"])
        assert parent["to_node_id"] == nodes[-1]["id"]
        assert parent["tc"]["state"] == "BLANK"
        children = [
            row
            for row in calculated["outcome"]["display_rows"]
            if row["row_type"] == "CALCULATION_ZONE"
            and row["section_id"] in {section["id"] for section in project["sections"][1:4]}
        ]
        for child in children:
            zone = next(
                z for z in outcome.sections if z.sequence == child["source_result_sequence"]
            )
            assert child["tc"]["effective_value"] == pytest.approx(zone.true_course_deg.adopted())
            assert child["mc"]["effective_value"] == pytest.approx(
                zone.magnetic_course_deg.adopted()
            )
            assert child["tc"]["state"] == "DISPLAY_VALUE"
        assert len({row["tc"]["effective_value"] for row in children}) >= 2
        assert float(parent["ete"]["text"].split(" / ")[0]) == pytest.approx(
            sum(float(row["ete"]["text"]) for row in children)
        )
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


@pytest.mark.parametrize("mode", ["line", "map"])
def test_outbound_omaru_input_matches_explicit_umk_and_restores(mode, monkeypatch) -> None:
    monkeypatch.setattr("autonavlog.web.facade.build_rjfm_departure_guidance", lambda *a, **k: None)
    core = []
    for explicit in (False, True):
        local = LocalApplication(ROOT / "data")
        try:
            fixture = "issue_125_umk.kml" if explicit else "issue_125_omaru.kml"
            request = {**INPUT["confirm"], "candidate_kind": mode}
            if mode == "line":
                local.dispatch(
                    "importRoute",
                    {
                        "filename": fixture,
                        "kml_text": (ROOT / "tests/fixtures" / fixture).read_text(),
                    },
                )
            else:
                points = [
                    {"name": "RJFM", "airport_id": "RJFM", "latitude_deg": 0, "longitude_deg": 0},
                    *(
                        [
                            {
                                "name": "UMK",
                                "latitude_deg": 31.985137767624444,
                                "longitude_deg": 131.42429852046251,
                            }
                        ]
                        if explicit
                        else []
                    ),
                    {
                        "name": "小丸",
                        "latitude_deg": 32.16255070087476,
                        "longitude_deg": 131.47036916946163,
                    },
                    {
                        "name": "米ノ津",
                        "latitude_deg": 32.1156353433264,
                        "longitude_deg": 130.3376784271216,
                    },
                    {
                        "name": "玉名",
                        "latitude_deg": 32.89346338383958,
                        "longitude_deg": 130.5368732463333,
                    },
                    {
                        "name": "大牟田",
                        "latitude_deg": 33.02960582095061,
                        "longitude_deg": 130.4440880275259,
                    },
                    {"name": "RJFS", "airport_id": "RJFS", "latitude_deg": 0, "longitude_deg": 0},
                ]
                request["map_points"] = points
            state = json.loads(local.dispatch("confirmRoute", request))
            project = state["project"]
            assert [n["name"] for n in project["route_nodes"]][:3] == ["RJFM", "UMK", "OMARU"]
            assert len(project["route_nodes"]) == 7
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
                    {
                        "section_id": s["id"],
                        "phase": s["phase"],
                        "planned_altitude_ft_msl": 2500
                        if i == len(project["sections"]) - 1
                        else 6500,
                    }
                    for i, s in enumerate(project["sections"])
                ],
            )
            local.dispatch("updateProject", update)
            calculated = json.loads(local.dispatch("calculate"))
            assert calculated["readiness"]["calculationIsCurrent"]
            outcome = CalculationOutcome.model_validate_json(json.dumps(calculated["outcome"]))
            parent = next(r for r in outcome.display_rows if r.row_type == "PHYSICAL_LEG_SUMMARY")
            assert (parent.from_name, parent.to_name) == ("RJFM", "OMARU")
            zones = outcome.sections[:2]
            assert zones[0].to_name == "UMK/RCA"
            assert zones[0].to_latitude_deg == 31.985137767624444
            assert zones[0].to_longitude_deg == 131.42429852046251
            assert zones[0].zone_ete_seconds.adopted() == pytest.approx(
                zones[0].performance_metadata["planned_duration_seconds"]
            )
            assert zones[0].section_fuel_gal.adopted() == pytest.approx(
                zones[0].performance_metadata["planned_fuel_gal"]
            )
            assert zones[0].true_course_deg.adopted() != pytest.approx(
                zones[1].true_course_deg.adopted()
            )
            core.append(
                [
                    (
                        z.zone_distance_nm.adopted(),
                        z.true_course_deg.adopted(),
                        z.magnetic_course_deg.adopted(),
                        z.zone_ete_seconds.adopted(),
                        z.section_fuel_gal.adopted(),
                    )
                    for z in zones
                ]
            )
            restored = json.loads(local.dispatch("bootstrap", calculated["workingRecovery"]))
            assert restored["readiness"]["calculationIsCurrent"]
            assert restored["outcome"] == calculated["outcome"]
            assert restored["project"]["route_nodes"] == calculated["project"]["route_nodes"]
            local.dispatch("updateProject", {**update, "total_usable_fuel_gal": 89})
            recalculated = json.loads(local.dispatch("calculate"))
            assert recalculated["readiness"]["calculationIsCurrent"]
            assert len(recalculated["project"]["route_nodes"]) == 7
            assert recalculated["outcome"]["summary"] == calculated["outcome"]["summary"]
        finally:
            local.close()
    assert core[0] == core[1]


def test_legacy_virtual_calculation_is_stale_after_physical_umk_restore(monkeypatch) -> None:
    from unittest.mock import patch

    monkeypatch.setattr("autonavlog.web.facade.build_rjfm_departure_guidance", lambda *a, **k: None)
    local = LocalApplication(ROOT / "data")
    try:
        # Retained legacy mode reproduces a previously saved, valid calculation.
        with patch("autonavlog.application.rjfm_departure_plan._reconcile_departure_umk"):
            local.dispatch(
                "importRoute",
                {
                    "filename": "legacy.kml",
                    "kml_text": (ROOT / "tests/fixtures/issue_125_omaru.kml").read_text(),
                },
            )
            state = json.loads(local.dispatch("confirmRoute", INPUT["confirm"]))
            project = state["project"]
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
            )
            local.dispatch("updateProject", update)
            calculated = json.loads(local.dispatch("calculate"))
            assert calculated["readiness"]["calculationIsCurrent"]
            assert len(calculated["project"]["route_nodes"]) == 6
        restored = json.loads(local.dispatch("bootstrap", calculated["workingRecovery"]))
        assert len(restored["project"]["route_nodes"]) == 7
        assert not restored["readiness"]["calculationIsCurrent"]
        recalculated = json.loads(local.dispatch("calculate"))
        assert recalculated["readiness"]["calculationIsCurrent"]
        assert (
            recalculated["project"]["metadata"]["ui_state"]["rjfm_departure_plan"][
                "main_route_mode"
            ]
            == "UMK_PHYSICAL"
        )
    finally:
        local.close()
