from __future__ import annotations

from collections.abc import Iterable
from html import escape
from math import cos, radians
from typing import Any

from autonavlog.application.readiness import (
    EffectiveIssue,
    can_render_transfer_aid,
    derive_project_status,
)
from autonavlog.domain.calculation import (
    CalculationOutcome,
    NavLogDisplayCell,
    NavLogDisplayRow,
)
from autonavlog.domain.enums import (
    AdoptedSource,
    DisplayCellState,
    ProjectStatus,
)
from autonavlog.domain.planning import RjfmGuidanceStatus, load_persisted_ui_state
from autonavlog.domain.project import Project
from autonavlog.nav.rounding import round_half_up

from .formatting import ROUNDING, format_clock

DISCLAIMER = (
    "この出力は航空大学校「別添8-1」の複製、公式様式、承認済み運航資料ではありません。"
    "計算値を原票へ手書きで転記するための補助表です。"
    "利用者が適用規定、性能資料、WX、確認事項および原票の欄と照合してください。"
)


def _text_cell(value: str, css_class: str = "") -> str:
    class_attribute = f" class='{css_class}'" if css_class else ""
    return f"<td{class_attribute} data-display-text='{escape(value)}'>{escape(value)}</td>"


def _raw(value: Any | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        rendered = format(value, ".16g")
        return f"{rendered}.0" if value.is_integer() and "e" not in rendered.lower() else rendered
    return str(value)


def _display_cell(cell: NavLogDisplayCell, css_class: str = "num") -> str:
    shown = cell.text or ""
    classes = [css_class, f"display-{cell.state.value.lower().replace('_', '-')}"]
    if cell.state == DisplayCellState.UNAVAILABLE:
        classes.append("missing")
    if cell.manual:
        classes.append("manual")
    origin = "<small class='value-origin'>手入力</small>" if cell.manual else ""
    return (
        f"<td class='{' '.join(classes)}' data-cell-state='{cell.state.value}' "
        f"data-display-text='{escape(shown)}'>"
        f"<span>{escape(shown)}</span>{origin}</td>"
    )


def _section_row(result: NavLogDisplayRow) -> str:
    if result.row_type == "LEG_SEPARATOR":
        return (
            f"<tr class='leg-separator' aria-hidden='true' data-row-type='LEG_SEPARATOR' "
            f"data-row-sequence='{result.sequence}'><td colspan='19'></td></tr>"
        )
    cells = [
        _text_cell(result.from_name, "route-name"),
        _text_cell(result.to_name, "route-name"),
        _display_cell(result.pa),
        _display_cell(result.toat),
        _display_cell(result.cas),
        _display_cell(result.tas),
        _display_cell(result.tc),
        _display_cell(result.variation),
        _display_cell(result.mc),
        _display_cell(result.wind),
        _display_cell(result.wca),
        _display_cell(result.mh),
        _display_cell(result.distance),
        _display_cell(result.gs),
        _display_cell(result.ete),
        _display_cell(result.eto),
        _display_cell(result.ato),
        _display_cell(result.ate),
        _display_cell(result.fuel),
    ]
    row_class = result.row_type.lower().replace("_", "-")
    return (
        f"<tr class='{row_class}' data-row-type='{result.row_type}' "
        f"data-row-sequence='{result.sequence}'>" + "".join(cells) + "</tr>"
    )


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
        (
            "TTL DIST",
            "" if total_distance is None else f"{ROUNDING.distance(total_distance):.1f}",
        ),
        (
            "TTL TIME",
            "" if total_time is None else f"{ROUNDING.duration_minutes(total_time):.1f}",
        ),
        ("TAKE OFF", project.planned_departure_time_jst.strftime("%H:%M")),
        ("LANDING", ""),
        ("PILOT", project.pilot_name or ""),
        ("WX", "FTD FIXED / ISA" if project.weather_mode == "FTD" else "FORECAST"),
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
    qnh_metadata = outcome.qnh_hpa.automatic_metadata
    raw_values = qnh_metadata.get("values")
    qnh_values = raw_values if isinstance(raw_values, dict) else {}
    qnh_method = (
        "MANUAL"
        if outcome.qnh_hpa.adopted_source == AdoptedSource.MANUAL
        else str(qnh_values.get("qnh_method") or qnh_metadata.get("qnh_method") or "")
    )
    qnh_warnings = ", ".join(outcome.qnh_hpa.warnings)
    qnh_text = "" if qnh is None else f"{_raw(qnh)} hPa"
    qnh_details = " / ".join(item for item in (qnh_method, qnh_warnings) if item)
    if qnh_details:
        qnh_text = f"{qnh_text} / {qnh_details}" if qnh_text else qnh_details
    values = [
        ("CODE", "MSM" if outcome.selected_forecast_run_id else ""),
        ("TIME", outcome.selected_forecast_run_id or ""),
        ("WIND", ""),
        ("VIS", ""),
        ("CLD", ""),
        ("TEMP", ""),
        ("QNH", qnh_text),
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
    if not outcome.sections or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None) / 60


def _fuel_time(minutes: float | None) -> str:
    if minutes is None:
        return ""
    rounded = int(round_half_up(minutes, 1.0))
    hours, remaining_minutes = divmod(rounded, 60)
    return (
        "<div class='fuel-time'>"
        f"<span>{hours}</span><span>:</span><span>{remaining_minutes:02d}</span>"
        "</div>"
    )


def _fuel_amount(amount: float | None) -> str:
    shown = "" if amount is None else f"{ROUNDING.fuel(amount):.1f}"
    return f"<div class='fuel-amount'><span>{shown}</span><span>G</span></div>"


def _fuel_row(
    label: str,
    minutes: float | None,
    amount: float | None,
    *,
    row_class: str = "",
    label_class: str = "",
) -> str:
    class_attribute = f" class='{row_class}'" if row_class else ""
    return (
        f"<tr{class_attribute}><td colspan='3' class='{label_class}'>{escape(label)}</td>"
        f"<td class='fuel-time-cell'>{_fuel_time(minutes)}</td>"
        f"<td class='fuel-amount-cell'>{_fuel_amount(amount)}</td></tr>"
    )


def _fuel_table(outcome: CalculationOutcome) -> str:
    fuel = outcome.fuel_plan
    climb_minutes = _phase_minutes(outcome, {"CLIMB"})
    cruise_minutes = _phase_minutes(outcome, {"CRUISE"})
    descent_minutes = _phase_minutes(outcome, {"DESCENT", "VISUAL_ARRIVAL"})
    tgl_minutes = fuel.tgl_gal / 2.0 * 7.0
    required_parts = [
        10.0,
        climb_minutes,
        cruise_minutes,
        descent_minutes,
        tgl_minutes,
        10.0,
        45.0,
    ]
    min_required_minutes = (
        None
        if any(value is None for value in required_parts)
        else sum(value for value in required_parts if value is not None)
    )
    extra_minutes = (
        None if fuel.extra_endurance_seconds is None else fuel.extra_endurance_seconds / 60
    )
    total_minutes = (
        None
        if min_required_minutes is None or extra_minutes is None
        else min_required_minutes + extra_minutes
    )
    bof_rows = [
        ("CLIMB", climb_minutes, fuel.climb_gal),
        ("CRUISE", cruise_minutes, fuel.cruise_gal),
        ("DESCENT", descent_minutes, fuel.descent_gal),
        ("TGL", tgl_minutes, fuel.tgl_gal),
        ("ADDITIONAL", 10.0, fuel.additional_gal),
    ]
    bof_html = "".join(
        "<tr>"
        + (
            "<td rowspan='6' class='fuel-gray'></td><td rowspan='5' class='fuel-bof'>BOF</td>"
            if index == 0
            else ""
        )
        + f"<td class='fuel-phase'>{escape(label)}</td>"
        + f"<td class='fuel-time-cell'>{_fuel_time(minutes)}</td>"
        + f"<td class='fuel-amount-cell'>{_fuel_amount(amount)}</td></tr>"
        for index, (label, minutes, amount) in enumerate(bof_rows)
    )
    reserve = (
        "<tr class='fuel-reserve-row'><td colspan='2' class='fuel-strong'>RESERVE</td>"
        f"<td class='fuel-time-cell'>{_fuel_time(45.0)}</td>"
        f"<td class='fuel-amount-cell'>{_fuel_amount(fuel.reserve_gal)}</td></tr>"
    )
    min_required_row = _fuel_row(
        "MIN REQUIRED",
        min_required_minutes,
        fuel.min_required_gal,
        row_class="fuel-min-row",
        label_class="fuel-gray fuel-strong",
    )
    extra_row = _fuel_row(
        "EXTRA",
        extra_minutes,
        fuel.extra_gal,
        row_class="fuel-extra-row",
        label_class="fuel-strong",
    )
    total_row = _fuel_row(
        "TOTAL",
        total_minutes,
        fuel.total_usable_gal,
        label_class="fuel-strong",
    )
    return f"""
<table class='fuel-table'>
  <colgroup><col><col><col><col><col></colgroup>
  <thead><tr><th colspan='3'></th><th>TIME</th><th>FUEL</th></tr></thead>
  <tbody>
    <tr><td class='fuel-gray'></td><td colspan='2' class='fuel-strong'>TAXI・RUN UP</td>
      <td class='fuel-time-cell'>{_fuel_time(10.0)}</td>
      <td class='fuel-amount-cell'>{_fuel_amount(fuel.taxi_runup_gal)}</td></tr>
    {bof_html}
    {reserve}
    {min_required_row}
    {extra_row}
    {total_row}
  </tbody>
</table>
"""


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
        ("master場周経路高度", f"{arrival.pattern_altitude_ft_msl:.0f} ft"),
        (
            "採用場周経路高度",
            f"{arrival.selected_pattern_altitude_ft_msl} ft / "
            f"{arrival.selected_pattern_altitude_source.value}",
        ),
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


def _rjfm_guidance_svg(outcome: CalculationOutcome) -> str:
    guidance = outcome.rjfm_departure_guidance
    if guidance is None:
        return ""
    usable = [
        candidate
        for candidate in guidance.candidates
        if candidate.status in {RjfmGuidanceStatus.VALID, RjfmGuidanceStatus.WARNING}
        and candidate.path
    ]
    coordinates = [
        (point.latitude_deg, point.longitude_deg)
        for candidate in usable
        for point in candidate.path
    ] + [(point.latitude_deg, point.longitude_deg) for point in guidance.center_route]
    if len(coordinates) < 2:
        return ""
    mean_latitude = sum(float(item[0]) for item in coordinates) / len(coordinates)
    x_scale = max(cos(radians(mean_latitude)), 0.1)
    projected = [(float(lon) * x_scale, float(lat)) for lat, lon in coordinates]
    min_x = min(point[0] for point in projected)
    max_x = max(point[0] for point in projected)
    min_y = min(point[1] for point in projected)
    max_y = max(point[1] for point in projected)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)

    def xy(latitude: float, longitude: float) -> tuple[float, float]:
        x = (longitude * x_scale - min_x) / span_x * 700 + 20
        y = 300 - (latitude - min_y) / span_y * 260
        return x, y

    colors = {"09": "#2368a2", "27": "#6e4aa0"}
    lines: list[str] = []
    for candidate in usable:
        points = " ".join(
            f"{xy(float(point.latitude_deg), float(point.longitude_deg))[0]:.1f},"
            f"{xy(float(point.latitude_deg), float(point.longitude_deg))[1]:.1f}"
            for point in candidate.path
        )
        lines.append(
            f"<polyline points='{points}' fill='none' stroke='{colors[candidate.runway]}' "
            "stroke-width='3.5' stroke-linejoin='round' stroke-linecap='round'/>"
        )
    center_points = " ".join(
        f"{xy(float(point.latitude_deg), float(point.longitude_deg))[0]:.1f},"
        f"{xy(float(point.latitude_deg), float(point.longitude_deg))[1]:.1f}"
        for point in guidance.center_route
    )
    lines.append(
        f"<polyline points='{center_points}' fill='none' stroke='#198754' "
        "stroke-width='3' stroke-dasharray='7 4'/>"
    )
    labels = []
    for name, point in zip(
        ("UMK/RCA 5500", "OVER FIELD", "OMARU"),
        guidance.center_route,
        strict=True,
    ):
        x, y = xy(float(point.latitude_deg), float(point.longitude_deg))
        labels.append(
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='4' fill='#198754'/>"
            f"<text x='{x + 7:.1f}' y='{y - 6:.1f}'>{escape(name)}</text>"
        )
    legend = "".join(
        f"<text x='{20 + index * 140}' y='318' fill='{colors[candidate.runway]}'>"
        f"RWY{candidate.runway}</text>"
        for index, candidate in enumerate(usable)
    )
    return (
        "<svg class='rjfm-guidance-map' viewBox='0 0 740 330' role='img' "
        "aria-label='RJFM北行き出発案内経路'>"
        + "".join(lines)
        + "".join(labels)
        + legend
        + "</svg>"
    )


def _rjfm_candidate_summary(candidate: Any) -> str:
    status = {
        RjfmGuidanceStatus.VALID: "成立",
        RjfmGuidanceStatus.WARNING: "成立（注意）",
    }.get(candidate.status, candidate.status.value)
    turn_direction = (
        "" if candidate.turn_direction is None else str(candidate.turn_direction.value)
    )
    turn_label = {
        "LEFT": "左",
        "RIGHT": "右",
    }.get(turn_direction, "方向未確定")
    turn_method = {
        "FIXED_BANK_20": f"{turn_label}20°バンク",
        "ADJUSTED_MAX_RADIUS": f"{turn_label}旋回・最大半径へ調整",
        "NONE": "旋回解なし",
    }.get(candidate.turn_method.value, candidate.turn_method.value)
    delta = candidate.expected_time_delta_seconds
    delta_text = "算出不可"
    if delta is not None:
        minutes = round_half_up(float(delta) / 60.0, 0.5)
        delta_text = f"{'+' if minutes > 0 else ''}{minutes:.1f}分"
    radial = (
        "-"
        if candidate.turn_entry_radial_deg is None
        else f"R-{round_half_up(float(candidate.turn_entry_radial_deg), 1.0):03.0f}"
    )
    dme = (
        "-"
        if candidate.turn_entry_dme_nm is None
        else f"{round_half_up(float(candidate.turn_entry_dme_nm), 0.1):.1f} DME"
    )
    partial = (
        "-"
        if candidate.partial_turn_deg is None
        else f"{round_half_up(float(candidate.partial_turn_deg), 1.0):.0f}°"
    )
    return (
        f"RWY{candidate.runway}: {escape(status)} / "
        f"{escape(turn_method)} / "
        f"{turn_label}360°×{candidate.full_turns} + {partial} / "
        f"進入 {radial} {dme} / UMK 5500 ft / LOSS・GAIN {delta_text}"
    )


def _rjfm_guidance_section(outcome: CalculationOutcome) -> str:
    guidance = outcome.rjfm_departure_guidance
    if guidance is None:
        return ""
    usable = [
        candidate
        for candidate in guidance.candidates
        if candidate.status in {RjfmGuidanceStatus.VALID, RjfmGuidanceStatus.WARNING}
        and candidate.path
    ]
    invalid = [
        candidate
        for candidate in guidance.candidates
        if candidate.status in {RjfmGuidanceStatus.HARD_INVALID, RjfmGuidanceStatus.UNAVAILABLE}
    ]
    summaries = "".join(f"<li>{_rjfm_candidate_summary(candidate)}</li>" for candidate in usable)
    advisories = "".join(
        "<li>"
        f"RWY{candidate.runway}: "
        + escape(
            " / ".join(
                constraint.message
                for constraint in candidate.constraints
                if not constraint.hard and not constraint.passed
            )
        )
        + "</li>"
        for candidate in usable
        if candidate.status == RjfmGuidanceStatus.WARNING
        and any(
            not constraint.hard and not constraint.passed for constraint in candidate.constraints
        )
    )
    failures = "".join(
        "<li>"
        f"RWY{candidate.runway}: "
        + escape(
            " / ".join(
                constraint.message
                for constraint in candidate.constraints
                if constraint.hard and not constraint.passed
            )
            or "案内経路を生成できません。"
        )
        + "</li>"
        for candidate in invalid
    )
    source_dates = " / ".join(
        f"{escape(name)}: {escape(value)}"
        for name, value in sorted(guidance.source_effective_dates.items())
    )
    invalid_html = (
        f"<p class='rjfm-invalid'><strong>案内不成立:</strong></p><ul>{failures}</ul>"
        if failures
        else ""
    )
    advisory_html = (
        f"<p class='rjfm-advisory'><strong>成立候補の注意条件:</strong></p><ul>{advisories}</ul>"
        if advisories
        else ""
    )
    return f"""
<section class='rjfm-guidance'>
  <h3>RJFM北行き RCA / CENTER ROUTE 案内（非公式）</h3>
  <div class='rjfm-guidance-layout'>
    {_rjfm_guidance_svg(outcome)}
    <div>
      <p><strong>主NAVLOG例外:</strong> RJFM → OMARUを1つの親Legとして表示し、
      UMK/RCA 5500 ftをCLIMBからCRUISEへの内部境界とします。
      DIST・ETE・FUELは子区間の合計、TC・VAR・MCはRJFM → OMARUの直行値です。</p>
      <p><strong>CENTER ROUTE:</strong> UMK → OVER FIELD → OMARU / 5500 ft</p>
      <ul>{summaries or "<li>使用可能な案内経路なし</li>"}</ul>
      {advisory_html}
      {invalid_html}
      <p><strong>参照版:</strong> {escape(guidance.reference_revision)}<br>
      <strong>payload SHA-256:</strong> {escape(guidance.reference_content_fingerprint)}<br>
      {source_dates}</p>
      <p>ATC指示を優先してください。地形・障害物・未定義の他空域は本案内の保証対象外です。</p>
    </div>
  </div>
</section>
"""


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
    section_rows = "".join(_section_row(result) for result in outcome.display_rows)
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
.route-table .leg-separator td {{ height:7px; border-left:0; border-right:0; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.missing {{ color:#b00020; background:#fff0f0; font-weight:700; }}
.official-bottom {{ display:grid; grid-template-columns:2fr 1fr; gap:5px; margin-top:5px;
  align-items:start; }}
.info-table,.fuel-table {{ table-layout:fixed; font-size:6.5px; }}
.info-table td {{ height:22px; }}
.fuel-table {{ border:2px solid #111 !important; font-family:"Arial Narrow",Arial,sans-serif; }}
.fuel-table col:nth-child(1) {{ width:10%; }}
.fuel-table col:nth-child(2) {{ width:15.5%; }}
.fuel-table col:nth-child(3) {{ width:25.2%; }}
.fuel-table col:nth-child(4) {{ width:24%; }}
.fuel-table col:nth-child(5) {{ width:25.3%; }}
.fuel-table th,.fuel-table td {{ height:22px; padding:0 4px; font-weight:400; }}
.fuel-table thead th {{ height:24px; border-bottom:3px solid #111; font-weight:700; }}
.fuel-gray {{ background:#bfbfbf; }}
.fuel-strong,.fuel-bof {{ text-align:center; font-weight:700 !important; }}
.fuel-bof {{ border-left:3px solid #111 !important; }}
.fuel-phase {{ text-align:left; }}
.fuel-time,.fuel-amount {{ display:grid; align-items:center; width:100%; }}
.fuel-time {{ grid-template-columns:1fr .5fr 1fr; text-align:center; }}
.fuel-amount {{ grid-template-columns:1fr auto; gap:4px; }}
.fuel-amount span:first-child {{ text-align:right; }}
.fuel-reserve-row td,.fuel-min-row td {{ border-bottom:3px solid #111; }}
.fuel-extra-row td {{ border-bottom:3px double #111; }}
.rjfm-guidance {{ margin-top:8px; border-top:2px solid #111; padding-top:6px; }}
.rjfm-guidance h3 {{ margin:0 0 5px; font-size:13px; }}
.rjfm-guidance-layout {{ display:grid; grid-template-columns:1.35fr 1fr; gap:8px; }}
.rjfm-guidance-map {{ width:100%; border:1px solid #777; background:#fff; }}
.rjfm-guidance-map text {{ font-size:10px; font-weight:700; }}
.rjfm-guidance p,.rjfm-guidance li {{ font-size:8px; line-height:1.3; }}
.rjfm-guidance ul {{ margin:3px 0; padding-left:18px; }}
.rjfm-advisory {{ color:#8a4b08; }}
.rjfm-invalid {{ color:#b00020; }}
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
  .rjfm-guidance {{ break-before:page; }}
  .rjfm-guidance p,.rjfm-guidance li {{ font-size:6.5pt; }}
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
  {_rjfm_guidance_section(outcome)}
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
