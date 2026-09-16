"""Export #117 FTD Golden through the legacy Python facade, without Local bindings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from autonavlog.importers.kml import import_kml_text
from autonavlog.web.models import (
    ConfirmRouteRequest,
    ReplaceCheckPointsRequest,
    SaveProjectRequest,
    UpdateProjectRequest,
)

ROOT = Path(__file__).resolve().parents[1]


def reference_state(
    *,
    strong_wind: bool = False,
    forecast: bool = False,
    route: str | None = None,
    pinned: bool = False,
    manual: bool = False,
    local: bool = False,
    feed: str | None = None,
    flight_date: str = "2026-09-16",
    departure_time: str = "12:00",
    native_cache: str | None = None,
) -> dict[str, Any]:
    inputs = json.loads((ROOT / "tests/fixtures/issue_117_ftd.json").read_text())
    if strong_wind and (forecast or feed):
        raise ValueError("strong_wind is available only for FTD weather")
    if forecast:
        inputs["confirm"].update(
            weather_mode="FORECAST", flight_date="2026-09-12", departure_time_jst="12:00"
        )
    if feed:
        inputs["confirm"].update(
            weather_mode="FORECAST",
            flight_date=flight_date,
            departure_time_jst=departure_time,
        )
    if forecast or feed:
        inputs["confirm"]["ftd_weather"] = None
    if strong_wind:
        for wind in inputs["confirm"]["ftd_weather"].values():
            wind.update(direction_deg_from=360, speed_kt=200)
    with TemporaryDirectory() as temporary:
        if local:
            from autonavlog.local import LocalApplication

            local_application = LocalApplication(
                ROOT / "data", forecast_fixture=ROOT / "tests/fixtures/msm"
            )
            app = local_application.app
        else:
            from autonavlog.web.runtime import WebRuntimeConfig, build_web_application

            app = build_web_application(
                WebRuntimeConfig(data_root=ROOT / "data", storage_root=Path(temporary))
            )
        if forecast:
            from autonavlog.weather.destination_taf import DecodedTafProvider
            from autonavlog.weather.msm_fixture import (
                FIXTURE_WEATHER_LABEL,
                fixture_weather_provider,
            )

            if not local:
                app.weather_factory = lambda: fixture_weather_provider(ROOT / "tests/fixtures/msm")
            app.weather_label = FIXTURE_WEATHER_LABEL
            app.destination_wind_provider = DecodedTafProvider([], "TAF_PROXY_NOT_CONFIGURED")
            app.development_weather = False
        if feed:
            from autonavlog.weather.destination_taf import DecodedTafProvider
            from autonavlog.weather.local_msm import LocalMsmWeather

            feed_weather = LocalMsmWeather()
            app.weather_factory = lambda: feed_weather.provider
            app.weather_label = "実MSM（端末内処理）"
            app.destination_wind_provider = DecodedTafProvider([], "TAF_PROXY_NOT_CONFIGURED")
            app.development_weather = False
            if native_cache:
                from jma_gpv_weather import MsmClient

                from autonavlog.weather.msm_adapter import MsmWeatherProvider

                listing_data = json.loads((Path(feed) / "catalog.json").read_text())["listings"]

                class CapturedListingClient(MsmClient):
                    def discover_runs(self, requirements):
                        return super().discover_runs(requirements, listings=listing_data)

                app.weather_factory = lambda: MsmWeatherProvider(
                    native_cache, client=CapturedListingClient(native_cache)
                )
        session = app.create_session("reference")
        filename = f"issue_125_{route}.kml" if route else "issue_43_golden.kml"
        app.accept_import(
            session,
            result=import_kml_text(
                (ROOT / "tests/fixtures" / filename).read_text(),
                filename=filename,
            ),
            filename=filename,
        )
        app.confirm_route(session, ConfirmRouteRequest.model_validate(inputs["confirm"]))
        assert session.project is not None
        if route:
            inputs["altitudes"] = [6500] * (len(session.project.sections) - 1) + [2500]
        update = {
            **{
                key: inputs["confirm"][key]
                for key in (
                    "flight_date",
                    "departure_time_jst",
                    "weather_mode",
                    "ftd_weather",
                    "default_variation_deg_east",
                )
            },
            "total_usable_fuel_gal": 90,
            "sections": [
                {
                    "section_id": str(section.id),
                    "phase": section.phase.value,
                    "planned_altitude_ft_msl": altitude,
                }
                for section, altitude in zip(
                    session.project.sections, inputs["altitudes"], strict=True
                )
            ],
            "visual_reporting_point_node_id": str(session.project.route_nodes[-2].id),
            "selected_pattern_altitude_ft_msl": 1000,
        }
        if manual:
            update.update(descent_rate_fpm=1000, run_up_included=False, tgl_count=2)
            update["sections"][0]["manual_tas_kt_by_phase"] = {"CLIMB": 125, "CRUISE": 145}
            update["sections"][1]["manual_wind_by_phase"] = {
                "CRUISE": {"direction_deg_from": 190, "speed_kt": 12}
            }
            update["sections"][2]["manual_tas_kt_by_phase"] = {"DESCENT": 135}
        app.update_project(session, UpdateProjectRequest.model_validate(update))
        if manual:
            from autonavlog.nav.geodesy import geodesic_leg

            start, end = session.project.route_nodes[1:3]
            geometry = geodesic_leg(
                start.latitude_deg, start.longitude_deg, end.latitude_deg, end.longitude_deg
            )
            app.replace_check_points(
                session,
                ReplaceCheckPointsRequest.model_validate(
                    {
                        "check_points": [
                            {
                                "name": "MSM CORE CP",
                                "latitude_deg": geometry.midpoint_latitude_deg,
                                "longitude_deg": geometry.midpoint_longitude_deg,
                                "linked_section_id": str(session.project.sections[1].id),
                            }
                        ]
                    }
                ),
            )
        if pinned:
            pinned_run = "20260915180000" if feed else "20260912000000"
            session.project = session.project.model_copy(
                update={"selected_forecast_run_id": pinned_run}
            )
            app.save(session, SaveProjectRequest(name="Pinned Forecast"))
            session = app.create_session("reference")
            assert session.project.selected_forecast_run_id == pinned_run
        if feed and not native_cache:
            from datetime import UTC, datetime, timedelta

            catalog = json.loads((Path(feed) / "catalog.json").read_text())
            # Reference fixtures retain their weather times; refresh only index lifetime.
            catalog["generated_at"] = datetime.now(UTC).isoformat()
            catalog["expires_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            requirement = session.calculation_service.forecast_service.build_initial_requirement(
                session.project
            )
            asset = json.loads(feed_weather.plan(session.project, requirement, json.dumps(catalog)))
            feed_weather.accept((Path(feed) / asset["file"]).read_bytes(), asset["sha256"])
        return app.calculate(session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strong-wind", action="store_true")
    parser.add_argument("--forecast", action="store_true")
    parser.add_argument("--route", choices=["umk", "omaru", "inbound"])
    parser.add_argument("--pinned", action="store_true")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--feed")
    parser.add_argument("--flight-date", default="2026-09-16")
    parser.add_argument("--departure-time", default="12:00")
    parser.add_argument("--native-cache")
    arguments = parser.parse_args()
    print(
        json.dumps(
            reference_state(
                strong_wind=arguments.strong_wind,
                forecast=arguments.forecast,
                route=arguments.route,
                pinned=arguments.pinned,
                manual=arguments.manual,
                feed=arguments.feed,
                flight_date=arguments.flight_date,
                departure_time=arguments.departure_time,
                native_cache=arguments.native_cache,
            ),
            ensure_ascii=False,
            allow_nan=False,
        )
    )