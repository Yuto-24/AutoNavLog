"""Agreed OMARU return: real geometry, separate descent clock, one parent."""

import json
from pathlib import Path

import pytest

from autonavlog.application.arrival import ARITA_COORDINATE
from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.local import LocalApplication
from autonavlog.nav.geodesy import geodesic_leg

ROOT = Path(__file__).resolve().parents[2]
INPUT = json.loads((ROOT / "tests/fixtures/issue_117_ftd.json").read_text())["confirm"]


@pytest.mark.parametrize("mode", ["line", "map"])
@pytest.mark.parametrize("via_arita", [False, True])
@pytest.mark.parametrize("explicit_umk", [False, True])
@pytest.mark.parametrize("rate", [500, 1000])
def test_return_completion_geometry_descent_and_restore(
    mode, via_arita, explicit_umk, rate, monkeypatch
):
    monkeypatch.setattr("autonavlog.web.facade.build_rjfm_inbound_guidance", lambda *a, **k: None)
    local = LocalApplication(ROOT / "data")
    # Explicit input locations differ slightly from masters and must be retained.
    umk = (
        (31.985237767624444, 131.42439852046251)
        if explicit_umk
        else (31.985137767624444, 131.42429852046251)
    )
    arita = (
        (ARITA_COORDINATE[0] + 0.0001, ARITA_COORDINATE[1] + 0.0001)
        if via_arita
        else ARITA_COORDINATE
    )
    points = [
        ("RJFO", 33.47949406702627, 131.7371201529664),
        ("小丸", 32.16255070087476, 131.47036916946163),
        *([("手動UMK", *umk)] if explicit_umk else []),
        *([("有田", *arita)] if via_arita else []),
        ("RJFM", 31.8772222222, 131.4486111111),
    ]
    try:
        request = {**INPUT, "candidate_kind": mode, "descent_rate_fpm": rate}
        if mode == "line":
            coordinates = " ".join(f"{lon},{lat},0" for _, lat, lon in points)
            labels = "".join(
                f"<Placemark><name>{name}</name><Point><coordinates>{lon},{lat},0</coordinates></Point></Placemark>"
                for name, lat, lon in points[1:-1]
            )
            kml = f'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><LineString><coordinates>{coordinates}</coordinates></LineString></Placemark>{labels}</Document></kml>'
            local.dispatch("importRoute", {"filename": "return.kml", "kml_text": kml})
        else:
            request["map_points"] = [
                {
                    "name": name,
                    "latitude_deg": lat,
                    "longitude_deg": lon,
                    "airport_id": name if name in {"RJFO", "RJFM"} else None,
                }
                for name, lat, lon in points
            ]
        confirmed = json.loads(local.dispatch("confirmRoute", request))
        p = confirmed["project"]
        assert len(p["route_nodes"]) == 5
        nodes = p["route_nodes"]
        assert (nodes[2]["latitude_deg"], nodes[2]["longitude_deg"]) == umk
        assert (nodes[3]["latitude_deg"], nodes[3]["longitude_deg"]) == arita
        assert nodes[3]["role"] == "VISUAL_REPORTING_POINT"
        if mode == "line":
            assert nodes[1]["name"] == "小丸"
            if explicit_umk:
                assert nodes[2]["name"] == "手動UMK"
            if via_arita:
                assert nodes[3]["name"] == "有田"
        update = {
            key: INPUT[key]
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
            visual_reporting_point_node_id=nodes[3]["id"],
            descent_rate_fpm=rate,
        )
        if via_arita and explicit_umk:
            update.update(
                arrival_altitude_mode="MANUAL_NON_STANDARD_ENTRY",
                manual_vrep_altitude_ft_msl=2500,
                manual_vrep_reason="retained manual input",
            )
        local.dispatch("updateProject", update)
        calculated = json.loads(local.dispatch("calculate"))
        assert calculated["readiness"]["calculationIsCurrent"]
        outcome = CalculationOutcome.model_validate_json(json.dumps(calculated["outcome"]))
        if via_arita and explicit_umk:
            assert (
                calculated["project"]["metadata"]["ui_state"]["arrival_plan"][
                    "manual_vrep_altitude_ft_msl"
                ]
                == 2500
            )
            assert outcome.arrival_altitude.adopted_altitude_ft_msl == 1500
        descent = [
            z
            for z in outcome.sections
            if z.performance_metadata.get("type") == "rjfm_inbound_operational_descent"
        ]
        assert sum(z.zone_ete_seconds.adopted() for z in descent) == pytest.approx(
            360 if rate == 500 else 180
        )
        assert sum(z.section_fuel_gal.adopted() for z in descent) == pytest.approx(
            1.2 if rate == 500 else 0.6
        )
        assert all(z.performance_metadata["target_altitude_ft_msl"] == 1500 for z in descent)
        assert all(z.performance_metadata["deceleration_duration_seconds"] == 0 for z in descent)
        assert sum(
            z.zone_distance_nm.adopted() / z.ground_speed_kt.adopted() * 3600 for z in descent
        ) != pytest.approx(360 if rate == 500 else 180)
        parent = next(
            r
            for r in outcome.display_rows
            if r.row_type == "PHYSICAL_LEG_SUMMARY" and str(r.from_node_id) == nodes[1]["id"]
        )
        assert str(parent.to_node_id) == nodes[-1]["id"]
        children = [
            r
            for r in outcome.display_rows
            if r.row_type == "CALCULATION_ZONE"
            and str(r.section_id) in {s["id"] for s in p["sections"][1:]}
        ]
        assert len(children) == 3
        for child, start, end in zip(children, nodes[1:-1], nodes[2:], strict=True):
            geometry = geodesic_leg(
                start["latitude_deg"],
                start["longitude_deg"],
                end["latitude_deg"],
                end["longitude_deg"],
            )
            zone = next(z for z in outcome.sections if z.sequence == child.source_result_sequence)
            assert zone.zone_distance_nm.adopted() == pytest.approx(geometry.distance_nm)
            assert child.tc.effective_value == pytest.approx(geometry.initial_true_course_deg)
            assert child.mc.effective_value == pytest.approx(zone.magnetic_course_deg.adopted())
        for field in ("distance", "ete", "fuel"):
            assert float(getattr(parent, field).text.split(" / ")[0]) == pytest.approx(
                sum(float(getattr(c, field).text) for c in children)
            )
        assert children[1].pa.effective_value == 1500
        info = [r for r in outcome.display_rows if r.row_type == "DESTINATION_INFO"]
        assert len(info) == 1
        assert info[0].to_name == "RJFM"
        assert info[0].pa.effective_value == children[-1].pa.effective_value
        assert info[0].toat.effective_value is not None
        restored = json.loads(local.dispatch("bootstrap", calculated["workingRecovery"]))
        assert restored["readiness"]["calculationIsCurrent"]
        assert restored["project"]["route_nodes"] == calculated["project"]["route_nodes"]
        assert restored["outcome"] == calculated["outcome"]
        local.dispatch("updateProject", {**update, "total_usable_fuel_gal": 89})
        recalculated = json.loads(local.dispatch("calculate"))
        assert recalculated["readiness"]["calculationIsCurrent"]
        assert len(recalculated["project"]["route_nodes"]) == 5
    finally:
        local.close()


def test_explicit_arita_legacy_seven_minute_result_becomes_stale(monkeypatch):
    from unittest.mock import patch

    from autonavlog.domain.planning import RjfmInboundPlan

    monkeypatch.setattr("autonavlog.web.facade.build_rjfm_inbound_guidance", lambda *a, **k: None)
    local = LocalApplication(ROOT / "data")
    try:
        # Reproduce the previously valid explicit-UMK/ARITA V1 calculation.
        old = "RJFM_INBOUND_OMARU_UMK_VREP_V1"
        with (
            patch("autonavlog.application.project_fingerprints.RJFM_INBOUND_RULE_VERSION", old),
            patch(
                "autonavlog.application.rjfm_inbound_plan.RjfmInboundPlan",
                side_effect=lambda **kw: RjfmInboundPlan(rule_version=old, **kw),
            ),
            patch("autonavlog.application.calculation_service.is_arita_node", return_value=False),
        ):
            INBOUND_KML = (
                (ROOT / "tests/fixtures/issue_125_inbound.kml")
                .read_text()
                .replace(
                    "131.48740082335098,31.786185910330968", "131.3535165268158,31.94977931375621"
                )
                .replace("SHIRAHAMA", "ARITA")
            )
            local.dispatch("importRoute", {"filename": "old.kml", "kml_text": INBOUND_KML})
            state = json.loads(local.dispatch("confirmRoute", INPUT))
            update = {
                key: INPUT[key]
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
                visual_reporting_point_node_id=state["project"]["route_nodes"][-2]["id"],
            )
            local.dispatch("updateProject", update)
            saved = json.loads(local.dispatch("calculate"))
            assert saved["readiness"]["calculationIsCurrent"]
            old_descent = [
                z
                for z in saved["outcome"]["sections"]
                if z["performance_metadata"].get("type") == "rjfm_inbound_operational_descent"
            ]
            assert old_descent[0]["performance_metadata"]["planned_duration_seconds"] == 420
        restored = json.loads(local.dispatch("bootstrap", saved["workingRecovery"]))
        assert not restored["readiness"]["calculationIsCurrent"]
        assert restored["outcome"]["sections"] == saved["outcome"]["sections"]
        current = json.loads(local.dispatch("calculate"))
        assert current["readiness"]["calculationIsCurrent"]
        descent = [
            z
            for z in current["outcome"]["sections"]
            if z["performance_metadata"].get("type") == "rjfm_inbound_operational_descent"
        ]
        assert descent[0]["performance_metadata"]["planned_duration_seconds"] == 360
    finally:
        local.close()
