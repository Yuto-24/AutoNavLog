"""Export #117 FTD Golden through the legacy Python facade, without Local bindings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from autonavlog.importers.kml import import_kml_text
from autonavlog.web.models import ConfirmRouteRequest, UpdateProjectRequest
from autonavlog.web.runtime import WebRuntimeConfig, build_web_application

ROOT = Path(__file__).resolve().parents[1]


def reference_state(*, strong_wind: bool = False) -> dict[str, Any]:
    inputs = json.loads((ROOT / "tests/fixtures/issue_117_ftd.json").read_text())
    if strong_wind:
        for wind in inputs["confirm"]["ftd_weather"].values():
            wind.update(direction_deg_from=360, speed_kt=200)
    with TemporaryDirectory() as temporary:
        app = build_web_application(
            WebRuntimeConfig(
                data_root=ROOT / "data",
                storage_root=Path(temporary),
            )
        )
        session = app.create_session("reference")
        app.accept_import(
            session,
            result=import_kml_text(
                (ROOT / "tests/fixtures/issue_43_golden.kml").read_text(),
                filename="issue_43_golden.kml",
            ),
            filename="issue_43_golden.kml",
        )
        app.confirm_route(session, ConfirmRouteRequest.model_validate(inputs["confirm"]))
        assert session.project is not None
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
        app.update_project(session, UpdateProjectRequest.model_validate(update))
        return app.calculate(session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strong-wind", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            reference_state(strong_wind=arguments.strong_wind),
            ensure_ascii=False,
            allow_nan=False,
        )
    )
