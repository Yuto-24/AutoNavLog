from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from autonavlog.nav.rounding import round_half_up
from autonavlog.version import __version__
from autonavlog.web.app import create_app
from autonavlog.web.calculation_jobs import (
    CalculationJob,
    CalculationJobAlreadyActiveError,
)
from autonavlog.web.cloudflare_access import CloudflareAccessVerificationError
from autonavlog.web.runtime import WebRuntimeConfig

ROOT = Path(__file__).resolve().parents[2]

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>RJFM-RJFO</name>
      <LineString>
        <coordinates>
          131.4486111111,31.8772222222,0
          131.5000000000,32.4000000000,0
          131.6500000000,33.1000000000,0
          131.7000000000,33.4000000000,0
          131.7372222222,33.4794444444,0
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""

KML_FROM_RJFK = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>RJFK-RJFO</name>
      <LineString>
        <coordinates>
          130.7194444444,31.8033333333,0
          131.0000000000,32.4000000000,0
          131.4000000000,33.1000000000,0
          131.7372222222,33.4794444444,0
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""


KML_TO_RJFK = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>RJFM-RJFK</name>
      <LineString>
        <coordinates>
          131.4486111111,31.8772222222,0
          130.9000000000,31.8400000000,0
          130.7600000000,31.8200000000,0
          130.7194444444,31.8033333333,0
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""

class StubAccessVerifier:
    def __init__(self, identities: dict[str, str]) -> None:
        self.identities = identities

    def verify_email(self, assertion: str) -> str:
        try:
            return self.identities[assertion]
        except KeyError as error:
            raise CloudflareAccessVerificationError("invalid test assertion") from error


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _calculate(client: httpx.AsyncClient) -> dict[str, object]:
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
    return job["state"]


@pytest.mark.anyio
async def test_trusted_http_session_cookie_is_reusable(tmp_path: Path) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            trusted_local_identity="local-test-user",
            session_cookie_secure=False,
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://192.0.2.10",
    ) as client:
        created = await client.post("/api/session")
        assert created.status_code == 200
        cookie = created.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "Secure" not in cookie
        assert "SameSite=strict" in cookie

        assert (await client.get("/api/state")).status_code == 200


@pytest.mark.anyio
async def test_web_route_calculation_save_and_fail_closed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.get("/healthz")).status_code == 200
        root_response = await client.get("/")
        assert root_response.status_code == 503
        assert root_response.headers["cache-control"] == "no-cache"

        created = await client.post("/api/session")
        assert created.status_code == 200
        assert created.headers["cache-control"] == "no-store"
        assert created.headers["referrer-policy"] == "no-referrer"
        content_security_policy = created.headers["content-security-policy"]
        assert "connect-src 'self' https://maps.gsi.go.jp" in content_security_policy
        assert "https://maps.gsi.go.jp" not in content_security_policy.split("img-src", 1)[1].split(
            ";", 1
        )[0]
        assert set(created.json()) == {"state"}
        assert created.json()["state"]["runtime"]["appVersion"] == __version__
        destination = next(
            airport for airport in created.json()["state"]["airports"] if airport["id"] == "RJFO"
        )
        assert destination["elevationFtMsl"] == 17
        assert destination["patternAltitudeFtMsl"] == 1000
        assert destination["patternAltitudeValidationStatus"] == "VERIFIED"
        cookie = created.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=strict" in cookie

        imported = await client.post(
            "/api/import",
            json={"filename": "route.kml", "kml_text": KML},
        )
        assert imported.status_code == 200
        candidate = imported.json()["import"]["candidates"][0]
        assert candidate["name"] == "RJFM-RJFO"
        assert candidate["departureAirportId"] == "RJFM"
        assert candidate["destinationAirportId"] == "RJFO"
        assert candidate["departureDistanceNm"] <= 5
        assert candidate["destinationDistanceNm"] <= 5

        confirmed = await client.post(
            "/api/route/confirm",
            json={
                "candidate_kind": "line",
                "candidate_index": 0,
                "route_use_confirmed": True,
                "flight_date": "2026-08-10",
                "departure_time_jst": "09:00",
                "total_usable_fuel_gal": 90,
                "default_variation_deg_east": 8,
                "all_leg_altitude_ft_msl": 3000,
                "defaults_confirmed": True,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        confirmed_state = confirmed.json()
        assert len(confirmed_state["project"]["route_nodes"]) == 5
        assert confirmed_state["project"]["route_nodes"][-2]["role"] == ("VISUAL_REPORTING_POINT")
        assert confirmed_state["project"]["sections"][-1]["planned_altitude_ft_msl"] == 1500
        guidance_variations = [
            item["variationDegEast"] for item in confirmed_state["altitudeGuidance"]["sections"]
        ]
        assert guidance_variations == [7.0, 8.0, 8.0, 8.0]
        for item in confirmed_state["altitudeGuidance"]["sections"]:
            assert item["vfrCruisingAltitudeMagneticCourseDeg"] == (
                round_half_up(item["magneticCourseDeg"], 1.0) % 360.0
            )
        arrival_plan = confirmed_state["project"]["metadata"]["ui_state"]["arrival_plan"]
        assert arrival_plan["selected_pattern_altitude_ft_msl"] == 1000
        assert arrival_plan["selected_pattern_altitude_source"] == "AUTOMATIC"
        assert confirmed_state["project"]["sections"][-1]["phase"] == "VISUAL_ARRIVAL"
        assert confirmed_state["project"]["sections"][-2]["phase"] == "DESCENT"
        assert all(
            issue["code"] != "PATTERN_ALTITUDE_REQUIRED"
            for issue in confirmed_state["readiness"]["issues"]
        )

        removed_calculate = await client.post("/api/calculate")
        assert removed_calculate.status_code == 405

        manual_arrival = await client.put(
            "/api/project",
            json={
                "flight_date": "2026-08-10",
                "departure_time_jst": "09:00",
                "total_usable_fuel_gal": 90,
                "default_variation_deg_east": 8,
                "tgl_count": 0,
                "sections": [
                    {
                        "section_id": section["id"],
                        "planned_altitude_ft_msl": section["planned_altitude_ft_msl"],
                        "phase": section["phase"],
                        "manual_wind_by_phase": (
                            {"CRUISE": {"direction_deg_from": 222, "speed_kt": 22}}
                            if section["id"] == confirmed_state["project"]["sections"][0]["id"]
                            else {}
                        ),
                        "manual_temperature_c_by_phase": (
                            {"CRUISE": 9}
                            if section["id"] == confirmed_state["project"]["sections"][0]["id"]
                            else {}
                        ),
                    }
                    for section in confirmed_state["project"]["sections"]
                ],
                "visual_reporting_point_node_id": confirmed_state["project"]["route_nodes"][-2][
                    "id"
                ],
                "selected_pattern_altitude_ft_msl": 1300,
                "arrival_altitude_mode": "MANUAL_NON_STANDARD_ENTRY",
                "manual_vrep_altitude_ft_msl": 2100,
                "manual_vrep_reason": "Direct Base training entry",
            },
        )
        assert manual_arrival.status_code == 200, manual_arrival.text

        destination_state = manual_arrival.json()
        arrival_plan = destination_state["project"]["metadata"]["ui_state"]["arrival_plan"]
        assert arrival_plan["selected_pattern_altitude_ft_msl"] == 1300
        assert arrival_plan["selected_pattern_altitude_source"] == "MANUAL"
        assert arrival_plan["altitude_mode"] == "MANUAL_NON_STANDARD_ENTRY"
        assert arrival_plan["manual_vrep_altitude_ft_msl"] == 2100
        assert destination_state["project"]["sections"][-1]["planned_altitude_ft_msl"] == 2100
        assert all(
            issue["code"] != "PATTERN_ALTITUDE_REQUIRED"
            for issue in destination_state["readiness"]["issues"]
        )

        created_job = await client.post("/api/calculation-jobs")
        assert created_job.status_code == 202, created_job.text
        job = created_job.json()
        assert job["status"] in {"queued", "preparing_weather", "calculating", "succeeded"}
        assert 0 <= job["progress_percent"] <= 100
        assert job["progress_message"]
        for _ in range(100):
            job_response = await client.get(f"/api/calculation-jobs/{job['job_id']}")
            assert job_response.status_code == 200, job_response.text
            job = job_response.json()
            assert 0 <= job["progress_percent"] <= 100
            assert job["progress_message"]
            if job["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert job["status"] == "succeeded", job
        assert job["progress_percent"] == 100
        calculated_state = job["state"]
        assert calculated_state["outcome"] is not None
        assert calculated_state["destinationWind"]["availability"] == "AVAILABLE"
        assert calculated_state["destinationWind"]["wind_direction_deg_from"] == 200
        assert calculated_state["destinationWind"]["wind_speed_kt"] == 8
        assert calculated_state["outcome"]["arrival_altitude"]["base_vrep_altitude_ft_msl"] == 1800
        assert calculated_state["outcome"]["arrival_altitude"]["adopted_altitude_ft_msl"] == 2100
        variations = [
            section["variation_deg_east"] for section in calculated_state["outcome"]["sections"]
        ]
        assert {item["automatic_value"] for item in variations} == {7.0, 8.0}
        assert all(item["adopted_source"] == "AUTOMATIC" for item in variations)
        assert all(
            item["automatic_metadata"]["rule_version"] == "DEPARTURE_LATITUDE_32N_V1"
            for item in variations
        )
        assert all(
            issue["code"] != "PATTERN_ALTITUDE_REQUIRED"
            for issue in calculated_state["readiness"]["issues"]
        )
        assert any(
            issue["code"] == "DEVELOPMENT_WEATHER_PROVIDER"
            for issue in calculated_state["readiness"]["issues"]
        )
        assert "transferAidAllowed" not in calculated_state["readiness"]

        editable_sections = destination_state["project"]["sections"]
        first_editable_id = editable_sections[0]["id"]
        atomic_payload = {
            "flight_date": "2026-08-10",
            "departure_time_jst": "09:00",
            "total_usable_fuel_gal": 90,
            "default_variation_deg_east": 8,
            "tgl_count": 0,
            "sections": [
                {
                    "section_id": section["id"],
                    "planned_altitude_ft_msl": (
                        4500
                        if section["id"] == first_editable_id
                        else section["planned_altitude_ft_msl"]
                    ),
                    "phase": section["phase"],
                    "manual_wind_direction_deg": (
                        270 if section["id"] == first_editable_id else None
                    ),
                    "manual_wind_speed_kt": (15 if section["id"] == first_editable_id else None),
                    "manual_temperature_c": (12 if section["id"] == first_editable_id else None),
                    "manual_tas_kt": (155 if section["id"] == first_editable_id else None),
                }
                for section in editable_sections
            ],
            "visual_reporting_point_node_id": destination_state["project"]["route_nodes"][-2]["id"],
            "arrival_altitude_mode": "MANUAL_NON_STANDARD_ENTRY",
            "manual_vrep_altitude_ft_msl": 2100,
            "manual_vrep_reason": "Direct Base training entry",
        }
        recalculated = await client.post("/api/project/recalculate", json=atomic_payload)
        assert recalculated.status_code == 200, recalculated.text
        recalculated_state = recalculated.json()
        edited_project_section = next(
            section
            for section in recalculated_state["project"]["sections"]
            if section["id"] == first_editable_id
        )
        assert edited_project_section["planned_altitude_ft_msl"] == 4500
        assert edited_project_section["manual_wind_direction_deg"] == 270
        assert edited_project_section["manual_wind_speed_kt"] == 15
        assert edited_project_section["manual_temperature_c"] == 12
        assert edited_project_section["manual_temperature_c_by_phase"] == {"CRUISE": 9}
        assert edited_project_section["manual_wind_by_phase"] == {
            "CRUISE": {"direction_deg_from": 222, "speed_kt": 22}
        }
        assert edited_project_section["manual_tas_kt"] == 155
        edited_result = next(
            section
            for section in recalculated_state["outcome"]["sections"]
            if section["section_id"] == first_editable_id
        )
        for field in ("wind_direction_deg_from", "wind_speed_kt", "temperature_c", "tas_kt"):
            assert edited_result[field]["adopted_source"] == "MANUAL"
            assert edited_result[field]["manual_override"] is not None
        assert edited_result["temperature_c"]["automatic_value"] is not None
        assert edited_result["wind_speed_kt"]["automatic_value"] is not None

        last_good_project = recalculated_state["project"]
        last_good_outcome = recalculated_state["outcome"]
        web = app.state.web_application
        token = client.cookies.get("autonavlog_session")
        assert token is not None
        session = web.session(token, "local-test-user")

        def fail_calculation(*_args: object, **_kwargs: object) -> None:
            from autonavlog.web.facade import WebApplicationError

            raise WebApplicationError("TEST_CALCULATION_FAILED", "test calculation failure")

        monkeypatch.setattr(session.calculation_service, "calculate", fail_calculation)
        failed_payload = atomic_payload | {"total_usable_fuel_gal": 89}
        failed = await client.post("/api/project/recalculate", json=failed_payload)
        assert failed.status_code == 400
        assert failed.json()["error"]["code"] == "TEST_CALCULATION_FAILED"
        after_failure = (await client.get("/api/state")).json()
        assert after_failure["project"] == last_good_project
        assert after_failure["outcome"] == last_good_outcome

        blocked = await client.get("/api/transfer-aid")
        assert blocked.status_code == 404

        saved = await client.post(
            "/api/projects/save",
            json={"name": "web-smoke"},
        )
        assert saved.status_code == 200
        assert saved.json()["project"]["revision"] == 1


@pytest.mark.anyio
async def test_destination_change_resets_manual_pattern_to_new_master(
    tmp_path: Path,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200
        assert (
            await client.post(
                "/api/import",
                json={"filename": "rjfm-rjfo.kml", "kml_text": KML},
            )
        ).status_code == 200
        first = await client.post("/api/route/confirm", json=_route_payload())
        assert first.status_code == 200, first.text
        first_project = first.json()["project"]

        manual = await client.put(
            "/api/project",
            json={
                "flight_date": "2099-08-10",
                "departure_time_jst": "09:00",
                "total_usable_fuel_gal": 90,
                "default_variation_deg_east": 8,
                "visual_reporting_point_node_id": first_project["route_nodes"][-2]["id"],
                "selected_pattern_altitude_ft_msl": 1300,
                "arrival_altitude_mode": "MANUAL_NON_STANDARD_ENTRY",
                "manual_vrep_altitude_ft_msl": 2100,
                "manual_vrep_reason": "Direct Base training entry",
            },
        )
        assert manual.status_code == 200, manual.text
        assert (
            manual.json()["project"]["metadata"]["ui_state"]["arrival_plan"][
                "selected_pattern_altitude_source"
            ]
            == "MANUAL"
        )

        assert (
            await client.post(
                "/api/import",
                json={"filename": "rjfm-rjfk.kml", "kml_text": KML_TO_RJFK},
            )
        ).status_code == 200
        changed = await client.post("/api/route/confirm", json=_route_payload())
        assert changed.status_code == 200, changed.text
        changed_state = changed.json()
        assert changed_state["project"]["destination_airport_id"] == "RJFK"
        arrival_plan = changed_state["project"]["metadata"]["ui_state"]["arrival_plan"]
        assert arrival_plan["selected_pattern_altitude_ft_msl"] == 1900
        assert arrival_plan["selected_pattern_altitude_source"] == "AUTOMATIC"
        assert arrival_plan["altitude_mode"] == "STANDARD_DISTANCE_RULE"
        assert arrival_plan["manual_vrep_altitude_ft_msl"] is None
        assert changed_state["project"]["sections"][-2]["phase"] == "DESCENT"
        assert changed_state["project"]["sections"][-1]["phase"] == "VISUAL_ARRIVAL"

@pytest.mark.anyio
async def test_route_vrep_altitude_edit_drives_calculation_and_survives_reload(
    tmp_path: Path,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200
        imported = await client.post(
            "/api/import",
            json={"filename": "route.kml", "kml_text": KML},
        )
        assert imported.status_code == 200, imported.text
        confirmed = await client.post(
            "/api/route/confirm",
            json={
                **_route_payload(),
                "all_leg_altitude_ft_msl": 3000,
            },
        )
        assert confirmed.status_code == 200, confirmed.text

        automatic_state = await _calculate(client)
        automatic_plan = automatic_state["project"]["metadata"]["ui_state"]["arrival_plan"]
        assert automatic_plan["altitude_mode"] == "STANDARD_DISTANCE_RULE"
        assert automatic_plan["manual_vrep_altitude_ft_msl"] is None
        assert automatic_state["outcome"]["arrival_altitude"]["adopted_altitude_ft_msl"] == 1500
        assert automatic_state["outcome"]["arrival_altitude"]["adopted_source"] == "AUTOMATIC"

        project = automatic_state["project"]
        visual_section_id = project["sections"][-1]["id"]
        edited = await client.put(
            "/api/project",
            json={
                "flight_date": project["flight_date"],
                "departure_time_jst": "09:00",
                "total_usable_fuel_gal": project["total_usable_fuel_gal"],
                "default_variation_deg_east": project["default_variation_deg_east"],
                "weather_mode": project["weather_mode"],
                "run_up_included": project["run_up_included"],
                "nose_fairing_enabled": project["nose_fairing_enabled"],
                "air_conditioning_enabled": project["air_conditioning_enabled"],
                "tgl_count": project["tgl_count"],
                "sections": [
                    {
                        "section_id": section["id"],
                        "planned_altitude_ft_msl": (
                            2100
                            if section["id"] == visual_section_id
                            else section["planned_altitude_ft_msl"]
                        ),
                        "phase": section["phase"],
                    }
                    for section in project["sections"]
                ],
                "visual_reporting_point_node_id": project["route_nodes"][-2]["id"],
                "arrival_altitude_mode": "STANDARD_DISTANCE_RULE",
                "manual_vrep_altitude_ft_msl": None,
                "manual_vrep_reason": None,
            },
        )
        assert edited.status_code == 200, edited.text
        edited_plan = edited.json()["project"]["metadata"]["ui_state"]["arrival_plan"]
        assert edited_plan["altitude_mode"] == "MANUAL_NON_STANDARD_ENTRY"
        assert edited_plan["manual_vrep_altitude_ft_msl"] == 2100
        assert edited_plan["manual_override_reason"] == "経路画面で指定したVREP計画高度"
        assert edited.json()["project"]["sections"][-1]["planned_altitude_ft_msl"] == 2100

        manual_state = await _calculate(client)
        manual_arrival = manual_state["outcome"]["arrival_altitude"]
        assert manual_arrival["adopted_altitude_ft_msl"] == 2100
        assert manual_arrival["adopted_source"] == "MANUAL"
        visual_result = next(
            section
            for section in manual_state["outcome"]["sections"]
            if section["phase"] == "VISUAL_ARRIVAL"
        )
        assert visual_result["planned_altitude_ft_msl"]["automatic_value"] == 2100
        automatic_eoc = next(
            point
            for point in automatic_state["outcome"]["derived_points"]
            if point["type"] == "EOC"
        )
        manual_eoc = next(
            point
            for point in manual_state["outcome"]["derived_points"]
            if point["type"] == "EOC"
        )
        assert manual_eoc["along_route_distance_nm"] > automatic_eoc["along_route_distance_nm"]
        assert manual_state["outcome"]["sections"][-1]["cumulative_ete_seconds"] != (
            automatic_state["outcome"]["sections"][-1]["cumulative_ete_seconds"]
        )
        assert manual_state["outcome"]["sections"][-1]["remaining_fuel_gal"] != (
            automatic_state["outcome"]["sections"][-1]["remaining_fuel_gal"]
        )

        saved = await client.post("/api/projects/save", json={"name": "manual-vrep"})
        assert saved.status_code == 200, saved.text
        loaded = await client.post(
            "/api/projects/load",
            json={"project_id": saved.json()["project"]["id"]},
        )
        assert loaded.status_code == 200, loaded.text
        loaded_plan = loaded.json()["project"]["metadata"]["ui_state"]["arrival_plan"]
        assert loaded_plan["manual_vrep_altitude_ft_msl"] == 2100
        reloaded_state = await _calculate(client)
        assert reloaded_state["outcome"]["arrival_altitude"]["adopted_altitude_ft_msl"] == 2100


@pytest.mark.anyio
async def test_ftd_mode_calculates_with_fixed_wind_and_isa_without_fake_weather_blocker(
    tmp_path: Path,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200
        assert (
            await client.post(
                "/api/import",
                json={"filename": "route.kml", "kml_text": KML},
            )
        ).status_code == 200
        confirmed = await client.post(
            "/api/route/confirm",
            json=_route_payload()
            | {
                "weather_mode": "FTD",
                "ftd_weather": {
                    "surface_wind": {"direction_deg_from": 180, "speed_kt": 5},
                    "wind_at_5000_ft": {"direction_deg_from": 270, "speed_kt": 20},
                },
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        state = confirmed.json()
        assert state["project"]["weather_mode"] == "FTD"
        assert state["project"]["ftd_weather"]["wind_at_5000_ft"]["speed_kt"] == 20

        calculated_state = await _calculate(client)
        assert calculated_state["project"]["selected_forecast_run_id"] == "ftd-fixed-v1"
        assert calculated_state["destinationWind"]["reason_code"] == "FTD_MODE_NO_TAF"
        assert calculated_state["destinationWind"]["source_label"] == "FTD固定気象"
        assert all(
            issue["code"] != "DEVELOPMENT_WEATHER_PROVIDER"
            for issue in calculated_state["readiness"]["issues"]
        )
        automatic_metadata = [
            section["temperature_c"]["automatic_metadata"]
            for section in calculated_state["outcome"]["sections"]
            if section["temperature_c"]["automatic_value"] is not None
        ]
        assert automatic_metadata
        assert any(item.get("provider") == "ftd_fixed" for item in automatic_metadata)


@pytest.mark.anyio
async def test_checkpoint_crud_previews_projection_and_persists_on_saved_project(
    tmp_path: Path,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200
        assert (
            await client.post(
                "/api/import",
                json={"filename": "route.kml", "kml_text": KML},
            )
        ).status_code == 200
        confirmed = await client.post("/api/route/confirm", json=_route_payload())
        assert confirmed.status_code == 200, confirmed.text
        project = confirmed.json()["project"]
        section = project["sections"][0]
        assert section["phase"] == "CLIMB"
        start = next(
            node for node in project["route_nodes"] if node["id"] == section["from_node_id"]
        )
        end = next(node for node in project["route_nodes"] if node["id"] == section["to_node_id"])
        check_point = {
            "name": "訓練CP",
            "latitude_deg": (start["latitude_deg"] + end["latitude_deg"]) / 2 + 0.02,
            "longitude_deg": (start["longitude_deg"] + end["longitude_deg"]) / 2,
            "linked_section_id": section["id"],
        }

        created = await client.put(
            "/api/project/check-points",
            json={"check_points": [check_point]},
        )
        assert created.status_code == 200, created.text
        created_state = created.json()
        references = created_state["project"]["visual_references"]
        assert len(references) == 1
        assert references[0]["source"] == "WEB_MANUAL"
        assert len(created_state["checkPointPlanning"]["projections"]) == 1
        assert created_state["checkPointPlanning"]["issues"] == []
        check_point_id = references[0]["id"]

        renamed = await client.put(
            "/api/project/check-points",
            json={"check_points": [check_point | {"id": check_point_id, "name": "訓練CP改"}]},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["project"]["visual_references"][0]["name"] == "訓練CP改"

        saved = await client.post("/api/projects/save", json={"name": "cp-route"})
        assert saved.status_code == 200, saved.text
        project_id = saved.json()["project"]["id"]
        removed = await client.put(
            "/api/project/check-points",
            json={"check_points": []},
        )
        assert removed.status_code == 200
        assert removed.json()["project"]["visual_references"] == []

        loaded = await client.post(
            "/api/projects/load",
            json={"project_id": project_id},
        )
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["project"]["visual_references"][0]["name"] == "訓練CP改"
        assert len(loaded.json()["checkPointPlanning"]["projections"]) == 1


@pytest.mark.anyio
async def test_route_endpoints_are_server_resolved_and_manual_payload_is_rejected(
    tmp_path: Path,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200
        imported = await client.post(
            "/api/import",
            json={"filename": "rjfk-route.kml", "kml_text": KML_FROM_RJFK},
        )
        assert imported.status_code == 200

        manual = await client.post(
            "/api/route/confirm",
            json={
                "candidate_kind": "line",
                "candidate_index": 0,
                "route_use_confirmed": True,
                "flight_date": "2026-08-10",
                "departure_time_jst": "09:00",
                "departure_airport_id": "RJFK",
                "destination_airport_id": "RJFO",
                "total_usable_fuel_gal": 90,
                "default_variation_deg_east": 7,
                "all_leg_altitude_ft_msl": 3000,
            },
        )
        assert manual.status_code == 422
        payload = {
            "candidate_kind": "line",
            "candidate_index": 0,
            "route_use_confirmed": True,
            "flight_date": "2026-08-10",
            "departure_time_jst": "09:00",
            "total_usable_fuel_gal": 90,
            "default_variation_deg_east": 7,
            "all_leg_altitude_ft_msl": 3000,
        }
        confirmed = await client.post("/api/route/confirm", json=payload)
        assert confirmed.status_code == 200, confirmed.text
        project = confirmed.json()["project"]
        assert project["departure_airport_id"] == "RJFK"
        assert project["route_nodes"][0]["name"] == "RJFK"
        assert project["metadata"]["web_original_departure_coordinate"] == [
            31.8033333333,
            130.7194444444,
        ]

        removed_destination_confirm = await client.post(
            "/api/destination/confirm",
            json={"selected_pattern_altitude_ft_msl": 1000},
        )
        assert removed_destination_confirm.status_code == 405


@pytest.mark.anyio
async def test_web_session_and_upload_boundaries(tmp_path: Path) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        missing = await client.get("/api/state")
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "SESSION_REQUIRED"

        created = await client.post("/api/session")
        assert created.status_code == 200
        invalid = await client.post(
            "/api/import",
            json={"filename": "bad.kml", "content_base64": "%%%"},
        )
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "UPLOAD_ENCODING_INVALID"


def _route_payload() -> dict[str, object]:
    return {
        "candidate_kind": "line",
        "candidate_index": 0,
        "route_use_confirmed": True,
        "flight_date": "2099-08-10",
        "departure_time_jst": "09:00",
        "total_usable_fuel_gal": 90,
        "default_variation_deg_east": 8,
        "all_leg_altitude_ft_msl": 3500,
        "defaults_confirmed": True,
    }


async def _save_route(client: httpx.AsyncClient, name: str) -> str:
    assert (
        await client.post("/api/import", json={"filename": "route.kml", "kml_text": KML})
    ).status_code == 200
    confirmed = await client.post("/api/route/confirm", json=_route_payload())
    assert confirmed.status_code == 200, confirmed.text
    saved = await client.post("/api/projects/save", json={"name": name})
    assert saved.status_code == 200, saved.text
    return str(saved.json()["project"]["id"])


def _without_route_labels(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_route_labels(item)
            for key, item in value.items()
            if key not in {"from_name", "to_name"}
        }
    if isinstance(value, list):
        return [_without_route_labels(item) for item in value]
    return value


@pytest.mark.anyio
async def test_route_node_rename_preserves_current_calculation_and_persists(
    tmp_path: Path,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200
        assert (
            await client.post("/api/import", json={"filename": "route.kml", "kml_text": KML})
        ).status_code == 200
        confirmed = await client.post("/api/route/confirm", json=_route_payload())
        assert confirmed.status_code == 200, confirmed.text
        project = confirmed.json()["project"]
        calculated = await _calculate(client)
        before_outcome = calculated["outcome"]
        assert before_outcome is not None
        before_destination_wind = calculated["destinationWind"]
        editable = project["route_nodes"][1]

        blank = await client.put(
            f"/api/project/route-nodes/{editable['id']}/name",
            json={"name": "   "},
        )
        assert blank.status_code == 422
        airport = await client.put(
            f"/api/project/route-nodes/{project['route_nodes'][0]['id']}/name",
            json={"name": "Nope"},
        )
        assert airport.status_code == 409
        destination = await client.put(
            f"/api/project/route-nodes/{project['route_nodes'][-1]['id']}/name",
            json={"name": "Nope"},
        )
        assert destination.status_code == 409

        renamed = await client.put(
            f"/api/project/route-nodes/{editable['id']}/name",
            json={"name": "  北行き訓練点  "},
        )
        assert renamed.status_code == 200, renamed.text
        renamed_state = renamed.json()
        renamed_node = next(
            item for item in renamed_state["project"]["route_nodes"] if item["id"] == editable["id"]
        )
        assert renamed_node["name"] == "北行き訓練点"
        assert renamed_node["name_source"] == "USER"
        assert renamed_state["readiness"]["calculationIsCurrent"] is True
        assert renamed_state["destinationWind"] == before_destination_wind
        assert _without_route_labels(renamed_state["outcome"]) == _without_route_labels(
            before_outcome
        )
        assert any(
            row["to_node_id"] == editable["id"] and row["to_name"] == "北行き訓練点"
            for row in renamed_state["outcome"]["display_rows"]
        )

        saved = await client.post("/api/projects/save", json={})
        assert saved.status_code == 200, saved.text
        loaded = await client.post(
            "/api/projects/load",
            json={"project_id": saved.json()["project"]["id"]},
        )
        assert loaded.status_code == 200, loaded.text
        loaded_node = next(
            item for item in loaded.json()["project"]["route_nodes"] if item["id"] == editable["id"]
        )
        assert loaded_node["name"] == "北行き訓練点"
        assert loaded_node["name_source"] == "USER"


@pytest.mark.anyio
async def test_descent_rate_api_validation_and_saved_project_persistence(
    tmp_path: Path,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200
        assert (
            await client.post("/api/import", json={"filename": "route.kml", "kml_text": KML})
        ).status_code == 200

        invalid = await client.post(
            "/api/route/confirm",
            json=_route_payload() | {"descent_rate_fpm": 750},
        )
        assert invalid.status_code == 422

        confirmed = await client.post(
            "/api/route/confirm",
            json=_route_payload() | {"descent_rate_fpm": 1000},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["project"]["descent_rate_fpm"] == 1000

        saved = await client.post("/api/projects/save", json={"name": "1000-fpm"})
        assert saved.status_code == 200, saved.text
        project_id = saved.json()["project"]["id"]
        loaded = await client.post("/api/projects/load", json={"project_id": project_id})
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["project"]["descent_rate_fpm"] == 1000


@pytest.mark.anyio
async def test_cloudflare_assertion_authentication_and_session_rotation(
    tmp_path: Path,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
        )
    )
    app.state.web_application.access_verifier = StubAccessVerifier(
        {
            "pilot-token": "Pilot@Example.com",
            "unicode-token": "利用者@example.com",
        }
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        unauthenticated = await client.post("/api/session")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

        spoofed = await client.post(
            "/api/session",
            headers={"Cf-Access-Authenticated-User-Email": "Pilot@Example.com"},
        )
        assert spoofed.status_code == 401
        assert spoofed.json()["error"]["code"] == "AUTHENTICATION_INVALID"

        invalid = await client.post(
            "/api/session",
            headers={"Cf-Access-Jwt-Assertion": "invalid-token"},
        )
        assert invalid.status_code == 401
        assert invalid.json()["error"]["code"] == "AUTHENTICATION_INVALID"

        authenticated = await client.post(
            "/api/session",
            headers={"Cf-Access-Jwt-Assertion": "pilot-token"},
        )
        assert authenticated.status_code == 200
        assert "sessionToken" not in authenticated.json()
        old_token = client.cookies.get("autonavlog_session")
        assert old_token is not None

        refreshed = await client.post(
            "/api/session",
            headers={"Cf-Access-Jwt-Assertion": "pilot-token"},
        )
        assert refreshed.status_code == 200
        assert client.cookies.get("autonavlog_session") != old_token

        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://test",
            headers={
                "Cf-Access-Jwt-Assertion": "pilot-token",
                "cookie": f"autonavlog_session={old_token}",
            },
        ) as stale:
            expired = await stale.get("/api/state")
            assert expired.status_code == 401
            assert expired.json()["error"]["code"] == "SESSION_NOT_FOUND"

        unicode_identity = await client.post(
            "/api/session",
            headers={"Cf-Access-Jwt-Assertion": "unicode-token"},
        )
        assert unicode_identity.status_code == 200
        assert (
            await client.get(
                "/api/state",
                headers={"Cf-Access-Jwt-Assertion": "unicode-token"},
            )
        ).status_code == 200


@pytest.mark.anyio
async def test_upload_contract_invalid_cookie_and_unknown_api(tmp_path: Path) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200

        no_source = await client.post("/api/import", json={"filename": "route.kml"})
        assert no_source.status_code == 422
        both_sources = await client.post(
            "/api/import",
            json={
                "filename": "route.kml",
                "kml_text": KML,
                "content_base64": "AA==",
            },
        )
        assert both_sources.status_code == 422

        oversized = await client.post(
            "/api/import",
            json={
                "filename": "oversized.kmz",
                "content_base64": "A" * (14 * 1024 * 1024 + 1),
            },
        )
        assert oversized.status_code == 413
        assert oversized.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
        oversized_text = await client.post(
            "/api/import",
            json={
                "filename": "oversized.kml",
                "kml_text": "x" * (10 * 1024 * 1024 + 1),
            },
        )
        assert oversized_text.status_code == 422

        imported = await client.post(
            "/api/import",
            json={"filename": "route.kml", "kml_text": KML},
        )
        assert imported.status_code == 200
        missing_candidate = await client.post(
            "/api/route/confirm",
            json=_route_payload() | {"candidate_index": 999},
        )
        assert missing_candidate.status_code == 400
        assert missing_candidate.json()["error"]["code"] == "ROUTE_CANDIDATE_NOT_FOUND"

        negative_index = await client.post(
            "/api/route/confirm",
            json=_route_payload()
            | {
                "candidate_kind": "points",
                "point_indices": [-1, 0],
            },
        )
        assert negative_index.status_code == 422

        missing_api = await client.get("/api/not-defined")
        assert missing_api.status_code == 404
        assert missing_api.json()["error"]["code"] == "API_NOT_FOUND"

    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
        headers={"cookie": "autonavlog_session=not-a-session"},
    ) as invalid_client:
        invalid_session = await invalid_client.get("/api/state")
        assert invalid_session.status_code == 401
        assert invalid_session.json()["error"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.anyio
async def test_sessions_and_saved_projects_are_owner_isolated(tmp_path: Path) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
        )
    )
    app.state.web_application.access_verifier = StubAccessVerifier(
        {"alice-token": "Alice@Example.com", "bob-token": "bob@example.com"}
    )
    transport = httpx.ASGITransport(app=app)
    alice_headers = {"Cf-Access-Jwt-Assertion": "alice-token"}
    bob_headers = {"Cf-Access-Jwt-Assertion": "bob-token"}

    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
        headers=alice_headers,
    ) as alice:
        assert (await alice.post("/api/session")).status_code == 200
        alice_token = alice.cookies.get("autonavlog_session")
        assert alice_token is not None
        alice_project_id = await _save_route(alice, "alice-route")
        alice_state = (await alice.get("/api/state")).json()
        assert [item["name"] for item in alice_state["savedProjects"]] == ["alice-route"]
        assert "web_owner_id" not in alice_state["savedProjects"][0]

        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://test",
            headers=bob_headers,
        ) as intruder:
            mismatch = await intruder.get(
                "/api/state",
                headers={"cookie": f"autonavlog_session={alice_token}"},
            )
            assert mismatch.status_code == 401
            assert mismatch.json()["error"]["code"] == "SESSION_OWNER_MISMATCH"

        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://test",
            headers=bob_headers,
        ) as bob:
            assert (await bob.post("/api/session")).status_code == 200
            bob_state = (await bob.get("/api/state")).json()
            assert bob_state["savedProjects"] == []

            hidden = await bob.post(
                "/api/projects/load",
                json={"project_id": alice_project_id},
            )
            assert hidden.status_code == 404
            assert hidden.json()["error"]["code"] == "PROJECT_NOT_FOUND"

            forbidden_delete = await bob.delete(f"/api/projects/{alice_project_id}")
            assert forbidden_delete.status_code == 404
            assert forbidden_delete.json()["error"]["code"] == "PROJECT_NOT_FOUND"

            await _save_route(bob, "bob-route")
            bob_state = (await bob.get("/api/state")).json()
            assert [item["name"] for item in bob_state["savedProjects"]] == ["bob-route"]

        alice_state = (await alice.get("/api/state")).json()
        assert [item["name"] for item in alice_state["savedProjects"]] == ["alice-route"]

        deleted = await alice.delete(f"/api/projects/{alice_project_id}")
        assert deleted.status_code == 200
        assert deleted.json()["savedProjects"] == []
        assert deleted.json()["project"] is None


@pytest.mark.anyio
async def test_expired_saved_project_remains_listed_and_persisted(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=storage_root,
            weather_mode="fake",
        )
    )
    app.state.web_application.access_verifier = StubAccessVerifier(
        {"owner-token": "owner@example.com"}
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
        headers={"Cf-Access-Jwt-Assertion": "owner-token"},
    ) as client:
        assert (await client.post("/api/session")).status_code == 200
        assert (
            await client.post("/api/import", json={"filename": "route.kml", "kml_text": KML})
        ).status_code == 200
        confirmed = await client.post(
            "/api/route/confirm",
            json=_route_payload()
            | {
                "flight_date": "2000-01-01",
                "departure_time_jst": "09:00",
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        saved = await client.post("/api/projects/save", json={"name": "expired-route"})
        assert saved.status_code == 200, saved.text
        project_id = saved.json()["project"]["id"]

        state = await client.get("/api/state")
        assert state.status_code == 200, state.text
        assert [item["name"] for item in state.json()["savedProjects"]] == ["expired-route"]
        assert (storage_root / "projects" / project_id / "project.json").exists()


@pytest.mark.anyio
async def test_session_capacity_evicts_the_least_recent_session(tmp_path: Path) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            maximum_sessions=1,
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="https://test") as first,
        httpx.AsyncClient(transport=transport, base_url="https://test") as second,
    ):
        assert (await first.post("/api/session")).status_code == 200
        assert (await second.post("/api/session")).status_code == 200

        evicted = await first.get("/api/state")
        assert evicted.status_code == 401
        assert evicted.json()["error"]["code"] == "SESSION_NOT_FOUND"
        assert (await second.get("/api/state")).status_code == 200

        logged_out = await second.delete("/api/session")
        assert logged_out.status_code == 204
        assert (await second.get("/api/state")).status_code == 401


@pytest.mark.anyio
async def test_intermediate_line_names_preserve_every_original_coordinate(
    tmp_path: Path,
) -> None:
    named_kml = """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
    <Placemark><name>小丸～日振島～祝島～ゴルフコース</name>
    <LineString><coordinates>
      131.4488055215004,31.87716585260077,0
      131.4317398539069,31.98214589070221,0
      131.4734489929498,32.16275095638636,0
      132.2948734240414,33.1802236311398,0
      131.9894319344609,33.78695544494976,0
      131.67890296839,33.62999835453385,0
      131.7371811724867,33.47957070171427,0
    </coordinates></LineString></Placemark></Document></kml>"""
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200
        imported = await client.post(
            "/api/import",
            json={"filename": "named-route.kml", "kml_text": named_kml},
        )
        assert imported.status_code == 200

        confirmed = await client.post("/api/route/confirm", json=_route_payload())
        assert confirmed.status_code == 200, confirmed.text
        nodes = confirmed.json()["project"]["route_nodes"]
        names = [node["name"] for node in nodes]

        assert len(nodes) == 7
        assert names == [
            "RJFM",
            "UMK",
            "OMARU",
            "小丸",
            "日振島",
            "祝島",
            "RJFO",
        ]
        assert [node["name_source"] for node in nodes] == [
            "GENERATED",
            "GENERATED",
            "GENERATED",
            "IMPORTED",
            "IMPORTED",
            "IMPORTED",
            "GENERATED",
        ]
        assert [(node["latitude_deg"], node["longitude_deg"]) for node in nodes[1:-1]] == [
            (31.98214589070221, 131.4317398539069),
            (32.16275095638636, 131.4734489929498),
            (33.1802236311398, 132.2948734240414),
            (33.78695544494976, 131.9894319344609),
            (33.62999835453385, 131.67890296839),
        ]
        rjfm_plan = confirmed.json()["project"]["metadata"]["ui_state"][
            "rjfm_departure_plan"
        ]
        assert rjfm_plan["trigger"] == "UMK"
        assert rjfm_plan["main_route_mode"] == "UMK_PHYSICAL"
        assert rjfm_plan["umk"]["source"] == "KML:UMK"
        assert rjfm_plan["omaru"]["source"] == "KML:OMARU"
        first_altitudes = [
            section["planned_altitude_ft_msl"]
            for section in confirmed.json()["project"]["sections"][:2]
        ]
        assert first_altitudes == [
            5500,
            5500,
        ]

        fixed_section_ids = {
            item["sectionId"]: item["inputMode"]
            for item in confirmed.json()["altitudeGuidance"]["sections"]
        }
        assert fixed_section_ids[confirmed.json()["project"]["sections"][0]["id"]] == (
            "RJFM_DEPARTURE_TO_UMK_FIXED"
        )
        assert fixed_section_ids[confirmed.json()["project"]["sections"][1]["id"]] == (
            "RJFM_UMK_TO_OMARU_FIXED"
        )
        assert fixed_section_ids[confirmed.json()["project"]["sections"][2]["id"]] == (
            "EDITABLE"
        )

        submitted_sections = []
        for index, section in enumerate(confirmed.json()["project"]["sections"]):
            altitude = section["planned_altitude_ft_msl"]
            phase = section["phase"]
            if index == 0:
                altitude = 4200
                phase = "DESCENT"
            elif index == 1:
                altitude = 4300
                phase = "DESCENT"
            elif index == 2:
                altitude = 6400
                phase = "CRUISE"
            submitted_sections.append(
                {
                    "section_id": section["id"],
                    "planned_altitude_ft_msl": altitude,
                    "phase": phase,
                }
            )
        updated = await client.put(
            "/api/project",
            json={
                "flight_date": "2099-08-10",
                "departure_time_jst": "09:00",
                "total_usable_fuel_gal": 90,
                "default_variation_deg_east": 8,
                "tgl_count": 0,
                "sections": submitted_sections,
            },
        )
        assert updated.status_code == 200, updated.text
        updated_sections = updated.json()["project"]["sections"]
        assert [section["planned_altitude_ft_msl"] for section in updated_sections[:3]] == [
            5500,
            5500,
            6400,
        ]
        assert [section["phase"] for section in updated_sections[:3]] == [
            "CLIMB",
            "CRUISE",
            "CRUISE",
        ]

        calculated_state = await _calculate(client)
        assert not any(
            issue["code"] == "RJFM_DEPARTURE_PLAN_STALE"
            for issue in calculated_state["outcome"]["issues"]
        )
        guidance = calculated_state["outcome"]["rjfm_departure_guidance"]
        assert [candidate["runway"] for candidate in guidance["candidates"]] == [
            "09",
            "27",
        ]
        assert [point["source"] for point in guidance["center_route"]][:2] == [
            "KML:UMK",
            rjfm_plan["over_field"]["source"],
        ]
        assert calculated_state["project"]["metadata"]["ui_state"][
            "rjfm_departure_guidance"
        ] == guidance
        assert guidance["generated_against_fingerprint"] == calculated_state["project"][
            "metadata"
        ]["ui_state"]["calculated_against_fingerprint"]

        saved = await client.post("/api/projects/save", json={"name": "RJFM guidance"})
        assert saved.status_code == 200, saved.text
        loaded = await client.post(
            "/api/projects/load",
            json={"project_id": saved.json()["project"]["id"]},
        )
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["project"]["metadata"]["ui_state"][
            "rjfm_departure_guidance"
        ] == guidance


@pytest.mark.anyio
async def test_duplicate_calculation_job_returns_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )

    def reject_duplicate(**kwargs: object) -> None:
        raise CalculationJobAlreadyActiveError("session already has an active calculation job")

    monkeypatch.setattr(app.state.calculation_jobs, "submit", reject_duplicate)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200

        response = await client.post("/api/calculation-jobs")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CALCULATION_JOB_ALREADY_ACTIVE"


@pytest.mark.anyio
async def test_calculation_job_snapshot_prune_race_returns_minimal_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
            trusted_local_identity="local-test-user",
        )
    )
    submitted = CalculationJob(
        id="issued-job-id",
        owner_id="local-test-user",
        session_token="session-token",
    )
    monkeypatch.setattr(
        app.state.calculation_jobs,
        "submit",
        lambda **kwargs: submitted,
    )
    monkeypatch.setattr(
        app.state.calculation_jobs,
        "snapshot",
        lambda job_id, **kwargs: None,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        assert (await client.post("/api/session")).status_code == 200

        response = await client.post("/api/calculation-jobs")

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "issued-job-id",
        "status": "queued",
        "queue_position": None,
        "progress_percent": 0,
        "progress_message": "計算待ちです。",
        "created_at_utc": submitted.created_at_utc.isoformat(),
        "updated_at_utc": submitted.updated_at_utc.isoformat(),
    }
