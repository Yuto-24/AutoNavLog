from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from autonavlog.domain.calculation import RjfmInboundGuidance
from autonavlog.web.app import create_app
from autonavlog.web.runtime import WebRuntimeConfig

ROOT = Path(__file__).resolve().parents[2]


def _synthetic_available_inbound_guidance(*_args: object, **_kwargs: object) -> RjfmInboundGuidance:
    """API contract fixture; production still loads the unavailable signed pack."""

    return RjfmInboundGuidance(
        status="AVAILABLE",
        message="Synthetic west-extension guidance.",
        generated_against_fingerprint="a" * 64,
        reference_revision="synthetic-primary-v1",
        reference_content_fingerprint="b" * 64,
        raw_turn_point={"latitude_deg": 32.0, "longitude_deg": 131.0},
        rounded_turn_point={"latitude_deg": 32.01, "longitude_deg": 131.01},
        bearing_magnetic_deg=270.0,
        actual_bearing_magnetic_deg=270.1,
        raw_extra_distance_nm=5.1,
        extra_distance_nm=5.5,
        raw_predicted_ete_min=8.2,
        predicted_ete_min=8.4,
        raw_dme_nm=12.2,
        rounded_dme_nm=12.5,
        raw_turn_altitude_ft_msl=3200.0,
        rounded_turn_altitude_ft_msl=3190.0,
        raw_minimum_boundary_clearance_nm=1.2,
        minimum_boundary_clearance_nm=1.1,
    )


INBOUND_KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>RJFO→RJFM inbound</name><LineString><coordinates>
    131.7371201529664,33.47949406702627,0
    131.47036916946163,32.16255070087476,0
    131.42429852046251,31.985137767624444,0
    131.3535165268158,31.94977931375614,0
    131.4484517490447,31.87712141761355,0
  </coordinates></LineString></Placemark>
</Document></kml>"""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _wait_for_calculation(client: httpx.AsyncClient) -> dict[str, Any]:
    created = await client.post("/api/calculation-jobs")
    assert created.status_code == 202, created.text
    job = created.json()
    for _ in range(100):
        response = await client.get(f"/api/calculation-jobs/{job['job_id']}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.01)
    assert job["status"] == "succeeded", job
    return cast(dict[str, Any], job["state"])


@pytest.mark.anyio
async def test_real_inbound_route_calculation_and_project_roundtrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autonavlog.web.facade.build_rjfm_inbound_guidance",
        _synthetic_available_inbound_guidance,
    )
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        session = await client.post("/api/session")
        assert session.status_code == 200, session.text

        imported = await client.post(
            "/api/import",
            json={"filename": "rjfo-rjfm-inbound.kml", "kml_text": INBOUND_KML},
        )
        assert imported.status_code == 200, imported.text
        candidate = imported.json()["import"]["candidates"][0]
        assert candidate["departureAirportId"] == "RJFO"
        assert candidate["destinationAirportId"] == "RJFM"

        confirmed = await client.post(
            "/api/route/confirm",
            json={
                "candidate_kind": "line",
                "candidate_index": 0,
                "route_use_confirmed": True,
                "flight_date": "2026-08-31",
                "departure_time_jst": "09:00",
                "total_usable_fuel_gal": 90,
                "default_variation_deg_east": 8,
                "weather_mode": "FTD",
                "ftd_weather": {
                    "surface_wind": {"direction_deg_from": 360, "speed_kt": 15},
                    "wind_at_5000_ft": {"direction_deg_from": 270, "speed_kt": 30},
                },
                "all_leg_altitude_ft_msl": 5000,
                "defaults_confirmed": True,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        confirmed_state = confirmed.json()
        project = confirmed_state["project"]
        nodes = sorted(project["route_nodes"], key=lambda node: node["sequence"])
        assert [node["name"] for node in nodes] == ["RJFO", "OMARU", "UMK", "WP4", "RJFM"]
        assert [node["role"] for node in nodes] == [
            "AIRPORT",
            "ROUTE_POINT",
            "ROUTE_POINT",
            "VISUAL_REPORTING_POINT",
            "DESTINATION",
        ]
        physical_node_ids = [node["id"] for node in nodes]
        inbound = next(
            section
            for section in project["sections"]
            if section["from_node_id"] == physical_node_ids[1]
            and section["to_node_id"] == physical_node_ids[2]
        )
        assert inbound["planned_altitude_ft_msl"] == 4500
        fixed = next(
            item
            for item in confirmed_state["altitudeGuidance"]["sections"]
            if item["sectionId"] == inbound["id"]
        )
        assert fixed["inputMode"] == "RJFM_INBOUND_OMARU_TO_UMK_FIXED"
        assert fixed["fixedAltitudeFtMsl"] == 4500
        assert project["weather_mode"] == "FTD"
        assert project["ftd_weather"] == {
            "surface_wind": {"direction_deg_from": 360, "speed_kt": 15},
            "wind_at_5000_ft": {"direction_deg_from": 270, "speed_kt": 30},
        }

        calculated = await _wait_for_calculation(client)
        calculated_project = calculated["project"]
        calculated_nodes = sorted(
            calculated_project["route_nodes"], key=lambda node: node["sequence"]
        )
        assert [node["id"] for node in calculated_nodes] == physical_node_ids
        assert len(calculated_nodes) == 5
        calculated_inbound = next(
            section for section in calculated_project["sections"] if section["id"] == inbound["id"]
        )
        assert calculated_inbound["planned_altitude_ft_msl"] == 4500
        navlog_inbound = next(
            row
            for row in calculated["outcome"]["display_rows"]
            if row["row_type"] == "PHYSICAL_LEG_SUMMARY"
            and row["from_node_id"] == physical_node_ids[1]
            and row["to_node_id"] == physical_node_ids[2]
        )
        assert navlog_inbound["pa"]["text"] == "4500"
        inbound_guidance = calculated["outcome"]["rjfm_inbound_guidance"]
        assert inbound_guidance is not None
        assert inbound_guidance["status"] == "AVAILABLE"
        assert inbound_guidance["rounded_dme_nm"] == 12.5
        assert inbound_guidance["reference_revision"] == "synthetic-primary-v1"
        eoc = [point for point in calculated["outcome"]["derived_points"] if point["type"] == "EOC"]
        assert len(eoc) == 1
        assert eoc[0]["latitude_deg"] == pytest.approx(31.985137767624444)
        assert eoc[0]["longitude_deg"] == pytest.approx(131.42429852046251)

        ui_state = calculated_project["metadata"]["ui_state"]
        assert ui_state["state_schema_version"] == 7
        assert ui_state["rjfm_inbound_plan"]["controlled_section_ids"] == [inbound["id"]]
        plan = ui_state["rjfm_inbound_plan"]
        assert plan["omaru_node_id"] == physical_node_ids[1]
        assert plan["umk_node_id"] == physical_node_ids[2]

        saved = await client.post("/api/projects/save", json={"name": "RJFM inbound FTD"})
        assert saved.status_code == 200, saved.text
        saved_project = saved.json()["project"]
        assert saved_project["weather_mode"] == "FTD"
        loaded = await client.post("/api/projects/load", json={"project_id": saved_project["id"]})
        assert loaded.status_code == 200, loaded.text
        loaded_state = loaded.json()
        loaded_project = loaded_state["project"]
        assert loaded_state["outcome"] is None
        assert loaded_project["weather_mode"] == "FTD"
        assert loaded_project["ftd_weather"] == calculated_project["ftd_weather"]
        assert loaded_project["metadata"]["ui_state"]["state_schema_version"] == 7
        assert loaded_project["metadata"]["ui_state"]["rjfm_inbound_plan"] == plan

        changed_sections = [
            {
                "section_id": section["id"],
                "planned_altitude_ft_msl": section["planned_altitude_ft_msl"],
                "phase": section["phase"],
            }
            for section in loaded_project["sections"]
        ]
        changed = await client.put(
            "/api/project",
            json={
                "flight_date": loaded_project["flight_date"],
                "departure_time_jst": "09:00",
                "total_usable_fuel_gal": 89,
                "default_variation_deg_east": loaded_project["default_variation_deg_east"],
                "weather_mode": "FTD",
                "ftd_weather": loaded_project["ftd_weather"],
                "tgl_count": loaded_project["tgl_count"],
                "sections": changed_sections,
            },
        )
        assert changed.status_code == 200, changed.text
        changed_state = changed.json()
        assert changed_state["outcome"] is None
        assert not changed_state["readiness"]["calculationIsCurrent"]
        assert changed_state["project"]["metadata"]["ui_state"]["state_schema_version"] == 7
        assert changed_state["project"]["metadata"]["ui_state"]["rjfm_inbound_plan"] == plan


@pytest.mark.anyio
async def test_production_reference_returns_nonblocking_unavailable_guidance(
    tmp_path: Path,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "production-storage",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200
        imported = await client.post(
            "/api/import",
            json={"filename": "rjfo-rjfm-inbound.kml", "kml_text": INBOUND_KML},
        )
        assert imported.status_code == 200, imported.text
        confirmed = await client.post(
            "/api/route/confirm",
            json={
                "candidate_kind": "line",
                "candidate_index": 0,
                "route_use_confirmed": True,
                "flight_date": "2026-08-31",
                "departure_time_jst": "09:00",
                "total_usable_fuel_gal": 90,
                "default_variation_deg_east": 8,
                "weather_mode": "FTD",
                "ftd_weather": {
                    "surface_wind": {"direction_deg_from": 360, "speed_kt": 15},
                    "wind_at_5000_ft": {"direction_deg_from": 270, "speed_kt": 30},
                },
                "all_leg_altitude_ft_msl": 5000,
                "defaults_confirmed": True,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        calculated = await _wait_for_calculation(client)
        outcome = calculated["outcome"]
        assert outcome["rjfm_inbound_guidance"] == {
            "status": "UNAVAILABLE",
            "reason_code": "KS43_HORIZONTAL_BOUNDARY_UNVERIFIED",
            "message": (
                "KS4-3 horizontal boundary has no verified, versioned solver reference; "
                "west-extension guidance is unavailable."
            ),
            "generated_against_fingerprint": outcome["rjfm_inbound_guidance"][
                "generated_against_fingerprint"
            ],
            "reference_revision": "2026-08-31-rjfm-inbound-west-guidance-v1",
            "reference_content_fingerprint": outcome["rjfm_inbound_guidance"][
                "reference_content_fingerprint"
            ],
            "raw_turn_point": None,
            "rounded_turn_point": None,
            "bearing_magnetic_deg": None,
            "actual_bearing_magnetic_deg": None,
            "raw_extra_distance_nm": None,
            "extra_distance_nm": None,
            "raw_predicted_ete_min": None,
            "predicted_ete_min": None,
            "raw_dme_nm": None,
            "rounded_dme_nm": None,
            "raw_turn_altitude_ft_msl": None,
            "rounded_turn_altitude_ft_msl": None,
            "raw_minimum_boundary_clearance_nm": None,
            "minimum_boundary_clearance_nm": None,
        }
        assert outcome["blockers"] == [] if "blockers" in outcome else True
