from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from autonavlog.web.app import create_app
from autonavlog.web.runtime import WebRuntimeConfig

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "connected_oita_routes.kml"


def _connected_route_kml(vertex_count: int) -> str:
    start = (31.8771201986468, 131.4484514728637)
    end = (33.47949347092593, 131.7371201702322)
    coordinates = [
        (
            start[0] + (end[0] - start[0]) * index / (vertex_count - 1),
            start[1] + (end[1] - start[1]) * index / (vertex_count - 1),
        )
        for index in range(vertex_count)
    ]
    join_index = vertex_count // 2

    def coordinate_text(items: list[tuple[float, float]]) -> str:
        return " ".join(f"{longitude:.12f},{latitude:.12f}" for latitude, longitude in items)

    return f"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Doc</name>
    <Folder><name>Long route</name>
    <Placemark><name>RJFM</name><Point><coordinates>{start[1]},{start[0]}</coordinates>
    </Point></Placemark>
    <Placemark><name>A</name><LineString><coordinates>
    {coordinate_text(coordinates[: join_index + 1])}
    </coordinates></LineString></Placemark>
    <Placemark><name>B</name><LineString><coordinates>
    {coordinate_text(coordinates[join_index:])}
    </coordinates></LineString></Placemark>
    <Placemark><name>RJFO</name><Point><coordinates>{end[1]},{end[0]}</coordinates>
    </Point></Placemark>
    </Folder></Document></kml>"""


def _confirm_payload() -> dict[str, object]:
    return {
        "candidate_kind": "connected_lines",
        "candidate_index": 0,
        "route_use_confirmed": True,
        "flight_date": "2099-08-10",
        "departure_time_jst": "09:00",
        "total_usable_fuel_gal": 90,
        "default_variation_deg_east": 8,
        "all_leg_altitude_ft_msl": 3500,
        "defaults_confirmed": True,
    }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_web_import_exposes_four_connected_candidates_and_records_selection(
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
            json={
                "filename": FIXTURE.name,
                "kml_text": FIXTURE.read_text(encoding="utf-8"),
            },
        )
        assert imported.status_code == 200, imported.text
        import_payload = imported.json()["import"]
        candidates = import_payload["candidates"]

        assert import_payload["warnings"] == []
        assert [item["kind"] for item in candidates] == ["connected_lines"] * 4
        assert [item["index"] for item in candidates] == [0, 1, 2, 3]
        assert [item["name"] for item in candidates] == [
            "RJFM→RJFO①",
            "RJFM→RJFO②",
            "RJFO→RJFM①",
            "RJFO→RJFM②",
        ]
        assert [item["segmentCount"] for item in candidates] == [7, 6, 5, 5]
        assert [item["legCount"] for item in candidates] == [7, 6, 5, 5]
        assert [item["vertexCount"] for item in candidates] == [8, 7, 6, 6]
        assert [item["distanceNm"] for item in candidates] == [
            124.66,
            151.42,
            112.03,
            129.01,
        ]
        assert candidates[0]["containerPath"] == ["大分経路", "RJFO", "RJFM→RJFO①"]
        assert candidates[0]["segmentNames"][0] == "RJFM→UMK(TC352,DIST6.5)"
        assert candidates[0]["segmentNames"][-1] == "杵築→RJFO(TC054,DIST6.5)"
        assert candidates[0]["maxJoinGapNm"] == pytest.approx(0.00061)

        confirmed = await client.post(
            "/api/route/confirm",
            json=_confirm_payload(),
        )
        assert confirmed.status_code == 200, confirmed.text
        project = confirmed.json()["project"]
        assert len(project["route_nodes"]) == 8
        assert project["route_nodes"][0]["name"] == "RJFM"
        assert project["route_nodes"][-1]["name"] == "RJFO"
        assert project["route_nodes"][1]["name"] == "UMK"
        assert project["route_nodes"][1]["name_source"] == "GENERATED"
        assert project["route_nodes"][1]["source"] == "KML/KMZ Point"
        assert project["metadata"]["web_import_container_path"] == [
            "大分経路",
            "RJFO",
            "RJFM→RJFO①",
        ]
        assert project["metadata"]["web_import_segment_names"] == candidates[0][
            "segmentNames"
        ]


@pytest.mark.anyio
async def test_connected_route_coordinate_limit_has_dedicated_error_and_allows_500(
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
        deduplicated_import = await client.post(
            "/api/import",
            json={
                "filename": "deduplicated.kml",
                "kml_text": """<kml xmlns="http://www.opengis.net/kml/2.2">
                <Document><name>Doc</name><Folder><name>Deduplicated</name>
                <Placemark><name>A</name><LineString><coordinates>
                131.448451472864,31.877120198647 131.448501472864,31.877120198647
                131.5,32.2 131.6,32.8
                </coordinates></LineString></Placemark>
                <Placemark><name>B</name><LineString><coordinates>
                131.6,32.8 131.737120170232,33.479493470926
                </coordinates></LineString></Placemark>
                </Folder></Document></kml>""",
            },
        )
        assert deduplicated_import.status_code == 200, deduplicated_import.text
        deduplicated_candidate = deduplicated_import.json()["import"]["candidates"][0]
        assert deduplicated_candidate["segmentCount"] == 2
        assert deduplicated_candidate["legCount"] == 3
        assert deduplicated_candidate["vertexCount"] == 4
        assert len(deduplicated_candidate["coordinates"]) == 4

        oversized_import = await client.post(
            "/api/import",
            json={"filename": "501.kml", "kml_text": _connected_route_kml(501)},
        )
        assert oversized_import.status_code == 200, oversized_import.text
        oversized_candidate = oversized_import.json()["import"]["candidates"][0]
        assert oversized_candidate["segmentCount"] == 2
        assert oversized_candidate["legCount"] == 500
        assert oversized_candidate["vertexCount"] == 501

        rejected = await client.post("/api/route/confirm", json=_confirm_payload())
        assert rejected.status_code == 400
        assert rejected.json()["error"] == {
            "code": "ROUTE_COORDINATE_LIMIT_EXCEEDED",
            "message": (
                "選択した経路は座標数の上限（500点）を超えています。"
                "500点以下の経路を選択してください。"
            ),
        }

        maximum_import = await client.post(
            "/api/import",
            json={"filename": "500.kml", "kml_text": _connected_route_kml(500)},
        )
        assert maximum_import.status_code == 200, maximum_import.text
        maximum_candidate = maximum_import.json()["import"]["candidates"][0]
        assert maximum_candidate["segmentCount"] == 2
        assert maximum_candidate["legCount"] == 499
        assert maximum_candidate["vertexCount"] == 500

        confirmed = await client.post("/api/route/confirm", json=_confirm_payload())
        assert confirmed.status_code == 200, confirmed.text
        assert len(confirmed.json()["project"]["route_nodes"]) == 500


@pytest.mark.anyio
async def test_invalid_group_falls_back_to_lines_and_points_only_remain_a_fallback(
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
        invalid = await client.post(
            "/api/import",
            json={
                "filename": "invalid.kml",
                "kml_text": """<kml xmlns="http://www.opengis.net/kml/2.2">
                <Document><name>Doc</name><Folder><name>Route</name>
                <Placemark><name>P1</name><Point><coordinates>131,31</coordinates>
                </Point></Placemark>
                <Placemark><name>P2</name><Point><coordinates>132,32</coordinates>
                </Point></Placemark>
                <Placemark><name>A</name><LineString><coordinates>131,31 131.1,31</coordinates>
                </LineString></Placemark>
                <Placemark><name>B</name><LineString><coordinates>132,32 132.1,32</coordinates>
                </LineString></Placemark>
                </Folder></Document></kml>""",
            },
        )
        assert invalid.status_code == 200, invalid.text
        invalid_import = invalid.json()["import"]
        assert [item["kind"] for item in invalid_import["candidates"]] == ["line", "line"]
        assert "kept as individual candidates" in invalid_import["warnings"][0]

        points_only = await client.post(
            "/api/import",
            json={
                "filename": "points.kml",
                "kml_text": """<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
                <Placemark><name>P1</name><Point><coordinates>131,31</coordinates>
                </Point></Placemark>
                <Placemark><name>P2</name><Point><coordinates>132,32</coordinates>
                </Point></Placemark>
                </Document></kml>""",
            },
        )
        assert points_only.status_code == 200, points_only.text
        assert [
            item["kind"] for item in points_only.json()["import"]["candidates"]
        ] == ["points"]
