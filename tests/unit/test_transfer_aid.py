from __future__ import annotations

from datetime import datetime, timezone

from autonavlog.application.calculation_service import CalculationService
from autonavlog.domain.calculation import (
    DerivedRoutePoint,
    Issue,
    NavLogDisplayCell,
)
from autonavlog.domain.enums import (
    AdoptedSource,
    DerivedPointType,
    DisplayCellState,
    FlightPhase,
    IssueSeverity,
    ProjectStatus,
    ValueState,
)
from autonavlog.domain.planning import (
    RjfmConstraintResult,
    RjfmCoordinate,
    RjfmDepartureGuidance,
    RjfmGuidancePathPoint,
    RjfmGuidanceStatus,
    RjfmRunwayGuidance,
    RjfmTurnMethod,
)
from autonavlog.domain.values import AdoptedValue
from autonavlog.presentation.clearcopy import render_clearcopy_html
from autonavlog.presentation.transfer_aid import (
    DISCLAIMER,
    render_transfer_aid_document,
    render_transfer_aid_html,
)
from autonavlog.weather.fake_provider import FakeWeatherProvider


def _outcome_with_sections(project, airports, performance_repository):
    cruise_project = project.model_copy(deep=True)
    for section in cruise_project.sections:
        section.phase = FlightPhase.CRUISE
    outcome = CalculationService(airports, performance_repository).calculate(
        cruise_project,
        FakeWeatherProvider(),
    )
    point = DerivedRoutePoint(
        type=DerivedPointType.RCA,
        section_id=outcome.sections[0].section_id,
        latitude_deg=32.12345,
        longitude_deg=131.54321,
        along_route_distance_nm=12.3,
        estimated_time_utc=outcome.sections[0].eto_utc.adopted(),
    )
    return (
        cruise_project,
        outcome.model_copy(
            update={
                "status": ProjectStatus.READY_FOR_COPY,
                "issues": [],
                "derived_points": [point],
            }
        ),
    )


def _display_value(text: str, effective_value: float | str, *, manual: bool = False):
    return NavLogDisplayCell(
        state=DisplayCellState.DISPLAY_VALUE,
        text=text,
        effective_value=effective_value,
        manual=manual,
    )


def _replace_first_display_row(outcome, **updates):
    index = next(
        index
        for index, row in enumerate(outcome.display_rows)
        if row.row_type == "PHYSICAL_LEG_SUMMARY"
    )
    rows = list(outcome.display_rows)
    rows[index] = rows[index].model_copy(update=updates)
    return outcome.model_copy(update={"display_rows": rows})


def test_ready_transfer_aid_is_dense_a4_landscape_table(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )

    html = render_transfer_aid_html(ready_project, outcome)

    assert "AutoNavLog 転記補助表（非公式）" in html
    assert DISCLAIMER in html
    assert "@page { size: A4 landscape; margin: 8mm; }" in html
    assert 'class="route-table"' in html
    assert "copy-field" not in html
    assert "transfer-status transfer-ready" in html
    assert "転記可（要照合）" in html
    for heading in (
        "DATE",
        "SHIP",
        "FROM",
        "TO",
        "TTL DIST",
        "TTL TIME",
        "TAKE OFF",
        "LANDING",
        "PILOT",
        "PA",
        "TOAT",
        "CAS",
        "TAS",
        "TC",
        "VAR",
        "MC",
        "WIND",
        "WCA",
        "MH",
        "ZONE / CUM",
        "GS",
        "ETE",
        "ETO",
        "ATO",
        "ATE",
        "SECT / REM",
        "INFO",
        "QNH",
        "TIME",
        "FUEL",
        "MIN REQUIRED",
    ):
        assert heading in html
    route_table = html.split('<table class="route-table">', 1)[1].split("</table>", 1)[0]
    assert "PHASE" not in route_table
    assert "<th>ALT" not in route_table
    assert "DERIVED POINTS" not in html
    assert "CHECK POINT ABEAM" not in html
    assert "ARRIVAL / VREP ALTITUDE" not in html
    assert "PWR_NOT_EXACTLY_65_PERCENT" not in html
    info_table = html.split("<table class='info-table'>", 1)[1].split("</table>", 1)[0]
    assert "rowspan" not in info_table
    assert info_table.count("<th>") == info_table.count("<td") == 8
    assert render_clearcopy_html(ready_project, outcome) == html

    document = render_transfer_aid_document(ready_project, outcome)
    assert document.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in document
    assert 'onclick="window.print()"' in document
    assert "A4横で印刷" in document
    assert html in document


def test_transfer_aid_does_not_revive_legacy_sea_or_eto_values(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    legacy_section = outcome.sections[0].model_copy(
        update={
            "safe_enroute_altitude_ft_msl": AdoptedValue[float](
                automatic_value=9876.0,
                automatic_status=ValueState.AUTO,
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "eto_utc": AdoptedValue[datetime](
                automatic_value=datetime(2026, 8, 10, 12, 34, tzinfo=timezone.utc),
                automatic_status=ValueState.AUTO,
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
        }
    )
    legacy_outcome = outcome.model_copy(
        update={"sections": [legacy_section, *outcome.sections[1:]]}
    )

    html = render_transfer_aid_html(ready_project, legacy_outcome)
    route_table = html.split('<table class="route-table">', 1)[1].split("</table>", 1)[0]

    assert "SEA" not in route_table
    assert "9876" not in route_table
    assert "21:34" not in route_table
    rendered_rows = [
        row for row in legacy_outcome.display_rows if row.row_type != "LEG_SEPARATOR"
    ]
    assert route_table.count("display-blank") >= len(rendered_rows) * 3


def test_non_ready_transfer_aid_is_red_and_marks_missing_values(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    blocked = _replace_first_display_row(
        outcome,
        gs=NavLogDisplayCell(
            state=DisplayCellState.UNAVAILABLE,
            text="未取得",
            reason_code="GS_UNAVAILABLE",
        ),
    ).model_copy(
        update={
            "status": ProjectStatus.MANUAL_INPUT_REQUIRED,
            "issues": [
                Issue(
                    code="GS_UNAVAILABLE",
                    severity=IssueSeverity.BLOCKER,
                    message="GSを確定できません。",
                )
            ],
        }
    )

    html = render_transfer_aid_html(ready_project, blocked)

    assert "transfer-status transfer-blocked" in html
    assert ">転記不可<br>" in html
    assert "color:#b00020" in html
    assert "未取得" in html
    assert "class='num display-unavailable missing'" in html
    assert "GS_UNAVAILABLE" not in html
    assert "GSを確定できません。" not in html
    assert "警告" not in html
    assert "WARNING" not in html
    assert DISCLAIMER in html


def test_transfer_aid_groups_repeated_issues_and_preserves_segment_range(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    repeated = [
        Issue(
            code="PWR_NOT_EXACTLY_65_PERCENT",
            severity=IssueSeverity.WARNING,
            message="65%に最も近い性能行を採用しました。",
            segment_sequence=sequence,
        )
        for sequence in range(1, 5)
    ]
    grouped_outcome = outcome.model_copy(update={"issues": repeated})

    html = render_transfer_aid_html(ready_project, grouped_outcome)

    assert "PWR_NOT_EXACTLY_65_PERCENT" not in html
    assert "65%に最も近い性能行を採用しました。" not in html
    assert "警告・未確定項目" not in html


def test_transfer_aid_does_not_require_qnh_value(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    html = render_transfer_aid_html(ready_project, outcome)

    assert "QNH" in html
    assert outcome.qnh_hpa.adopted() is None
    assert "None hPa" not in html
    assert "MSM推定QNH" not in html
    assert "METAR観測QNH" not in html


def test_transfer_aid_distinguishes_every_value_state(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    marked = _replace_first_display_row(
        outcome,
        pa=_display_value("5500", 5500.125),
        tc=_display_value("123", 123.456),
        variation=_display_value("+7", 7.125, manual=True),
        mc=NavLogDisplayCell(
            state=DisplayCellState.UNAVAILABLE,
            text="未取得",
            reason_code="COURSE_MISSING",
        ),
        wca=_display_value("+2", 2.375),
    )

    html = render_transfer_aid_html(ready_project, marked)

    assert ">5500<" in html
    assert ">123<" in html
    assert ">+7<" in html
    assert ">+2<" in html
    assert ">未取得<" in html
    assert "display-unavailable missing" in html
    assert "source_page=5-32" not in html
    assert "TEST_RULE" not in html
    assert "COURSE_MISSING" not in html
    assert "CROSSWIND_NEAR_LIMIT" not in html


def test_transfer_aid_manual_qnh_keeps_msm_estimate_visible(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    manual = outcome.model_copy(
        update={
            "qnh_hpa": AdoptedValue[float](
                automatic_value=1008,
                automatic_status=ValueState.AUTO,
                manual_override=1010,
                adopted_source=AdoptedSource.MANUAL,
            )
        }
    )

    html = render_transfer_aid_html(ready_project, manual)

    assert "QNH" in html
    assert "1010.0 hPa" in html
    assert "1008.0 hPa" not in html
    assert "MSM推定QNH" not in html


def test_transfer_aid_escapes_project_text(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    ready_project.pilot_name = "<script>alert('x')</script>"

    html = render_transfer_aid_html(ready_project, outcome)

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html


def test_transfer_aid_formats_nav_values_at_required_precision(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    formatted_outcome = _replace_first_display_row(
        outcome,
        distance=_display_value("12.5 / 35.0", 12.34567),
        ete=_display_value("1.0 / 14.0", 61.20000000000001),
        fuel=_display_value("1.2 / 75.8", 1.23456),
        cas=_display_value("114", 114.07694052991398),
        tc=_display_value("008", 7.5),
    ).model_copy(
        update={
            "fuel_plan": outcome.fuel_plan.model_copy(
                update={
                    "total_usable_gal": 90.12345,
                    "taxi_runup_gal": 1.54321,
                }
            ),
        }
    )

    html = render_transfer_aid_html(ready_project, formatted_outcome)
    route_table = html.split('<table class="route-table">', 1)[1].split("</table>", 1)[0]
    fuel_table = html.split("<table class='fuel-table'>", 1)[1].split("</table>", 1)[0]

    assert "12.5" in route_table
    assert "1.0" in route_table
    assert "1.2" in route_table
    assert ">114<" in route_table
    assert ">008<" in route_table
    assert "12.34567" not in route_table
    assert "114.07694052991398" not in route_table
    assert "90.1" in fuel_table
    assert "1.5" in fuel_table


def test_transfer_aid_uses_requested_five_column_fuel_table(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )

    html = render_transfer_aid_html(ready_project, outcome)
    fuel_table = html.split("<table class='fuel-table'>", 1)[1].split("</table>", 1)[0]

    assert "<colgroup><col><col><col><col><col></colgroup>" in fuel_table
    assert "rowspan='6'" in fuel_table
    assert "rowspan='5'" in fuel_table
    assert "TAXI・RUN UP" in fuel_table
    assert "MIN REQUIRED" in fuel_table
    assert "<span>0</span><span>:</span><span>10</span>" in fuel_table
    assert "<span>0</span><span>:</span><span>45</span>" in fuel_table


def test_transfer_aid_renders_only_usable_rjfm_path_and_invalid_reason(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    center = [
        RjfmCoordinate(
            latitude_deg=latitude,
            longitude_deg=longitude,
            source="fixture-map",
            estimated_error_nm=0.2,
        )
        for latitude, longitude in (
            (32.08, 131.50),
            (32.10, 131.47),
            (32.22, 131.55),
        )
    ]
    path = [
        RjfmGuidancePathPoint(
            latitude_deg=latitude,
            longitude_deg=longitude,
            altitude_ft_msl=altitude,
            elapsed_seconds=elapsed,
            segment="TEST",
        )
        for latitude, longitude, altitude, elapsed in (
            (31.877, 131.449, 19, 0),
            (32.08, 131.50, 5500, 300),
        )
    ]
    guidance = RjfmDepartureGuidance(
        reference_revision="fixture-r1",
        reference_content_fingerprint="b" * 64,
        source_effective_dates={"AIP": "2025-08-07"},
        generated_against_fingerprint="a" * 64,
        center_route=center,
        candidates=[
            RjfmRunwayGuidance(
                runway="09",
                status=RjfmGuidanceStatus.WARNING,
                turn_method=RjfmTurnMethod.FIXED_BANK_20,
                path=path,
                constraints=[
                    RjfmConstraintResult(
                        code="MZE_ENTRY_DME",
                        passed=False,
                        hard=False,
                        message="MZE旋回開始点が4.0 DME未満",
                    )
                ],
                full_left_turns=1,
                partial_left_turn_deg=42,
                turn_entry_radial_deg=305,
                turn_entry_dme_nm=3.9,
                expected_time_delta_seconds=75,
            ),
            RjfmRunwayGuidance(
                runway="27",
                status=RjfmGuidanceStatus.HARD_INVALID,
                constraints=[
                    RjfmConstraintResult(
                        code="PCA",
                        passed=False,
                        hard=True,
                        message="PCA高度帯へ進入",
                    )
                ],
            ),
        ],
    )

    html = render_transfer_aid_html(
        ready_project,
        outcome.model_copy(update={"rjfm_departure_guidance": guidance}),
    )

    assert "RJFM北行き RCA / CENTER ROUTE 案内" in html
    assert "RWY09: 成立（注意）" in html
    assert "成立候補の注意条件" in html
    assert "RWY09: MZE旋回開始点が4.0 DME未満" in html
    assert "RWY27: PCA高度帯へ進入" in html
    assert html.count("<polyline") == 2  # RWY09 plus CENTER ROUTE; no invalid RWY27 path.
    assert "DIST÷GSとは一致しません" in html
    assert "AIP: 2025-08-07" in html
