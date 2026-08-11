from __future__ import annotations

from datetime import datetime, timezone

from autonavlog.application.calculation_service import CalculationService
from autonavlog.domain.calculation import DerivedRoutePoint, Issue
from autonavlog.domain.enums import (
    AdoptedSource,
    DerivedPointType,
    FlightPhase,
    IssueSeverity,
    ProjectStatus,
    ValueState,
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
    assert route_table.count("<td class='num'></td>") == len(legacy_outcome.sections) * 3


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
    missing_section = outcome.sections[0].model_copy(
        update={"ground_speed_kt": AdoptedValue[float]()}
    )
    blocked = outcome.model_copy(
        update={
            "status": ProjectStatus.MANUAL_INPUT_REQUIRED,
            "sections": [missing_section, *outcome.sections[1:]],
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
    assert "未確定" in html
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


def test_transfer_aid_always_labels_automatic_qnh_as_msm_estimated(
    project,
    airports,
    performance_repository,
) -> None:
    ready_project, outcome = _outcome_with_sections(
        project,
        airports,
        performance_repository,
    )
    metar_outcome = outcome.model_copy(
        update={
            "qnh_hpa": outcome.qnh_hpa.model_copy(
                update={
                    "automatic_metadata": outcome.qnh_hpa.automatic_metadata
                    | {"label": "METAR観測QNH"}
                }
            )
        }
    )

    html = render_transfer_aid_html(ready_project, metar_outcome)

    assert "QNH" in html
    assert f"{outcome.qnh_hpa.adopted()} hPa" in html
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
    section = outcome.sections[0].model_copy(
        update={
            "planned_altitude_ft_msl": AdoptedValue[float](
                automatic_value=5000,
                automatic_status=ValueState.AUTO,
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "pressure_altitude_planning_ft": AdoptedValue[float](
                automatic_value=5500.125,
                automatic_status=ValueState.PERFORMANCE_TABLE,
                automatic_metadata={"source_page": "5-32"},
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "true_course_deg": AdoptedValue[float](
                automatic_value=123.456,
                automatic_status=ValueState.FIXED_RULE,
                automatic_metadata={"rule_version": "TEST_RULE"},
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "variation_deg_east": AdoptedValue[float](
                automatic_value=8.25,
                automatic_status=ValueState.AUTO,
                manual_override=7.125,
                adopted_source=AdoptedSource.MANUAL,
            ),
            "magnetic_course_deg": AdoptedValue[float](
                automatic_status=ValueState.UNAVAILABLE,
                automatic_metadata={"reason_code": "COURSE_MISSING"},
            ),
            "wca_deg": AdoptedValue[float](
                automatic_value=2.375,
                automatic_status=ValueState.WARNING,
                adopted_source=AdoptedSource.AUTOMATIC,
                warnings=("CROSSWIND_NEAR_LIMIT",),
            ),
        }
    )
    marked = outcome.model_copy(update={"sections": [section, *outcome.sections[1:]]})

    html = render_transfer_aid_html(ready_project, marked)

    assert "5500.125" in html
    assert ">123<" in html
    assert ">+7<" in html
    assert ">+2<" in html
    assert "—（未確定）" in html
    assert "state-" not in html
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
    first = outcome.sections[0].model_copy(
        update={
            "zone_distance_nm": AdoptedValue[float](
                automatic_value=12.34567,
                automatic_status=ValueState.AUTO,
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "zone_ete_seconds": AdoptedValue[float](
                automatic_value=61.20000000000001,
                automatic_status=ValueState.AUTO,
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "section_fuel_gal": AdoptedValue[float](
                automatic_value=1.23456,
                automatic_status=ValueState.AUTO,
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "cas_kt": AdoptedValue[float](
                automatic_value=114.07694052991398,
                automatic_status=ValueState.AUTO,
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "true_course_deg": AdoptedValue[float](
                automatic_value=7.5,
                automatic_status=ValueState.AUTO,
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
        }
    )
    formatted_outcome = outcome.model_copy(
        update={
            "sections": [first, *outcome.sections[1:]],
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
