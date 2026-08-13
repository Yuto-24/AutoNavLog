from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

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
async def test_web_route_calculation_save_and_fail_closed_output(
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
        assert (await client.get("/")).status_code == 503

        created = await client.post("/api/session")
        assert created.status_code == 200
        assert set(created.json()) == {"state"}
        destination = next(
            airport
            for airport in created.json()["state"]["airports"]
            if airport["id"] == "RJFO"
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
        assert imported.json()["import"]["candidates"][0]["name"] == "RJFM-RJFO"

        confirmed = await client.post(
            "/api/route/confirm",
            json={
                "candidate_kind": "line",
                "candidate_index": 0,
                "route_use_confirmed": True,
                "flight_date": "2026-08-10",
                "departure_time_jst": "09:00",
                "departure_airport_id": "RJFM",
                "destination_airport_id": "RJFO",
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
            item["variationDegEast"]
            for item in confirmed_state["altitudeGuidance"]["sections"]
        ]
        assert guidance_variations == [7.0, 8.0, 8.0, 8.0]
        assert any(
            issue["code"] == "PATTERN_ALTITUDE_REQUIRED"
            for issue in confirmed_state["readiness"]["issues"]
        )

        unconfirmed = await client.post("/api/calculate")
        assert unconfirmed.status_code == 409
        assert unconfirmed.json()["error"]["code"] == "PATTERN_ALTITUDE_REQUIRED"

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
                    }
                    for section in confirmed_state["project"]["sections"]
                ],
                "visual_reporting_point_node_id": confirmed_state["project"]["route_nodes"][-2][
                    "id"
                ],
                "arrival_altitude_mode": "MANUAL_NON_STANDARD_ENTRY",
                "manual_vrep_altitude_ft_msl": 2100,
                "manual_vrep_reason": "Direct Base training entry",
            },
        )
        assert manual_arrival.status_code == 200, manual_arrival.text

        destination_confirmed = await client.post(
            "/api/destination/confirm",
            json={
                "destination_airport_id": "RJFO",
                "selected_pattern_altitude_ft_msl": 1300,
            },
        )
        assert destination_confirmed.status_code == 200, destination_confirmed.text
        destination_state = destination_confirmed.json()
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
        for _ in range(100):
            job_response = await client.get(f"/api/calculation-jobs/{job['job_id']}")
            assert job_response.status_code == 200, job_response.text
            job = job_response.json()
            if job["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(0.01)
        assert job["status"] == "succeeded", job
        calculated_state = job["state"]
        assert calculated_state["outcome"] is not None
        assert calculated_state["outcome"]["arrival_altitude"]["base_vrep_altitude_ft_msl"] == 1800
        assert calculated_state["outcome"]["arrival_altitude"]["adopted_altitude_ft_msl"] == 2100
        variations = [
            section["variation_deg_east"]
            for section in calculated_state["outcome"]["sections"]
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
        assert calculated_state["readiness"]["transferAidAllowed"] is False

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
                    "manual_wind_speed_kt": (
                        15 if section["id"] == first_editable_id else None
                    ),
                    "manual_temperature_c": (
                        12 if section["id"] == first_editable_id else None
                    ),
                    "manual_tas_kt": (
                        155 if section["id"] == first_editable_id else None
                    ),
                }
                for section in editable_sections
            ],
            "visual_reporting_point_node_id": destination_state["project"]["route_nodes"][-2][
                "id"
            ],
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
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "TRANSFER_AID_BLOCKED"

        saved = await client.post(
            "/api/projects/save",
            json={"name": "web-smoke"},
        )
        assert saved.status_code == 200
        assert saved.json()["project"]["revision"] == 1


@pytest.mark.anyio
async def test_departure_override_uses_original_kml_start_and_keeps_old_payload_compatible(
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

        confirmed = await client.post(
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
        assert confirmed.status_code == 200, confirmed.text
        project = confirmed.json()["project"]
        assert project["departure_airport_id"] == "RJFK"
        assert project["route_nodes"][0]["name"] == "RJFK"
        assert project["metadata"]["web_original_departure_coordinate"] == [
            31.8033333333,
            130.7194444444,
        ]

        incompatible_override = await client.post(
            "/api/destination/confirm",
            json={
                "departure_airport_id": "RJFM",
                "destination_airport_id": "RJFO",
                "selected_pattern_altitude_ft_msl": 1000,
            },
        )
        assert incompatible_override.status_code == 400
        assert incompatible_override.json()["error"]["code"] == (
            "ROUTE_AIRPORT_ENDPOINT_MISMATCH"
        )
        assert "KML始点" in incompatible_override.json()["error"]["message"]

        compatible_old_payload = await client.post(
            "/api/destination/confirm",
            json={
                "destination_airport_id": "RJFO",
                "selected_pattern_altitude_ft_msl": 1000,
            },
        )
        assert compatible_old_payload.status_code == 200, compatible_old_payload.text
        compatible_project = compatible_old_payload.json()["project"]
        assert compatible_project["departure_airport_id"] == "RJFK"
        assert compatible_project["route_nodes"][0]["name"] == "RJFK"

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
        "flight_date": "2026-08-10",
        "departure_time_jst": "09:00",
        "departure_airport_id": "RJFM",
        "destination_airport_id": "RJFO",
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

            await _save_route(bob, "bob-route")
            bob_state = (await bob.get("/api/state")).json()
            assert [item["name"] for item in bob_state["savedProjects"]] == ["bob-route"]

        alice_state = (await alice.get("/api/state")).json()
        assert [item["name"] for item in alice_state["savedProjects"]] == ["alice-route"]


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
            "小丸",
            "日振島",
            "祝島",
            "ゴルフコース",
            "WP6",
            "RJFO",
        ]
        assert [(node["latitude_deg"], node["longitude_deg"]) for node in nodes[1:-1]] == [
            (31.98214589070221, 131.4317398539069),
            (32.16275095638636, 131.4734489929498),
            (33.1802236311398, 132.2948734240414),
            (33.78695544494976, 131.9894319344609),
            (33.62999835453385, 131.67890296839),
        ]


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
        "created_at_utc": submitted.created_at_utc.isoformat(),
        "updated_at_utc": submitted.updated_at_utc.isoformat(),
    }
