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
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/")).status_code == 200

        created = await client.post("/api/session")
        assert created.status_code == 200
        token = created.json()["sessionToken"]
        headers = {"X-AutoNavLog-Session": token}

        imported = await client.post(
            "/api/import",
            headers=headers,
            json={"filename": "route.kml", "kml_text": KML},
        )
        assert imported.status_code == 200
        assert imported.json()["import"]["candidates"][0]["name"] == "RJFM-RJFO"

        confirmed = await client.post(
            "/api/route/confirm",
            headers=headers,
            json={
                "candidate_kind": "line",
                "candidate_index": 0,
                "route_use_confirmed": True,
                "flight_date": "2026-08-10",
                "departure_time_jst": "09:00",
                "departure_airport_id": "RJFM",
                "destination_airport_id": "RJFO",
                "pilot_name": "Test Pilot",
                "ship_identifier": "JA01AN",
                "total_usable_fuel_gal": 81,
                "default_variation_deg_east": 8,
                "all_leg_altitude_ft_msl": 3000,
                "defaults_confirmed": True,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        confirmed_state = confirmed.json()
        assert len(confirmed_state["project"]["route_nodes"]) == 5
        assert confirmed_state["project"]["route_nodes"][-2]["role"] == (
            "VISUAL_REPORTING_POINT"
        )
        assert confirmed_state["project"]["sections"][-1]["planned_altitude_ft_msl"] == 1500
        assert any(
            issue["code"] == "PATTERN_ALTITUDE_REQUIRED"
            for issue in confirmed_state["readiness"]["issues"]
        )

        calculated = await client.post("/api/calculate", headers=headers)
        assert calculated.status_code == 200, calculated.text
        calculated_state = calculated.json()
        assert calculated_state["outcome"] is not None
        assert sum(
            issue["code"] == "PATTERN_ALTITUDE_REQUIRED"
            for issue in calculated_state["readiness"]["issues"]
        ) == 1
        assert any(
            issue["code"] == "DEVELOPMENT_WEATHER_PROVIDER"
            for issue in calculated_state["readiness"]["issues"]
        )
        assert calculated_state["readiness"]["transferAidAllowed"] is False

        blocked = await client.get("/api/transfer-aid", headers=headers)
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "TRANSFER_AID_BLOCKED"

        saved = await client.post(
            "/api/projects/save",
            headers=headers,
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
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.get("/api/state")
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "SESSION_REQUIRED"

        token = (await client.post("/api/session")).json()["sessionToken"]
        invalid = await client.post(
            "/api/import",
            headers={"X-AutoNavLog-Session": token},
            json={"filename": "bad.kml", "content_base64": "%%%"},
        )
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "UPLOAD_ENCODING_INVALID"
