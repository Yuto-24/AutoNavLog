from __future__ import annotations

from html import escape

from autonavlog.domain.calculation import CalculationOutcome, SectionResult
from autonavlog.domain.enums import ValueState
from autonavlog.domain.project import Project

from .formatting import (
    ROUNDING,
    format_adopted,
    format_clock,
    format_duration,
    provenance_details,
)


def _field(label: str, value: str, state: str) -> str:
    return (
        "<div class='copy-field'>"
        f"<span class='label'>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        f"<small>{escape(state)}</small>"
        "</div>"
    )


def _format_wind(result: SectionResult) -> str:
    speed = result.wind_speed_kt.adopted()
    direction = result.wind_direction_deg_from.adopted()
    if speed == 0:
        return "CALM"
    if speed is None or direction is None:
        return "未確定"
    return f"{ROUNDING.bearing(direction):.0f}° / {ROUNDING.wind(speed):.0f} kt"


def _section_fields(result: SectionResult) -> str:
    values = [
        ("FROM", result.from_name, "INPUT"),
        ("TO", result.to_name, "INPUT"),
        (
            "PA",
            format_adopted(result.pressure_altitude_planning_ft, lambda v: f"{v:.0f} ft"),
            result.pressure_altitude_planning_ft.state.value,
        ),
        (
            "TC",
            format_adopted(result.true_course_deg, lambda v: f"{ROUNDING.bearing(v):.0f}°"),
            result.true_course_deg.state.value,
        ),
        (
            "VAR",
            format_adopted(result.variation_deg_east, lambda v: f"{v:+.0f}°"),
            result.variation_deg_east.state.value,
        ),
        (
            "MC",
            format_adopted(result.magnetic_course_deg, lambda v: f"{ROUNDING.bearing(v):.0f}°"),
            result.magnetic_course_deg.state.value,
        ),
        (
            "WIND",
            _format_wind(result),
            result.wind_speed_kt.state.value,
        ),
        (
            "WCA",
            format_adopted(result.wca_deg, lambda v: f"{v:+.0f}°"),
            result.wca_deg.state.value,
        ),
        (
            "MH",
            format_adopted(result.magnetic_heading_deg, lambda v: f"{ROUNDING.bearing(v):.0f}°"),
            result.magnetic_heading_deg.state.value,
        ),
        (
            "TOAT",
            format_adopted(result.temperature_c, lambda v: f"{ROUNDING.temperature(v):.0f}°C"),
            result.temperature_c.state.value,
        ),
        ("CAS", format_adopted(result.cas_kt, lambda v: f"{v:.0f} kt"), result.cas_kt.state.value),
        ("TAS", format_adopted(result.tas_kt, lambda v: f"{v:.0f} kt"), result.tas_kt.state.value),
        (
            "GS",
            format_adopted(result.ground_speed_kt, lambda v: f"{v:.0f} kt"),
            result.ground_speed_kt.state.value,
        ),
        (
            "ZONE DIST",
            format_adopted(result.zone_distance_nm, lambda v: f"{ROUNDING.distance(v):.1f} nm"),
            result.zone_distance_nm.state.value,
        ),
        (
            "CUM DIST",
            format_adopted(
                result.cumulative_distance_nm,
                lambda v: f"{ROUNDING.distance(v):.1f} nm",
            ),
            result.cumulative_distance_nm.state.value,
        ),
        (
            "ZONE ETE",
            format_adopted(result.zone_ete_seconds, format_duration),
            result.zone_ete_seconds.state.value,
        ),
        (
            "CUM ETE",
            format_adopted(result.cumulative_ete_seconds, format_duration),
            result.cumulative_ete_seconds.state.value,
        ),
        (
            "ETO",
            format_adopted(result.eto_utc, format_clock),
            result.eto_utc.state.value,
        ),
        (
            "SECT FUEL",
            format_adopted(result.section_fuel_gal, lambda v: f"{ROUNDING.fuel(v):.1f} gal"),
            result.section_fuel_gal.state.value,
        ),
        (
            "REM FUEL",
            format_adopted(result.remaining_fuel_gal, lambda v: f"{ROUNDING.fuel(v):.1f} gal"),
            result.remaining_fuel_gal.state.value,
        ),
    ]
    fields = "".join(_field(*item) for item in values)
    details = "".join(
        (
            provenance_details("PA", result.pressure_altitude_planning_ft),
            provenance_details("TC", result.true_course_deg),
            provenance_details("WIND", result.wind_speed_kt),
            provenance_details("TAS", result.tas_kt),
            provenance_details("GS", result.ground_speed_kt),
            provenance_details("ETE", result.zone_ete_seconds),
            provenance_details("FUEL", result.section_fuel_gal),
        )
    )
    return (
        f"<section class='copy-section'><h3>{escape(result.from_name)} → "
        f"{escape(result.to_name)}</h3>{fields}<div class='evidence'>{details}</div></section>"
    )


def _fuel_row(label: str, value: float | None) -> str:
    shown = "未確定" if value is None else f"{ROUNDING.fuel(value):.1f} gal"
    return (
        f"<div class='fuel-row'><span>{escape(label)}</span>"
        f"<strong>{escape(shown)}</strong></div>"
    )


def render_clearcopy_html(project: Project, outcome: CalculationOutcome) -> str:
    total_distance = (
        outcome.sections[-1].cumulative_distance_nm.adopted() if outcome.sections else None
    )
    total_time = (
        outcome.sections[-1].cumulative_ete_seconds.adopted() if outcome.sections else None
    )
    qnh = format_adopted(outcome.qnh_hpa, lambda v: f"{ROUNDING.qnh(v):.0f} hPa")
    qnh_label = (
        "QNH (手動)"
        if outcome.qnh_hpa.state == ValueState.MANUAL_OVERRIDE
        else "MSM推定QNH"
    )
    summary = "".join(
        (
            _field("PILOT", project.pilot_name or "未入力", "INPUT"),
            _field("DATE", project.flight_date.isoformat(), "INPUT"),
            _field("SHIP", project.ship_identifier or "未入力", "INPUT"),
            _field(
                "FROM / TO",
                f"{project.departure_airport_id} / {project.destination_airport_id}",
                "INPUT",
            ),
            _field(
                "TTL DIST",
                "未確定"
                if total_distance is None
                else f"{ROUNDING.distance(total_distance):.1f} nm",
                "CALCULATED",
            ),
            _field(
                "TTL TIME",
                "未確定" if total_time is None else format_duration(total_time),
                "CALCULATED",
            ),
            _field(qnh_label, qnh, outcome.qnh_hpa.state.value),
            _field("FORECAST RUN", outcome.selected_forecast_run_id or "未確定", "FIXED"),
        )
    )
    issues = "".join(
        f"<li class='{issue.severity.value.lower()}'><strong>{escape(issue.code)}</strong> "
        f"{escape(issue.message)}</li>"
        for issue in outcome.issues
    ) or "<li>なし</li>"
    fuel = outcome.fuel_plan
    fuel_rows = "".join(
        (
            _fuel_row("TOTAL", fuel.total_usable_gal),
            _fuel_row("TAXI / RUN-UP", fuel.taxi_runup_gal),
            _fuel_row("CLIMB", fuel.climb_gal),
            _fuel_row("CRUISE", fuel.cruise_gal),
            _fuel_row("DESCENT / ARRIVAL", fuel.descent_gal),
            _fuel_row("ADDITIONAL", fuel.additional_gal),
            _fuel_row("TGL", fuel.tgl_gal),
            _fuel_row("RESERVE", fuel.reserve_gal),
            _fuel_row("MIN REQUIRED", fuel.min_required_gal),
            _fuel_row("EXTRA", fuel.extra_gal),
        )
    )
    sections = "".join(_section_fields(result) for result in outcome.sections)
    return f"""
<style>
.autonavlog-copy {{ color:#17202a; background:#f7f8fa; font-family:Arial,sans-serif;
  max-width:760px; margin:0 auto; padding:16px; letter-spacing:0; }}
.autonavlog-copy h2 {{ font-size:22px; margin:0 0 4px; }}
.autonavlog-copy h3 {{ font-size:17px; margin:0 0 12px; }}
.notice {{ border-left:4px solid #c0392b; padding:8px 12px; background:#fff; margin:12px 0; }}
.status-line {{ display:flex; justify-content:space-between; gap:8px; padding:10px 0;
  border-bottom:1px solid #ccd1d1; }}
.summary-grid,.copy-section {{ display:grid; grid-template-columns:1fr; gap:8px; }}
.copy-section {{ margin:18px 0; padding-top:14px; border-top:2px solid #34495e; }}
.copy-field {{ display:grid; grid-template-columns:minmax(92px,1fr) minmax(110px,2fr) auto;
  align-items:center; min-height:44px; padding:6px 8px; background:#fff;
  border:1px solid #d5d8dc; }}
.copy-field .label {{ font-size:12px; font-weight:700; color:#4d5656; }}
.copy-field strong {{ text-align:right; overflow-wrap:anywhere; }}
.copy-field small,.state {{ color:#566573; font-size:10px; }}
.fuel-row {{ display:flex; justify-content:space-between; padding:9px 4px;
  border-bottom:1px solid #d5d8dc; }}
.evidence details {{ margin:6px 0; background:#fff; border:1px solid #d5d8dc; padding:8px; }}
.evidence dl {{ font-size:12px; overflow-wrap:anywhere; }}
.blocker {{ color:#922b21; }} .warning {{ color:#7d6608; }}
@media (min-width:640px) {{
  .summary-grid {{ grid-template-columns:1fr 1fr; }}
  .copy-section {{ grid-template-columns:1fr 1fr; }}
  .copy-section h3,.copy-section .evidence {{ grid-column:1/-1; }}
}}
</style>
<main class="autonavlog-copy">
  <h2>AutoNavLog 清書ビュー</h2>
  <div class="notice">地上準備専用。値を確認し、別添8-1へ手書きで転記してください。</div>
  <div class="status-line"><strong>{escape(outcome.status.value)}</strong>
    <span>Policy {escape(outcome.policy_version)}</span></div>
  <section><h3>PROJECT</h3><div class="summary-grid">{summary}</div></section>
  <section><h3>警告・未確定</h3><ul>{issues}</ul></section>
  {sections}
  <section><h3>燃料計画</h3>{fuel_rows}</section>
</main>
"""
