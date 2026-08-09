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
        "PHASE",
        "FROM",
        "TO",
        "ALT",
        "PA",
        "TC",
        "VAR",
        "MC",
        "WIND",
        "WCA",
        "MH",
        "OAT",
        "CAS",
        "TAS",
        "GS",
        "ZONE DIST",
        "CUM DIST",
        "ZONE ETE",
        "CUM ETE",
        "ETO",
        "SECT FUEL",
        "REM FUEL",
    ):
        assert heading in html
    assert "RCA" in html
    assert "DERIVED POINTS" in html
    assert "FUEL SUMMARY" in html
    assert "MIN REQUIRED" in html
    assert "MSM推定QNH" in html
    assert "1013 hPa" in html
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
    assert route_table.count("<td class='num'></td>") == len(legacy_outcome.sections)


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
    assert "GS_UNAVAILABLE" in html
    assert "GSを確定できません。" in html
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

    assert html.count("PWR_NOT_EXACTLY_65_PERCENT") == 1
    assert html.count("65%に最も近い性能行を採用しました。") == 1
    assert ">2–5</td>" in html


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

    assert "MSM推定QNH" in html
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
                automatic_value=5500,
                automatic_status=ValueState.PERFORMANCE_TABLE,
                automatic_metadata={"source_page": "5-32"},
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "true_course_deg": AdoptedValue[float](
                automatic_value=123,
                automatic_status=ValueState.FIXED_RULE,
                automatic_metadata={"rule_version": "TEST_RULE"},
                adopted_source=AdoptedSource.AUTOMATIC,
            ),
            "variation_deg_east": AdoptedValue[float](
                automatic_value=8,
                automatic_status=ValueState.AUTO,
                manual_override=7,
                adopted_source=AdoptedSource.MANUAL,
            ),
            "magnetic_course_deg": AdoptedValue[float](
                automatic_status=ValueState.UNAVAILABLE,
                automatic_metadata={"reason_code": "COURSE_MISSING"},
            ),
            "wca_deg": AdoptedValue[float](
                automatic_value=2,
                automatic_status=ValueState.WARNING,
                adopted_source=AdoptedSource.AUTOMATIC,
                warnings=("CROSSWIND_NEAR_LIMIT",),
            ),
        }
    )
    marked = outcome.model_copy(update={"sections": [section, *outcome.sections[1:]]})

    html = render_transfer_aid_html(ready_project, marked)

    assert "state-auto" in html
    assert "state-performance-table" in html
    assert "性能表 / source_page=5-32" in html
    assert "state-fixed-rule" in html
    assert "規則値 / rule_version=TEST_RULE" in html
    assert "state-manual-override" in html
    assert "手動 / 自動 +8" in html
    assert "state-unavailable" in html
    assert "—（未確定）" in html
    assert "未確定理由: COURSE_MISSING" in html
    assert "state-warning" in html
    assert "⚠ 注意値 / ⚠ CROSSWIND_NEAR_LIMIT" in html


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

    assert "MSM推定QNH" in html
    assert "1010 hPa（手動上書き / MSM推定 1008 hPa）" in html


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
