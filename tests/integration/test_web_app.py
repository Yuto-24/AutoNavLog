from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from autonavlog.web.app import create_app
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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_web_route_calculation_save_and_fail_closed_output(tmp_path: Path) -> None:
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
        assert any(
            issue["code"] == "PATTERN_ALTITUDE_REQUIRED"
            for issue in confirmed_state["readiness"]["issues"]
        )

        calculated = await client.post("/api/calculate")
        assert calculated.status_code == 200, calculated.text
        calculated_state = calculated.json()
        assert calculated_state["outcome"] is not None
        assert (
            sum(
                issue["code"] == "PATTERN_ALTITUDE_REQUIRED"
                for issue in calculated_state["readiness"]["issues"]
            )
            == 1
        )
        assert any(
            issue["code"] == "DEVELOPMENT_WEATHER_PROVIDER"
            for issue in calculated_state["readiness"]["issues"]
        )
        assert calculated_state["readiness"]["transferAidAllowed"] is False

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
async def test_cloudflare_identity_is_required_without_local_override(tmp_path: Path) -> None:
    app = create_app(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=tmp_path / "storage",
            weather_mode="fake",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        unauthenticated = await client.post("/api/session")
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

        authenticated = await client.post(
            "/api/session",
            headers={"Cf-Access-Authenticated-User-Email": "Pilot@Example.com"},
        )
        assert authenticated.status_code == 200
        assert "sessionToken" not in authenticated.json()


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
    transport = httpx.ASGITransport(app=app)
    alice_headers = {"Cf-Access-Authenticated-User-Email": "Alice@Example.com"}
    bob_headers = {"Cf-Access-Authenticated-User-Email": "bob@example.com"}

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
async def test_line_waypoint_names_reach_the_confirmed_route(tmp_path: Path) -> None:
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
        names = [node["name"] for node in confirmed.json()["project"]["route_nodes"]]

        assert names == ["RJFM", "日振島", "祝島", "RJFO"]
