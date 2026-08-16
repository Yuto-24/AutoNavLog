#!/usr/bin/env python3
"""Deterministic AutoNavLog acceptance checks for a fresh Google Colab VM.

Before execution, upload these files to the active VM:

* ``/content/autonavlog-0.3.1-py3-none-any.whl``
* ``/content/pasted-text-1.txt``

The default entry point installs the wheel, exercises the real pasted Google
Earth KML, runs a deterministic calculation, and checks the interactive Colab
UI. It always attempts to write ``/content/autonavlog-colab-e2e-result.json``.

The scanner mode is intended for the local machine after ``colab exec`` writes
an ``*_output.ipynb`` file:

    python scripts/colab_e2e_assert.py \
      --scan-output-notebook notebooks/AutoNavLog_output.ipynb
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import math
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CONTENT_ROOT = Path("/content")
WHEEL_PATH = CONTENT_ROOT / "autonavlog-0.3.1-py3-none-any.whl"
PASTED_KML_PATH = CONTENT_ROOT / "pasted-text-1.txt"
RESULT_PATH = CONTENT_ROOT / "autonavlog-colab-e2e-result.json"
EXPECTED_AUTONAVLOG_VERSION = "0.3.1"
EXPECTED_KML_WARNING = (
    "KS4-6(SFC/4000): skipped 13 Polygon surface(s) without a usable horizontal boundary"
)
JST = ZoneInfo("Asia/Tokyo")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_report(report: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _install_autonavlog_wheel(wheel_path: Path) -> dict[str, Any]:
    _require(wheel_path.is_file(), f"uploaded wheel is missing: {wheel_path}")
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--disable-pip-version-check",
        "--no-input",
        str(wheel_path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        diagnostic = "\n".join(
            line
            for line in (completed.stdout + "\n" + completed.stderr).splitlines()[-20:]
            if line.strip()
        )
        raise RuntimeError(
            f"AutoNavLog wheel installation failed ({completed.returncode}):\n{diagnostic}"
        )
    importlib.invalidate_caches()
    installed_version = importlib.metadata.version("autonavlog")
    _require(
        installed_version == EXPECTED_AUTONAVLOG_VERSION,
        "installed AutoNavLog version mismatch: "
        f"expected {EXPECTED_AUTONAVLOG_VERSION}, found {installed_version}",
    )
    return {
        "status": "PASS",
        "path": str(wheel_path),
        "sha256": _sha256(wheel_path),
        "installed_version": installed_version,
    }


def _check_pasted_kml(kml_path: Path) -> tuple[str, dict[str, Any]]:
    from autonavlog.importers.kml import import_kml_text

    _require(kml_path.is_file(), f"uploaded pasted KML is missing: {kml_path}")
    text = kml_path.read_text(encoding="utf-8-sig")
    imported = import_kml_text(text, filename=kml_path.name)
    _require(len(imported.points) == 0, "model KML unexpectedly contains Point geometry")
    _require(
        len(imported.lines) == 0,
        "model KML unexpectedly contains LineString geometry",
    )
    _require(
        len(imported.polygons) == 1,
        f"expected one usable Polygon, found {len(imported.polygons)}",
    )
    polygon = imported.polygons[0]
    _require(
        len(polygon.outer_boundary) == 14,
        f"expected 14 closed outer-boundary coordinates, found {len(polygon.outer_boundary)}",
    )
    _require(
        polygon.outer_boundary[0] == polygon.outer_boundary[-1],
        "Polygon outer boundary is not closed",
    )
    _require(
        imported.warnings == (EXPECTED_KML_WARNING,),
        f"unexpected model KML warnings: {imported.warnings!r}",
    )
    return text, {
        "status": "PASS",
        "path": str(kml_path),
        "sha256": _sha256(kml_path),
        "polygon_count": len(imported.polygons),
        "outer_boundary_coordinate_count": len(polygon.outer_boundary),
        "polygon_name": polygon.name,
        "minimum_altitude_m": polygon.minimum_altitude_m,
        "maximum_altitude_m": polygon.maximum_altitude_m,
        "warnings": list(imported.warnings),
    }


def _synthetic_fixture() -> tuple[Any, Any, Any, Any]:
    from autonavlog.domain.enums import (
        Availability,
        FlightPhase,
        RouteNodeRole,
        WeatherRequestKind,
    )
    from autonavlog.domain.project import Airport, NavSection, Project, RouteNode
    from autonavlog.domain.weather import WeatherResult
    from autonavlog.performance.repository import PerformanceRepository
    from autonavlog.performance.schemas import (
        ClimbRow,
        CruiseRow,
        PerformanceManifest,
    )
    from autonavlog.storage.airports import AirportRepository
    from autonavlog.weather.fake_provider import FakeWeatherProvider

    airports = AirportRepository(
        [
            Airport(
                id="RJFM",
                icao="RJFM",
                name="Miyazaki",
                latitude_deg=31.877,
                longitude_deg=131.449,
                elevation_ft_msl=20,
                pattern_altitude_ft_msl=1020,
                source="COLAB_E2E_SYNTHETIC",
                source_revision="v1",
            ),
            Airport(
                id="RJFO",
                icao="RJFO",
                name="Oita",
                latitude_deg=33.479,
                longitude_deg=131.737,
                elevation_ft_msl=19,
                pattern_altitude_ft_msl=1019,
                source="COLAB_E2E_SYNTHETIC",
                source_revision="v1",
            ),
        ]
    )

    climb_rows = []
    for altitude, time_min, fuel_gal, distance_nm in (
        (0.0, 0.0, 0.0, 0.0),
        (5000.0, 10.0, 4.0, 15.0),
        (6000.0, 12.0, 4.8, 18.0),
    ):
        for temperature_c in (0.0, 20.0):
            climb_rows.append(
                ClimbRow(
                    pressure_altitude_ft=altitude,
                    temperature_c=temperature_c,
                    weight_lb=3400,
                    cumulative_time_min=time_min + temperature_c / 100,
                    cumulative_fuel_gal=fuel_gal + temperature_c / 200,
                    cumulative_distance_nm=distance_nm + temperature_c / 50,
                    source_page="COLAB_E2E_SYNTHETIC",
                )
            )

    cruise_rows = []
    for altitude in (4000.0, 6000.0):
        for isa_deviation_c in (-15.0, 15.0):
            cruise_rows.extend(
                [
                    CruiseRow(
                        pressure_altitude_ft=altitude,
                        isa_deviation_c=isa_deviation_c,
                        rpm=2300,
                        map_in_hg=20,
                        power_percent=65,
                        ktas=150 - (altitude - 4000) / 2000,
                        gph=15 + (isa_deviation_c + 10) / 20,
                        source_page="COLAB_E2E_SYNTHETIC",
                    ),
                    CruiseRow(
                        pressure_altitude_ft=altitude,
                        isa_deviation_c=isa_deviation_c,
                        rpm=2350,
                        map_in_hg=21,
                        power_percent=70,
                        ktas=155,
                        gph=16.5,
                        source_page="COLAB_E2E_SYNTHETIC",
                    ),
                ]
            )
    performance = PerformanceRepository(
        PerformanceManifest(
            aircraft="SR22 G6 (SYNTHETIC E2E)",
            source_document="COLAB_E2E_SYNTHETIC",
            source_revision="v1",
            verified_against="DETERMINISTIC_TEST_FIXTURE",
            validation_status="VERIFIED",
        ),
        climb_rows,
        cruise_rows,
    )

    departure = RouteNode(
        sequence=0,
        name="RJFM",
        latitude_deg=31.877,
        longitude_deg=131.449,
        role=RouteNodeRole.AIRPORT,
        source="COLAB_E2E_SYNTHETIC",
    )
    turn = RouteNode(
        sequence=1,
        name="TP1",
        # 25 NM along the RJFM-RJFO geodesic. RCA remains inside this
        # physical leg while the gate checks the CAC CAS-111 calculation.
        latitude_deg=32.289856794821766,
        longitude_deg=131.52222746412863,
        role=RouteNodeRole.TURN_POINT,
        source="COLAB_E2E_SYNTHETIC",
    )
    destination = RouteNode(
        sequence=2,
        name="RJFO",
        latitude_deg=33.479,
        longitude_deg=131.737,
        role=RouteNodeRole.DESTINATION,
        source="COLAB_E2E_SYNTHETIC",
    )
    project = Project(
        name="AutoNavLog Colab E2E",
        pilot_name="E2E",
        ship_identifier="JA00XX",
        flight_date=date(2026, 7, 29),
        planned_departure_time_jst=datetime(2026, 7, 29, 9, 0, tzinfo=JST),
        departure_airport_id="RJFM",
        destination_airport_id="RJFO",
        total_usable_fuel_gal=81,
        default_variation_deg_east=8,
        route_nodes=[departure, turn, destination],
        sections=[
            NavSection(
                sequence=0,
                from_node_id=departure.id,
                to_node_id=turn.id,
                phase=FlightPhase.CLIMB,
                planned_altitude_ft_msl=5000,
            ),
            NavSection(
                sequence=1,
                from_node_id=turn.id,
                to_node_id=destination.id,
                phase=FlightPhase.CRUISE,
                planned_altitude_ft_msl=5000,
            ),
        ],
    )

    def weather_result(request: Any) -> Any:
        if request.kind == WeatherRequestKind.ESTIMATED_QNH:
            return WeatherResult(
                request_id=request.request_id,
                availability=Availability.AVAILABLE,
                kind=request.kind,
                values={"label": "MSM推定QNH", "qnh_hpa": 1013.0},
                metadata={"provider": "COLAB_E2E_SYNTHETIC"},
            )
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values={
                "u_ms": 10.2889,
                "v_ms": 0.0,
                "wind_speed_kt": 20.0,
                "wind_direction_deg_from": 270.0,
                "temperature_c": 15.0,
            },
            metadata={"provider": "COLAB_E2E_SYNTHETIC"},
        )

    provider = FakeWeatherProvider(result_factory=weather_result)
    return airports, performance, project, provider


def _check_calculation() -> tuple[tuple[Any, Any], dict[str, Any]]:
    from autonavlog.application.calculation_service import CalculationService
    from autonavlog.nav.airspeed import (
        pressure_altitude_exact_ft,
        tas_from_cas,
    )
    from autonavlog.presentation.clearcopy import render_clearcopy_html

    airports, performance, project, provider = _synthetic_fixture()
    calculation = CalculationService(airports, performance)
    outcome = calculation.calculate(project, provider)
    _require(outcome.converged, "deterministic calculation did not converge")
    _require(len(outcome.sections) == 3, "expected RCA to split two physical legs into three")
    _require(not outcome.blockers, f"unexpected blockers: {outcome.blockers!r}")
    rca_boundary_index = next(
        (
            index
            for index, section in enumerate(outcome.sections[:-1])
            if section.to_name == "RCA" and outcome.sections[index + 1].from_name == "RCA"
        ),
        None,
    )
    _require(
        rca_boundary_index is not None,
        "RCA split is missing from adjacent calculated sections",
    )
    _require(
        len(outcome.derived_points) == 1 and outcome.derived_points[0].type.value == "RCA",
        "calculation did not emit exactly one RCA derived point",
    )
    climb_sections = [section for section in outcome.sections if section.phase.value == "CLIMB"]
    _require(bool(climb_sections), "calculation emitted no CLIMB section")
    qnh_hpa = outcome.qnh_hpa.adopted()
    _require(qnh_hpa is not None, "automatic QNH is unavailable")
    departure = airports.get(project.departure_airport_id)
    climb_source = project.ordered_sections()[0]
    departure_pressure_altitude = pressure_altitude_exact_ft(
        departure.elevation_ft_msl,
        float(qnh_hpa),
    )
    cruise_pressure_altitude = pressure_altitude_exact_ft(
        climb_source.planned_altitude_ft_msl,
        float(qnh_hpa),
    )
    representative_pressure_altitude = (
        departure_pressure_altitude + cruise_pressure_altitude
    ) / 2.0
    expected_climb_tas = tas_from_cas(
        111.0,
        representative_pressure_altitude,
        15.0,
    )
    climb_metadata = climb_sections[0].performance_metadata
    _require(
        climb_metadata.get("cas_kt") == 111.0
        and climb_metadata.get("tas_method")
        == ("CAC_REV19_8-(3)_5_(1)_CAS_111_AT_REPRESENTATIVE_PRESSURE_ALTITUDE"),
        "CLIMB metadata does not identify the CAC Rev.19 CAS 111 rule",
    )
    _require(
        all(
            section.cas_kt.adopted() == 111.0
            and math.isclose(
                section.tas_kt.adopted() or -1.0,
                expected_climb_tas,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            for section in climb_sections
        ),
        "CLIMB TAS is not CAS 111 converted at midpoint temperature/altitude",
    )
    planned_climb_seconds = climb_metadata.get("planned_duration_seconds")
    _require(
        isinstance(planned_climb_seconds, (int, float))
        and math.isclose(
            sum(section.zone_ete_seconds.adopted() or 0.0 for section in climb_sections),
            float(planned_climb_seconds),
            rel_tol=0.0,
            abs_tol=1e-8,
        ),
        "RCA ETE does not equal the POH climb time",
    )
    _require(
        math.isclose(
            sum(section.zone_distance_nm.adopted() or 0.0 for section in climb_sections),
            outcome.derived_points[0].along_route_distance_nm,
            rel_tol=0.0,
            abs_tol=1e-8,
        ),
        "RCA distance is not the climb TAS/wind distance over the POH ETE",
    )
    total_zone_distance = sum(
        section.zone_distance_nm.adopted() or 0.0 for section in outcome.sections
    )
    _require(
        math.isclose(
            total_zone_distance,
            outcome.sections[-1].cumulative_distance_nm.adopted() or -1.0,
            rel_tol=0.0,
            abs_tol=1e-8,
        ),
        "phase splitting did not conserve route distance",
    )

    section_metrics = []
    has_nonzero_wca = False
    for section in outcome.sections:
        wca = section.wca_deg.adopted()
        ground_speed = section.ground_speed_kt.adopted()
        ete_seconds = section.zone_ete_seconds.adopted()
        if wca is None or not math.isfinite(wca):
            raise AssertionError("WCA is unavailable")
        _require(
            ground_speed is not None and math.isfinite(ground_speed) and ground_speed > 0,
            "GS is unavailable or non-positive",
        )
        _require(
            ete_seconds is not None and math.isfinite(ete_seconds) and ete_seconds > 0,
            "ETE is unavailable or non-positive",
        )
        zone_distance = section.zone_distance_nm.adopted()
        _require(zone_distance is not None, "zone distance is unavailable")
        _require(
            math.isclose(
                zone_distance,
                ground_speed * ete_seconds / 3600.0,
                rel_tol=0.0,
                abs_tol=1e-8,
            ),
            "DIST, GS, and ETE do not conserve the segment kinematics",
        )
        has_nonzero_wca = has_nonzero_wca or abs(float(wca)) > 0.01
        section_metrics.append(
            {
                "phase": section.phase.value,
                "from": section.from_name,
                "to": section.to_name,
                "zone_distance_nm": zone_distance,
                "wca_deg": wca,
                "ground_speed_kt": ground_speed,
                "ete_seconds": ete_seconds,
            }
        )
    _require(
        has_nonzero_wca,
        "synthetic crosswind did not produce a non-zero WCA",
    )

    html = render_clearcopy_html(project, outcome)
    required_labels = (
        "AutoNavLog 転記補助表（非公式）",
        "WCA",
        "GS",
        "ZONE ETE",
    )
    _require(
        all(label in html for label in required_labels),
        "clearcopy HTML is missing WCA, GS, or ZONE ETE",
    )
    return (calculation, provider), {
        "status": "PASS",
        "converged": outcome.converged,
        "project_status": outcome.status.value,
        "selected_forecast_run_id": outcome.selected_forecast_run_id,
        "climb_formula": {
            "cas_kt": 111.0,
            "representative_pressure_altitude_ft": (representative_pressure_altitude),
            "midpoint_temperature_c": 15.0,
            "expected_tas_kt": expected_climb_tas,
            "planned_duration_seconds": planned_climb_seconds,
            "rca_distance_nm": (outcome.derived_points[0].along_route_distance_nm),
        },
        "section_metrics": section_metrics,
        "clearcopy_required_labels": list(required_labels),
        "clearcopy_length": len(html),
        "clearcopy_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
    }


def _find_widget_attribute(
    instance: object,
    expected_type: type[Any],
    *,
    preferred_names: Sequence[str],
    predicate: Callable[[str, Any], bool],
) -> tuple[str, Any]:
    for name in preferred_names:
        value = getattr(instance, name, None)
        if isinstance(value, expected_type):
            return name, value
    for name, value in vars(instance).items():
        if isinstance(value, expected_type) and predicate(name, value):
            return name, value
    raise AssertionError(f"could not detect {expected_type.__name__} on {type(instance).__name__}")


def _widget_search_text(name: str, widget: Any) -> str:
    values = (
        name,
        str(getattr(widget, "description", "")),
        str(getattr(widget, "placeholder", "")),
        str(getattr(widget, "tooltip", "")),
    )
    return " ".join(values).lower()


def _check_ui(
    work_directory: Path,
    kml_text: str,
    calculation_and_provider: tuple[Any, Any],
) -> dict[str, Any]:
    import ipywidgets as widgets

    from autonavlog.application.project_service import ProjectService
    from autonavlog.presentation.colab import AutoNavLogApp
    from autonavlog.storage.local import LocalProjectRepository

    calculation, provider = calculation_and_provider
    app = AutoNavLogApp(
        ProjectService(LocalProjectRepository(work_directory / "projects")),
        calculation,
        provider,
        download_directory=work_directory / "downloads",
    )
    root = app.render()
    try:
        _require(isinstance(root, widgets.VBox), f"unexpected UI root: {type(root)!r}")
        headings = [
            child.value
            for child in root.children
            if isinstance(child, widgets.HTML) and child.value.startswith("<h3>")
        ]
        _require(
            len(headings) == 9,
            f"expected nine workflow sections, found {len(headings)}",
        )

        textarea_name, textarea = _find_widget_attribute(
            app,
            widgets.Textarea,
            preferred_names=("kml_text", "paste_text", "xml_text"),
            predicate=lambda name, widget: any(
                keyword in _widget_search_text(name, widget)
                for keyword in ("kml", "xml", "paste", "貼")
            ),
        )
        button_name, import_button = _find_widget_attribute(
            app,
            widgets.Button,
            preferred_names=("import_text_button", "paste_button"),
            predicate=lambda name, widget: (
                any(
                    keyword in _widget_search_text(name, widget)
                    for keyword in ("kml", "xml", "paste", "貼付")
                )
                and any(
                    keyword in _widget_search_text(name, widget)
                    for keyword in ("import", "read", "取込", "読み込")
                )
            ),
        )
        textarea.value = kml_text
        import_button.click()
        imported = getattr(app, "import_result", None)
        if imported is None:
            raise AssertionError("paste button did not populate import_result")
        _require(
            len(getattr(imported, "polygons", ())) == 1,
            "paste UI did not retain the model Polygon",
        )

        shape_name, shape_select = _find_widget_attribute(
            app,
            widgets.Select,
            preferred_names=("shape_candidates",),
            predicate=lambda _name, widget: any(
                isinstance(option, tuple)
                and len(option) == 2
                and isinstance(option[1], tuple)
                and option[1][0] == "polygon"
                for option in widget.options
            ),
        )
        confirmation_name, confirmation = _find_widget_attribute(
            app,
            widgets.Checkbox,
            preferred_names=("polygon_route_confirmation",),
            predicate=lambda name, widget: "polygon" in _widget_search_text(name, widget),
        )
        _require(
            any(
                isinstance(option, tuple) and len(option) == 2 and option[1] == ("polygon", 0)
                for option in shape_select.options
            ),
            "shape selector has no imported Polygon option",
        )
        _require(
            "polygon" in _widget_search_text(confirmation_name, confirmation),
            "Polygon route confirmation control is not identifiable",
        )
        shape_select.value = ("polygon", 0)
        confirmation.value = True
        quick_name, quick_button = _find_widget_attribute(
            app,
            widgets.Button,
            preferred_names=("quick_calculate_button",),
            predicate=lambda name, widget: (
                "nav log" in _widget_search_text(name, widget)
                and any(
                    keyword in _widget_search_text(name, widget)
                    for keyword in ("calculate", "計算")
                )
            ),
        )
        quick_button.click()
        initial_outcome = getattr(app, "outcome", None)
        _require(
            initial_outcome is not None,
            "one-click pasted-KML flow produced no outcome",
        )
        project = getattr(app, "project", None)
        _require(project is not None, "one-click flow did not create a Project")
        _require(
            len(project.route_nodes) == 16 and len(project.sections) == 15,
            "one-click flow did not create airport endpoints plus the 14-point Polygon route",
        )
        legacy_sea_issues = [
            issue
            for issue in initial_outcome.issues
            if issue.code.startswith("SEA_")
            or issue.code
            in {
                "SAFE_ENROUTE_ALTITUDE_REQUIRED",
                "PLANNED_ALTITUDE_BELOW_SAFE_ENROUTE",
            }
        ]
        _require(
            initial_outcome.status.value == "READY_FOR_COPY"
            and not initial_outcome.blockers
            and not legacy_sea_issues,
            f"one-click flow is not ready under the non-SEA v2.6 gate: {initial_outcome.issues!r}",
        )
        source_phases = [section.phase.value for section in project.ordered_sections()]
        _require(
            source_phases[0] == "CLIMB"
            and source_phases[-2:] == ["DESCENT", "VISUAL_ARRIVAL"]
            and "CRUISE" in source_phases[1:-2],
            f"one-click NAV2 phase assignment is incomplete: {source_phases!r}",
        )
        derived_types = [point.type.value for point in initial_outcome.derived_points]
        _require(
            "RCA" in derived_types and "EOC" in derived_types,
            f"one-click NAV2 output is missing RCA/EOC: {derived_types!r}",
        )

        outcome = initial_outcome
        transfer_aid_path = app.write_transfer_aid_html()
        transfer_aid_document = transfer_aid_path.read_text(encoding="utf-8")
        _require(
            transfer_aid_document.startswith("<!doctype html>")
            and 'onclick="window.print()"' in transfer_aid_document
            and "AutoNavLog 転記補助表（非公式）" in transfer_aid_document,
            "one-click flow did not create a printable standalone transfer aid",
        )
        quick_status = str(getattr(app.quick_run_status, "value", ""))
        _require(
            "NAV LOG計算完了" in quick_status and "ワンクリック処理結果" in quick_status,
            "one-click flow did not retain its success summary",
        )
        return {
            "status": "PASS",
            "root_type": type(root).__name__,
            "workflow_section_count": len(headings),
            "paste_textarea_attribute": textarea_name,
            "paste_button_attribute": button_name,
            "shape_selector_attribute": shape_name,
            "polygon_confirmation_attribute": confirmation_name,
            "imported_polygon_count": len(imported.polygons),
            "quick_calculate_button_attribute": quick_name,
            "quick_project_route_node_count": len(project.route_nodes),
            "quick_project_section_count": len(project.sections),
            "quick_project_initial_status": initial_outcome.status.value,
            "quick_project_legacy_sea_issue_count": len(legacy_sea_issues),
            "quick_project_status": outcome.status.value,
            "quick_project_source_phases": source_phases,
            "quick_project_derived_points": derived_types,
            "transfer_aid_file": transfer_aid_path.name,
            "transfer_aid_sha256": hashlib.sha256(
                transfer_aid_document.encode("utf-8")
            ).hexdigest(),
        }
    finally:
        root.close()


def run_colab_e2e(
    *,
    content_root: Path = CONTENT_ROOT,
    wheel_path: Path = WHEEL_PATH,
    pasted_kml_path: Path = PASTED_KML_PATH,
    result_path: Path = RESULT_PATH,
) -> int:
    """Run the deterministic gate and return a process-style exit code."""

    in_colab = importlib.util.find_spec("google.colab") is not None
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "RUNNING",
        "stage": "preflight",
        "environment": {
            "in_colab": in_colab,
            "python_version": sys.version.split()[0],
            "executable": sys.executable,
        },
        "inputs": {
            "wheel": str(wheel_path),
            "pasted_kml": str(pasted_kml_path),
        },
        "checks": {
            "real_msm": {
                "status": "SKIP",
                "reason": (
                    "This deterministic gate intentionally uses FakeWeatherProvider. "
                    "Live aloft weather uses the separately pinned jma-msm-wind "
                    "0.2.1 wheel, ecCodes, and JMA/RISH network access. Live "
                    "MSM-estimated QNH additionally uses the verified Pzs terrain "
                    "cache. Those live-data paths are checked separately."
                ),
            }
        },
    }
    try:
        _require(in_colab, "this acceptance script must run in Google Colab")
        _require(
            (3, 10) <= sys.version_info[:2] < (3, 13),
            f"unsupported Python version: {sys.version.split()[0]}",
        )
        _require(
            wheel_path.parent == content_root and pasted_kml_path.parent == content_root,
            "E2E inputs must be uploaded directly under the selected content root",
        )

        report["stage"] = "wheel_install"
        report["checks"]["wheel_install"] = _install_autonavlog_wheel(wheel_path)

        report["stage"] = "pasted_kml"
        kml_text, kml_check = _check_pasted_kml(pasted_kml_path)
        report["checks"]["pasted_kml"] = kml_check

        report["stage"] = "calculation"
        calculation_and_provider, calculation_check = _check_calculation()
        report["checks"]["calculation"] = calculation_check

        report["stage"] = "ui"
        content_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="autonavlog-e2e-",
            dir=content_root,
        ) as temporary_directory:
            report["checks"]["ui"] = _check_ui(
                Path(temporary_directory),
                kml_text,
                calculation_and_provider,
            )

        report["stage"] = "complete"
        report["status"] = "PASS"
        _write_report(report, result_path)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except BaseException as error:
        report["status"] = "FAIL"
        report["failure"] = {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
        }
        try:
            _write_report(report, result_path)
        except OSError as write_error:
            report["result_write_error"] = str(write_error)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


def scan_output_notebook_errors(notebook_path: str | Path) -> list[dict[str, Any]]:
    """Return every Jupyter ``error`` output recorded in an executed notebook."""

    path = Path(notebook_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    cells = document.get("cells")
    if not isinstance(cells, list):
        raise ValueError(f"notebook has no cells list: {path}")
    errors: list[dict[str, Any]] = []
    code_cell_index = 0
    for cell_index, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        code_cell_index += 1
        outputs = cell.get("outputs", [])
        if not isinstance(outputs, list):
            raise ValueError(f"code cell {cell_index} has invalid outputs")
        for output_index, output in enumerate(outputs):
            if not isinstance(output, dict) or output.get("output_type") != "error":
                continue
            traceback_lines = output.get("traceback", [])
            errors.append(
                {
                    "cell_index": cell_index,
                    "code_cell_index": code_cell_index,
                    "output_index": output_index,
                    "ename": str(output.get("ename", "")),
                    "evalue": str(output.get("evalue", "")),
                    "traceback": (
                        [str(line) for line in traceback_lines]
                        if isinstance(traceback_lines, list)
                        else [str(traceback_lines)]
                    ),
                }
            )
    return errors


def assert_output_notebook_clean(notebook_path: str | Path) -> None:
    """Raise when a Colab CLI output notebook contains an error output."""

    errors = scan_output_notebook_errors(notebook_path)
    if errors:
        summary = "; ".join(
            f"code cell {error['code_cell_index']}: {error['ename']}: {error['evalue']}"
            for error in errors
        )
        raise RuntimeError(f"executed notebook contains {len(errors)} error(s): {summary}")


def _scanner_path(arguments: Sequence[str]) -> Path | None:
    for index, argument in enumerate(arguments):
        if argument == "--scan-output-notebook":
            if index + 1 >= len(arguments):
                raise ValueError("--scan-output-notebook requires a path")
            return Path(arguments[index + 1])
        if argument.startswith("--scan-output-notebook="):
            return Path(argument.split("=", 1)[1])
    return None


def _run_entry_point(arguments: Sequence[str]) -> int:
    scanner_path = _scanner_path(arguments)
    if scanner_path is None:
        return run_colab_e2e()
    errors = scan_output_notebook_errors(scanner_path)
    payload = {
        "status": "FAIL" if errors else "PASS",
        "notebook": str(scanner_path),
        "error_count": len(errors),
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    exit_code = _run_entry_point(sys.argv[1:])
    if exit_code:
        raise SystemExit(exit_code)
