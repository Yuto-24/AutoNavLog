from __future__ import annotations

from collections.abc import Callable, Iterable
from html import escape
from typing import Any

from autonavlog.application.readiness import (
    EffectiveIssue,
    can_render_transfer_aid,
    derive_project_status,
)
from autonavlog.domain.calculation import CalculationOutcome, SectionResult
from autonavlog.domain.enums import ProjectStatus, ValueState
from autonavlog.domain.planning import load_persisted_ui_state
from autonavlog.domain.project import Project
from autonavlog.domain.values import AdoptedValue

from .formatting import ROUNDING, format_clock

DISCLAIMER = (
    "この出力は航空大学校「別添8-1」の複製、公式様式、承認済み運航資料ではありません。"
    "計算値を原票へ手書きで転記するための補助表です。"
    "利用者が適用規定、性能資料、WX、確認事項および原票の欄と照合してください。"
)


def _text_cell(value: str, css_class: str = "") -> str:
    class_attribute = f" class='{css_class}'" if css_class else ""
    return f"<td{class_attribute}>{escape(value)}</td>"


def _metadata_detail(metadata: dict[str, Any]) -> str:
    details: list[str] = []
    for key in (
        "source",
        "source_revision",
        "source_page",
        "type",
        "reason",
        "policy",
        "rule_version",
    ):
        value = metadata.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            details.append(f"{key}={value}")
    return " / ".join(details)


def _origin_text(
    value: AdoptedValue[Any],
    formatter: Callable[[Any], str],
) -> str:
    state = value.state
    metadata = value.automatic_metadata
    details = _metadata_detail(metadata)
    if state == ValueState.MANUAL_OVERRIDE:
        automatic = value.automatic_value
        automatic_text = "—" if automatic is None else formatter(automatic)
        origin = f"手動 / 自動 {automatic_text}"
    elif state == ValueState.PERFORMANCE_TABLE:
        origin = "性能表"
    elif state == ValueState.FIXED_RULE:
        origin = "規則値"
    elif state == ValueState.UNAVAILABLE:
        reason = metadata.get("reason_code") or metadata.get("availability") or "自動値なし"
        origin = f"未確定理由: {reason}"
    elif state == ValueState.WARNING:
        origin = "⚠ 注意値"
    else:
        origin = ""
    if details and state != ValueState.AUTO:
        origin = f"{origin} / {details}" if origin else details
    if value.warnings:
        warning_text = ", ".join(value.warnings)
        origin = f"{origin} / ⚠ {warning_text}" if origin else f"⚠ {warning_text}"
    return origin


def _value_cell(
    value: AdoptedValue[Any],
    formatter: Callable[[Any], str] = str,
    css_class: str = "num",
) -> str:
    adopted = value.adopted()
    shown = "—（未確定）" if adopted is None else formatter(adopted)
    classes = css_class + (" missing" if adopted is None else "")
    state_class = value.state.value.lower().replace("_", "-")
    origin = _origin_text(value, formatter)
    origin_html = "" if not origin else f"<small class='value-origin'>{escape(origin)}</small>"
    return (
        f"<td class='{classes} state-{state_class}' "
        f"title='{escape(value.state.value)}'><span>{escape(shown)}</span>{origin_html}</td>"
    )


def _optional_number(value: float | None, formatter: Callable[[float], str]) -> str:
    return "未確定" if value is None else formatter(value)


def _format_wind(result: SectionResult) -> str:
    speed = result.wind_speed_kt.adopted()
    direction = result.wind_direction_deg_from.adopted()
    if speed == 0:
        return "CALM"
    if speed is None or direction is None:
        return "未確定"
    return f"{ROUNDING.bearing(direction):03.0f}/{ROUNDING.wind(speed):.0f}"


def _phase_label(result: SectionResult) -> str:
    return {
        "CLIMB": "CLIMB",
        "CRUISE": "CRUISE",
        "DESCENT": "DESC",
        "VISUAL_ARRIVAL": "VIS ARR",
    }.get(result.phase.value, result.phase.value)


def _wind_cell(result: SectionResult) -> str:
    speed = result.wind_speed_kt
    direction = result.wind_direction_deg_from
    shown = _format_wind(result)
    missing = shown == "未確定"
    representative = (
        speed
        if speed.state == ValueState.MANUAL_OVERRIDE
        or direction.state != ValueState.MANUAL_OVERRIDE
        else direction
    )
    origin = _origin_text(representative, lambda value: f"{value:.0f}")
    if representative.state == ValueState.MANUAL_OVERRIDE:
        automatic_speed = speed.automatic_value
        automatic_direction = direction.automatic_value
        if automatic_speed == 0:
            automatic = "CALM"
        elif automatic_speed is None or automatic_direction is None:
            automatic = "—"
        else:
            automatic = (
                f"{ROUNDING.bearing(automatic_direction):03.0f}/"
                f"{ROUNDING.wind(automatic_speed):.0f}"
            )
        origin = f"手動 / 自動 {automatic}"
        warnings = tuple(dict.fromkeys((*speed.warnings, *direction.warnings)))
        if warnings:
            origin += " / ⚠ " + ", ".join(warnings)
    origin_html = "" if not origin else f"<small class='value-origin'>{escape(origin)}</small>"
    classes = "num missing" if missing else "num"
    return f"<td class='{classes}'><span>{escape(shown)}</span>{origin_html}</td>"


def _raw(value: Any | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        rendered = format(value, ".16g")
        return f"{rendered}.0" if value.is_integer() and "e" not in rendered.lower() else rendered
    return str(value)


def _raw_adopted_cell(value: AdoptedValue[Any], css_class: str = "num") -> str:
    adopted = value.adopted()
    shown = "—（未確定）" if adopted is None else _raw(adopted)
    classes = css_class + (" missing" if adopted is None else "")
    return f"<td class='{classes}'>{escape(shown)}</td>"


def _combined_adopted_cell(
    primary: AdoptedValue[float],
    cumulative: AdoptedValue[float],
    *,
    seconds_to_minutes: bool = False,
) -> str:
    primary_value = primary.adopted()
    cumulative_value = cumulative.adopted()
    if seconds_to_minutes:
        primary_value = None if primary_value is None else primary_value / 60
        cumulative_value = None if cumulative_value is None else cumulative_value / 60
    missing = primary_value is None or cumulative_value is None
    classes = "num combined" + (" missing" if missing else "")
    primary_text = "—" if primary_value is None else _raw(primary_value)
    cumulative_text = "—" if cumulative_value is None else _raw(cumulative_value)
    return (
        f"<td class='{classes}'><span>{escape(primary_text)}</span>"
        f"<small>{escape(cumulative_text)}</small></td>"
    )


def _raw_wind_cell(result: SectionResult) -> str:
    speed = result.wind_speed_kt.adopted()
    direction = result.wind_direction_deg_from.adopted()
    if speed == 0:
        shown = "CALM"
    elif speed is None or direction is None:
        shown = "—（未確定）"
    else:
        shown = f"{_raw(direction)}/{_raw(speed)}"
    classes = "num" + (" missing" if speed is None or (speed != 0 and direction is None) else "")
    return f"<td class='{classes}'>{escape(shown)}</td>"


def _section_row(result: SectionResult) -> str:
    cells = [
        _text_cell(result.from_name, "route-name"),
        _text_cell(result.to_name, "route-name"),
        _raw_adopted_cell(result.pressure_altitude_planning_ft),
        _raw_adopted_cell(result.temperature_c),
        _raw_adopted_cell(result.cas_kt),
        _raw_adopted_cell(result.tas_kt),
        _raw_adopted_cell(result.true_course_deg),
        _raw_adopted_cell(result.variation_deg_east),
        _raw_adopted_cell(result.magnetic_course_deg),
        _raw_wind_cell(result),
        _raw_adopted_cell(result.wca_deg),
        _raw_adopted_cell(result.magnetic_heading_deg),
        _combined_adopted_cell(
            result.zone_distance_nm,
            result.cumulative_distance_nm,
        ),
        _raw_adopted_cell(result.ground_speed_kt),
        _combined_adopted_cell(
            result.zone_ete_seconds,
            result.cumulative_ete_seconds,
            seconds_to_minutes=True,
        ),
        _text_cell("", "num"),
        _text_cell("", "num"),
        _text_cell("", "num"),
        _combined_adopted_cell(
            result.section_fuel_gal,
            result.remaining_fuel_gal,
        ),
    ]
    return "<tr>" + "".join(cells) + "</tr>"


def _project_summary(project: Project, outcome: CalculationOutcome) -> str:
    total_distance = (
        outcome.sections[-1].cumulative_distance_nm.adopted() if outcome.sections else None
    )
    total_time = outcome.sections[-1].cumulative_ete_seconds.adopted() if outcome.sections else None
    values = [
        ("DATE", project.flight_date.isoformat()),
        ("SHIP", project.ship_identifier or ""),
        ("FROM", project.departure_airport_id),
        ("TO", project.destination_airport_id),
        ("TTL DIST", _raw(total_distance)),
        ("TTL TIME", _raw(None if total_time is None else total_time / 60)),
        ("TAKE OFF", project.planned_departure_time_jst.strftime("%H:%M")),
        ("LANDING", ""),
        ("PILOT", project.pilot_name or ""),
    ]
    headings = "".join(f"<th>{escape(label)}</th>" for label, _ in values)
    cells = "".join(f"<td>{escape(value)}</td>" for _, value in values)
    return (
        "<table class='meta-table'><thead><tr>"
        f"{headings}</tr></thead><tbody><tr>{cells}</tr></tbody></table>"
    )


def _derived_points_table(outcome: CalculationOutcome) -> str:
    sections = {section.section_id: section for section in outcome.sections}
    rows: list[str] = []
    for point in outcome.derived_points:
        section = sections.get(point.section_id)
        leg = "不明" if section is None else f"{section.from_name} → {section.to_name}"
        estimated = (
            "未確定" if point.estimated_time_utc is None else format_clock(point.estimated_time_utc)
        )
        rows.append(
            "<tr>"
            f"<td>{escape(point.type.value)}</td>"
            f"<td>{escape(leg)}</td>"
            f"<td class='num'>{ROUNDING.distance(point.along_route_distance_nm):.1f}</td>"
            f"<td class='num'>{point.latitude_deg:.5f}</td>"
            f"<td class='num'>{point.longitude_deg:.5f}</td>"
            f"<td class='num'>{escape(estimated)}</td>"
            "</tr>"
        )
    body = "".join(rows) or "<tr><td colspan='6'>なし</td></tr>"
    return f"""
<table class="support-table derived-table">
  <thead><tr><th>TYPE</th><th>LEG</th><th>ALONG DIST<br><small>nm</small></th>
    <th>LAT</th><th>LON</th><th>ETO<br><small>JST</small></th></tr></thead>
  <tbody>{body}</tbody>
</table>
"""


def _info_table(outcome: CalculationOutcome) -> str:
    qnh = outcome.qnh_hpa.adopted()
    values = [
        ("CODE", "MSM" if outcome.selected_forecast_run_id else ""),
        ("TIME", outcome.selected_forecast_run_id or ""),
        ("WIND", ""),
        ("VIS", ""),
        ("CLD", ""),
        ("TEMP", ""),
        ("QNH", "" if qnh is None else f"{_raw(qnh)} hPa"),
    ]
    headings = "".join(f"<th>{escape(label)}</th>" for label, _ in values)
    cells = "".join(f"<td>{escape(value)}</td>" for _, value in values)
    return (
        "<table class='info-table'><thead><tr><th>INFO</th>"
        f"{headings}</tr></thead><tbody><tr><td></td>{cells}</tr></tbody></table>"
    )


def _phase_minutes(outcome: CalculationOutcome, phases: set[str]) -> float | None:
    values = [
        section.zone_ete_seconds.adopted()
        for section in outcome.sections
        if section.phase.value in phases
    ]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None) / 60


def _fuel_table(outcome: CalculationOutcome) -> str:
    fuel = outcome.fuel_plan
    total_time = outcome.sections[-1].cumulative_ete_seconds.adopted() if outcome.sections else None
    rows = [
        ("TAXI-RUN UP", None, fuel.taxi_runup_gal),
        ("BOF CLIMB", _phase_minutes(outcome, {"CLIMB"}), fuel.climb_gal),
        ("BOF CRUISE", _phase_minutes(outcome, {"CRUISE"}), fuel.cruise_gal),
        (
            "BOF DESCENT",
            _phase_minutes(outcome, {"DESCENT", "VISUAL_ARRIVAL"}),
            fuel.descent_gal,
        ),
        ("BOF TGL", None, fuel.tgl_gal),
        ("BOF ADDITIONAL", None, fuel.additional_gal),
        ("RESERVE", None, fuel.reserve_gal),
        ("MIN REQUIRED", None, fuel.min_required_gal),
        (
            "EXTRA",
            None if fuel.extra_endurance_seconds is None else fuel.extra_endurance_seconds / 60,
            fuel.extra_gal,
        ),
        ("TOTAL", None if total_time is None else total_time / 60, fuel.total_usable_gal),
    ]
    body = "".join(
        "<tr>"
        f"<th>{escape(label)}</th>"
        f"<td class='num'>{escape(_raw(time))}</td>"
        f"<td class='num'>{escape(_raw(amount))}</td>"
        "</tr>"
        for label, time, amount in rows
    )
    return (
        "<table class='fuel-table'><thead><tr><th></th><th>TIME</th><th>FUEL</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def _segment_sequence_label(sequences: list[int | None]) -> str:
    if not sequences:
        return "-"

    has_project_issue = None in sequences
    numbered = sorted({sequence + 1 for sequence in sequences if sequence is not None})
    ranges: list[str] = []
    if numbered:
        start = previous = numbered[0]
        for number in numbered[1:]:
            if number == previous + 1:
                previous = number
                continue
            ranges.append(str(start) if start == previous else f"{start}–{previous}")
            start = previous = number
        ranges.append(str(start) if start == previous else f"{start}–{previous}")

    labels = ["全体"] if has_project_issue else []
    labels.extend(ranges)
    return ", ".join(labels)


def _issues_table(
    outcome: CalculationOutcome,
    effective_issues: Iterable[EffectiveIssue] | None = None,
) -> str:
    grouped: dict[tuple[str, str, str, bool], list[int | None]] = {}
    issues = (
        outcome.issues
        if effective_issues is None
        else [
            item.issue.model_copy(
                update={
                    "severity": item.effective_severity,
                    "acknowledgement_required": item.effective_acknowledgement_required,
                }
            )
            for item in effective_issues
        ]
    )
    for issue in issues:
        key = (
            issue.severity.value,
            issue.code,
            issue.message,
            issue.acknowledgement_required,
        )
        grouped.setdefault(key, []).append(issue.segment_sequence)

    rows = "".join(
        "<tr>"
        f"<td class='{severity.lower()}'>{escape(severity)}</td>"
        f"<td class='num'>{escape(_segment_sequence_label(sequences))}</td>"
        f"<td>{escape(code)}</td>"
        f"<td>{escape(message)}</td>"
        "</tr>"
        for (severity, code, message, _acknowledgement_required), sequences in grouped.items()
    )
    if not rows:
        rows = "<tr><td colspan='4'>なし</td></tr>"
    return f"""
<table class="support-table issues-table">
  <thead><tr><th>SEVERITY</th><th>SEG</th><th>CODE</th><th>警告・未確定内容</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
"""


def _arrival_table(outcome: CalculationOutcome) -> str:
    arrival = outcome.arrival_altitude
    if arrival is None:
        return "<p class='missing'>VREP到着高度は未確定です。</p>"
    values = [
        ("VREP→ARP", f"{arrival.distance_nm_exact:.3f} nm"),
        ("空港標高（原値）", f"{arrival.airport_elevation_ft_msl:.0f} ft"),
        (
            "空港標高（100 ft half-up）",
            f"{arrival.airport_elevation_rounded_ft_msl} ft",
        ),
        ("導出場周経路高度", f"{arrival.derived_pattern_altitude_ft_msl} ft"),
        ("master場周経路高度", f"{arrival.pattern_altitude_ft_msl:.0f} ft"),
        ("5 NM基準高度", f"{arrival.base_vrep_altitude_ft_msl} ft"),
        (
            "5 NM超過（整数half-up）",
            f"{arrival.excess_distance_nm_rounded} nm",
        ),
        ("自動VREP高度", f"{arrival.automatic_altitude_ft_msl} ft"),
        (
            "採用VREP高度",
            f"{arrival.adopted_altitude_ft_msl} ft / {arrival.adopted_source.value}",
        ),
        ("規則", arrival.rule_version),
    ]
    if arrival.manual_override_reason is not None:
        values.append(("変則Entry理由", arrival.manual_override_reason))
    headers = "".join(f"<th>{escape(label)}</th>" for label, _ in values)
    cells = "".join(f"<td>{escape(value)}</td>" for _, value in values)
    return (
        "<table class='support-table arrival-table'><thead><tr>"
        f"{headers}</tr></thead><tbody><tr>{cells}</tr></tbody></table>"
    )


def _check_point_table(project: Project, outcome: CalculationOutcome) -> str:
    names = {item.id: item.name for item in project.visual_references}
    rows = "".join(
        "<tr>"
        f"<td>{escape(names.get(item.checkpoint_id, str(item.checkpoint_id)))}</td>"
        f"<td class='num'>{ROUNDING.distance(item.cumulative_distance_nm):.1f}</td>"
        f"<td class='num'>{ROUNDING.distance(item.along_section_distance_nm):.1f}</td>"
        f"<td class='num'>{item.cross_track_distance_nm:.2f}</td>"
        f"<td class='num'>{item.abeam_latitude_deg:.5f}</td>"
        f"<td class='num'>{item.abeam_longitude_deg:.5f}</td>"
        f"<td>{escape(item.policy_version)}</td>"
        "</tr>"
        for item in outcome.check_point_projections
    )
    if not rows:
        rows = "<tr><td colspan='7'>なし</td></tr>"
    return f"""
<table class="support-table cp-table">
  <thead><tr><th>CP</th><th>CUM DIST<br><small>nm</small></th>
    <th>LEG DIST<br><small>nm</small></th><th>XTRACK<br><small>nm</small></th>
    <th>ABEAM LAT</th><th>ABEAM LON</th><th>POLICY</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
"""


def _reference_provenance_table(project: Project) -> str:
    raw_state = project.metadata.get("ui_state")
    if raw_state is None:
        return "<p class='missing'>参照データsnapshotは未保存です。</p>"
    try:
        state = load_persisted_ui_state(raw_state)
    except (TypeError, ValueError):
        return "<p class='missing'>参照データsnapshotを検証できません。</p>"
    snapshot = state.reference_data_snapshot
    if snapshot is None:
        return "<p class='missing'>参照データsnapshotは未確定です。</p>"
    rows: list[str] = []

    def append_row(kind: str, row: Any) -> None:
        origin = row.origin
        dataset = "-" if origin is None else origin.dataset_id
        revision = "-" if origin is None else origin.dataset_revision
        rows.append(
            "<tr>"
            f"<td>{escape(kind)}</td><td>{escape(row.id)}</td>"
            f"<td>{escape(row.name)}</td><td>{escape(row.source)}</td>"
            f"<td>{escape(row.source_revision)}</td>"
            f"<td>{escape(dataset)}</td><td>{escape(revision)}</td>"
            "</tr>"
        )

    append_row("FROM", snapshot.departure_airport)
    append_row("TO", snapshot.destination_airport)
    for point in snapshot.route_points.values():
        append_row("ROUTE", point)
    for check_point in snapshot.check_points.values():
        append_row("CP", check_point)
    return f"""
<table class="support-table reference-table">
  <thead><tr><th>KIND</th><th>ID</th><th>NAME</th><th>SOURCE</th>
    <th>SOURCE REV</th><th>DATASET</th><th>DATASET REV</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
"""


def _route_source_notice(project: Project) -> str:
    polygon = any("Polygon (confirmed)" in node.source for node in project.route_nodes)
    if not polygon:
        return ""
    return (
        "<div class='transfer-disclaimer'>このRouteには、確認済みPolygonの"
        "外周をKML記載順で経路化した区間が含まれます。</div>"
    )


def _transfer_context(
    project: Project,
    outcome: CalculationOutcome,
    effective_issues: Iterable[EffectiveIssue] | None,
    calculation_is_current: bool | None,
    editable: bool,
) -> tuple[tuple[EffectiveIssue, ...] | None, ProjectStatus, bool]:
    if effective_issues is None:
        allowed = (
            outcome.status == ProjectStatus.READY_FOR_COPY
            and calculation_is_current is not False
            and editable
        )
        return None, outcome.status, allowed
    effective = tuple(effective_issues)
    status = derive_project_status(
        effective,
        project.acknowledged_warning_codes,
        outcome_exists=True,
    )
    allowed = can_render_transfer_aid(
        effective,
        project.acknowledged_warning_codes,
        outcome_exists=True,
        calculation_is_current=calculation_is_current is True,
        editable=editable,
    )
    return effective, status, allowed


def render_transfer_aid_html(
    project: Project,
    outcome: CalculationOutcome,
    *,
    effective_issues: Iterable[EffectiveIssue] | None = None,
    calculation_is_current: bool | None = None,
    editable: bool = True,
) -> str:
    """Render an unofficial, dense transcription aid for A4 landscape printing."""

    _effective, status, ready = _transfer_context(
        project,
        outcome,
        effective_issues,
        calculation_is_current,
        editable,
    )
    status_label = "転記可（要照合）" if ready else "転記不可"
    status_class = "transfer-ready" if ready else "transfer-blocked"
    section_rows = "".join(_section_row(result) for result in outcome.sections)
    if not section_rows:
        section_rows = "<tr><td class='missing' colspan='19'>Section計算結果なし</td></tr>"
    return f"""
<style>
@page {{ size: A4 landscape; margin: 8mm; }}
.autonavlog-transfer-aid {{
  color:#111; background:#fff;
  font-family:"Noto Sans CJK JP","Noto Sans JP","Yu Gothic","Hiragino Sans",
    Meiryo,Arial,sans-serif;
  max-width:1500px; margin:0 auto; padding:10px; line-height:1.15;
}}
.transfer-heading {{ display:flex; align-items:flex-end; justify-content:space-between;
  gap:10px; border-bottom:2px solid #111; padding-bottom:4px; }}
.transfer-heading h2 {{ font-size:18px; margin:0; }}
.transfer-heading .sub {{ font-size:9px; color:#444; }}
.transfer-status {{ font-size:15px; font-weight:800; border:2px solid;
  padding:4px 10px; white-space:nowrap; }}
.transfer-ready {{ color:#145a32; border-color:#145a32; background:#eafaf1; }}
.transfer-blocked {{ color:#b00020; border-color:#b00020; background:#fdeced; }}
.transfer-disclaimer {{ border:1.5px solid #b00020; margin:5px 0; padding:4px 6px;
  font-size:9px; font-weight:700; color:#7b0016; }}
.transfer-scroll {{ overflow-x:auto; }}
.autonavlog-transfer-aid table {{ border-collapse:collapse; width:100%; }}
.autonavlog-transfer-aid th,.autonavlog-transfer-aid td {{
  border:1px solid #555; padding:2px 3px; vertical-align:middle;
}}
.autonavlog-transfer-aid th {{ background:#e9ecef; font-weight:700; text-align:center; }}
.meta-table {{ font-size:7px; table-layout:auto; margin-bottom:4px; }}
.meta-table th {{ white-space:nowrap; }}
.meta-table td {{ min-width:30px; }}
.route-table {{ table-layout:fixed; font-size:6px; line-height:1.05; }}
.route-table th {{ white-space:normal; overflow-wrap:anywhere; }}
.route-table td {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.route-table .route-name {{ white-space:normal; overflow-wrap:anywhere; }}
.route-table small {{ font-size:5px; font-weight:400; }}
.route-table .combined small {{ display:block; border-top:1px solid #999; margin-top:2px;
  padding-top:2px; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.missing {{ color:#b00020; background:#fff0f0; font-weight:700; }}
.official-bottom {{ display:grid; grid-template-columns:2fr 1fr; gap:5px; margin-top:5px;
  align-items:start; }}
.info-table,.fuel-table {{ table-layout:fixed; font-size:6.5px; }}
.info-table td {{ height:22px; }}
.fuel-table th:first-child {{ width:48%; text-align:left; }}
@media print {{
  html,body {{ margin:0; padding:0; background:#fff; }}
  .autonavlog-transfer-aid {{ max-width:none; width:auto; margin:0; padding:0; }}
  .transfer-scroll {{ overflow:visible; }}
  .transfer-heading h2 {{ font-size:12pt; }}
  .transfer-status {{ font-size:9pt; }}
  .transfer-disclaimer {{ font-size:6.5pt; }}
  .meta-table {{ font-size:5.2pt; }}
  .route-table {{ font-size:4.8pt; line-height:1; }}
  .route-table th,.route-table td {{ padding:.55mm .35mm; }}
  .info-table,.fuel-table {{ font-size:5.2pt; }}
  .official-bottom,.route-table tr {{ break-inside:avoid; }}
}}
</style>
<main class="autonavlog-transfer-aid">
  <header class="transfer-heading">
    <div><h2>AutoNavLog 転記補助表（非公式）</h2>
      <div class="sub">地上準備・原票への手書き転記補助専用</div></div>
    <div class="transfer-status {status_class}">{status_label}<br>
      <small>{escape(status.value)}</small></div>
  </header>
  <div class="transfer-disclaimer">{escape(DISCLAIMER)}</div>
  {_project_summary(project, outcome)}
  <div class="transfer-scroll">
    <table class="route-table">
      <thead><tr>
        <th>FROM</th><th>TO</th><th>PA<br><small>ft</small></th>
        <th>TOAT<br><small>°C</small></th><th>CAS<br><small>kt</small></th>
        <th>TAS<br><small>kt</small></th><th>TC<br><small>°T</small></th>
        <th>VAR<br><small>°E</small></th><th>MC<br><small>°M</small></th>
        <th>WIND<br><small>°/kt</small></th><th>WCA<br><small>°</small></th>
        <th>MH<br><small>°M</small></th>
        <th>ZONE / CUM<br>DIST<br><small>nm</small></th>
        <th>GS<br><small>kt</small></th>
        <th>ZONE / CUM<br>ETE<br><small>min</small></th>
        <th>ETO</th><th>ATO</th><th>ATE</th>
        <th>SECT / REM<br>FUEL<br><small>gal</small></th>
      </tr></thead>
      <tbody>{section_rows}</tbody>
    </table>
  </div>
  <div class="official-bottom">
    {_info_table(outcome)}
    {_fuel_table(outcome)}
  </div>
</main>
"""


def render_transfer_aid_document(
    project: Project,
    outcome: CalculationOutcome,
    *,
    effective_issues: Iterable[EffectiveIssue] | None = None,
    calculation_is_current: bool | None = None,
    editable: bool = True,
) -> str:
    """Render a standalone UTF-8 A4 document that can be opened and printed."""

    effective, _status, allowed = _transfer_context(
        project,
        outcome,
        effective_issues,
        calculation_is_current,
        editable,
    )
    if not allowed:
        blockers = (
            []
            if effective is None
            else [item.ctx.code for item in effective if item.effective_severity.value == "BLOCKER"]
        )
        suffix = "" if not blockers else f" ({', '.join(blockers)})"
        raise ValueError(
            "転記補助HTMLは未確定項目を解消し、確認事項を承認してから出力してください。" + suffix
        )
    aid = render_transfer_aid_html(
        project,
        outcome,
        effective_issues=effective,
        calculation_is_current=calculation_is_current,
        editable=editable,
    )
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(project.name)} - AutoNavLog 転記補助表（非公式）</title>
  <style>
    .print-controls {{ max-width:1500px; margin:8px auto; text-align:right; }}
    .print-controls button {{ font-size:16px; padding:8px 18px; cursor:pointer; }}
    @media print {{ .print-controls {{ display:none; }} }}
  </style>
</head>
<body>
  <div class="print-controls">
    <button type="button" onclick="window.print()">A4横で印刷</button>
  </div>
  {aid}
</body>
</html>
"""
