from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from autonavlog.performance.climb import ClimbCalculator, ClimbPerformanceError
from autonavlog.performance.cruise import (
    CruisePerformanceError,
    CruisePerformanceSelectionPolicy,
)
from autonavlog.performance.repository import (
    PerformanceDataError,
    PerformanceRepository,
)
from autonavlog.performance.schemas import (
    ClimbRow,
    ClimbTemperaturePolicy,
    PerformanceManifest,
)


def _poh_13772_006_reissue_a_climb_rows() -> list[ClimbRow]:
    values = (
        (0, 15, 0.0, 0.0, 0.0),
        (1_000, 13, 0.8, 0.3, 1.5),
        (2_000, 11, 1.7, 0.7, 3.1),
        (3_000, 9, 2.6, 1.0, 4.8),
        (4_000, 7, 3.6, 1.4, 6.7),
        (5_000, 5, 4.7, 1.7, 8.6),
        (6_000, 3, 5.8, 2.1, 10.7),
        (7_000, 1, 6.9, 2.5, 12.9),
        (8_000, -1, 8.2, 2.9, 15.4),
        (9_000, -3, 9.6, 3.3, 18.0),
        (10_000, -5, 11.1, 3.7, 20.9),
        (11_000, -7, 12.7, 4.2, 24.1),
        (12_000, -9, 14.4, 4.6, 27.6),
        (13_000, -11, 16.4, 5.1, 31.6),
        (14_000, -13, 18.7, 5.7, 36.1),
        (15_000, -15, 21.2, 6.3, 41.4),
        (16_000, -17, 24.3, 7.0, 47.6),
        (17_000, -19, 27.9, 7.8, 55.1),
        (17_500, -20, 30.0, 8.2, 59.4),
    )
    return [
        ClimbRow(
            pressure_altitude_ft=altitude,
            temperature_c=isa_oat,
            weight_lb=3_600,
            cumulative_time_min=time,
            cumulative_fuel_gal=fuel,
            cumulative_distance_nm=distance,
            source_page="P/N 13772-006 Reissue A pp. 5-30/5-31",
        )
        for altitude, isa_oat, time, fuel, distance in values
    ]


def test_climb_interpolation_and_500ft_extrapolation(
    performance_repository: PerformanceRepository,
) -> None:
    calculator = ClimbCalculator(performance_repository.climb_rows)
    interpolated = calculator.cumulative(2500, 10, 3400)
    assert interpolated.time_min == pytest.approx(5.1)
    boundary = calculator.cumulative(6500, 10, 3400)
    assert boundary.warnings == ("EXTRAPOLATED_WITHIN_500FT",)
    with pytest.raises(ClimbPerformanceError):
        calculator.cumulative(6501, 10, 3400)
    with pytest.raises(ClimbPerformanceError):
        calculator.cumulative(5000, 21, 3400)


def test_climb_uses_cumulative_difference(
    performance_repository: PerformanceRepository,
) -> None:
    result = ClimbCalculator(performance_repository.climb_rows).calculate(0, 5000, 10, 3400)
    assert result.time_min == pytest.approx(10)
    assert result.fuel_gal == pytest.approx(4)
    assert result.distance_nm == pytest.approx(15)
    assert result.representative_tas_kt == pytest.approx(90)


def test_repository_readiness_resolves_profile_and_nominal_ranges(
    performance_repository: PerformanceRepository,
) -> None:
    assert performance_repository.readiness_problems("SR22_G6") == ()

    profile_problems = performance_repository.readiness_problems("C172")
    assert len(profile_problems) == 1
    assert "機体Profile C172" in profile_problems[0]

    low_cruise_only = PerformanceRepository(
        performance_repository.manifest,
        performance_repository.climb_rows,
        [row for row in performance_repository.cruise_rows if row.pressure_altitude_ft == 4000],
    )
    range_problems = low_cruise_only.readiness_problems("SR22_G6")
    assert any(
        "標準cruise条件（5,000 ft / ISA）を解決できません" in problem for problem in range_problems
    )


def test_directory_loader_fingerprints_actual_bytes_and_preserves_hash_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "performance"
    shutil.copytree(Path("data/performance"), root)
    loaded = PerformanceRepository.from_directory(root)

    assert loaded.content_fingerprint is not None
    assert len(loaded.content_fingerprint) == 64

    climb = root / "climb_time_fuel_distance.csv"
    climb.write_bytes(climb.read_bytes() + b"\n")

    with pytest.raises(PerformanceDataError, match="SHA-256 mismatch"):
        PerformanceRepository.from_directory(root)

    fail_closed = PerformanceRepository.from_directory_for_application(root)
    assert fail_closed.load_failure_reason == "HASH_MISMATCH"
    assert fail_closed.validation_issue_reason() == "HASH_MISMATCH"
    assert any("HASH_MISMATCH" in problem for problem in fail_closed.readiness_problems("SR22_G6"))
    with pytest.raises(PerformanceDataError, match="load failed"):
        fail_closed.require_verified()


def test_directory_loader_rejects_performance_table_path_traversal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "performance"
    shutil.copytree(Path("data/performance"), root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tables"][0]["file"] = "../climb.csv"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PerformanceDataError, match="unsafe table path"):
        PerformanceRepository.from_directory(root)


def test_climb_manifest_defaults_to_legacy_table_grid_policy() -> None:
    manifest = PerformanceManifest(
        aircraft="SR22 G6",
        source_document="legacy fixture",
        source_revision="v1",
        verified_against="fixture",
    )

    assert manifest.climb_temperature_policy == ClimbTemperaturePolicy.TABLE_GRID


def test_isa_baseline_policy_applies_poh_adjustment_to_whole_climb_difference() -> None:
    calculator = ClimbCalculator(
        _poh_13772_006_reissue_a_climb_rows(),
        ClimbTemperaturePolicy.ISA_BASELINE_10_PERCENT_PER_10C_ABOVE,
    )

    # Baseline from 1,000 to 5,000 ft is 3.9 min / 1.4 gal / 7.1 NM.
    # Representative altitude is 3,000 ft, where the table's ISA OAT is 9 C.
    result = calculator.calculate(1_000, 5_000, 19, 3_600)

    assert result.time_min == pytest.approx(3.9 * 1.10)
    assert result.fuel_gal == pytest.approx(1.4 * 1.10)
    assert result.distance_nm == pytest.approx(7.1 * 1.10)
    assert result.representative_altitude_ft == 3_000
    assert result.representative_isa_temperature_c == 9
    assert result.temperature_delta_above_standard_c == 10
    assert result.temperature_adjustment_factor == pytest.approx(1.10)
    assert result.temperature_policy == ("ISA_BASELINE_10_PERCENT_PER_10C_ABOVE")
    assert result.representative_tas_kt == pytest.approx(7.1 / (3.9 / 60))


def test_isa_baseline_policy_uses_representative_not_endpoint_temperature() -> None:
    calculator = ClimbCalculator(
        _poh_13772_006_reissue_a_climb_rows(),
        "ISA_BASELINE_10_PERCENT_PER_10C_ABOVE",
    )

    # For the full SL-to-10,000 ft climb, the representative altitude is
    # 5,000 ft (ISA OAT 5 C). Actual 15 C therefore adjusts the whole
    # baseline result once by 10%, rather than adjusting endpoint cumulatives.
    result = calculator.calculate(0, 10_000, 15, 3_600)

    assert result.time_min == pytest.approx(11.1 * 1.10)
    assert result.fuel_gal == pytest.approx(3.7 * 1.10)
    assert result.distance_nm == pytest.approx(20.9 * 1.10)
    assert result.representative_isa_temperature_c == 5
    assert result.temperature_adjustment_factor == pytest.approx(1.10)


def test_isa_baseline_policy_never_reduces_standard_or_colder_values() -> None:
    calculator = ClimbCalculator(
        _poh_13772_006_reissue_a_climb_rows(),
        ClimbTemperaturePolicy.ISA_BASELINE_10_PERCENT_PER_10C_ABOVE,
    )

    standard = calculator.calculate(1_000, 5_000, 9, 3_600)
    colder = calculator.calculate(1_000, 5_000, -20, 3_600)

    for result in (standard, colder):
        assert result.time_min == pytest.approx(3.9)
        assert result.fuel_gal == pytest.approx(1.4)
        assert result.distance_nm == pytest.approx(7.1)
        assert result.temperature_delta_above_standard_c == 0
        assert result.temperature_adjustment_factor == 1
    assert calculator.cumulative(0, 30, 3_600).time_min == 0


def test_isa_baseline_policy_applies_partial_ten_degree_increment() -> None:
    result = ClimbCalculator(
        _poh_13772_006_reissue_a_climb_rows(),
        ClimbTemperaturePolicy.ISA_BASELINE_10_PERCENT_PER_10C_ABOVE,
    ).calculate(1_000, 5_000, 14, 3_600)

    assert result.temperature_delta_above_standard_c == 5
    assert result.temperature_adjustment_factor == pytest.approx(1.05)
    assert result.time_min == pytest.approx(3.9 * 1.05)


def test_isa_baseline_repository_rejects_multiple_rows_at_one_altitude() -> None:
    rows = _poh_13772_006_reissue_a_climb_rows()
    duplicate = rows[0].model_copy(update={"temperature_c": 14})
    manifest = PerformanceManifest(
        aircraft="SR22 G6",
        source_document="fixture",
        source_revision="v1",
        verified_against="fixture",
        climb_temperature_policy=(ClimbTemperaturePolicy.ISA_BASELINE_10_PERCENT_PER_10C_ABOVE),
    )

    with pytest.raises(
        PerformanceDataError,
        match="exactly one row per altitude",
    ):
        PerformanceRepository(manifest, rows + [duplicate], [])


def test_cruise_policy_interpolates_exact_65_percent_with_trace(
    performance_repository: PerformanceRepository,
) -> None:
    result = CruisePerformanceSelectionPolicy(performance_repository.cruise_rows).select(
        pressure_altitude_ft=5000,
        isa_deviation_c=0,
        distance_nm=100,
        true_course_deg=0,
        wind_direction_deg_from=None,
        wind_speed_kt=0,
    )

    assert result.row.pressure_altitude_ft == 5000
    assert result.row.isa_deviation_c == 0
    assert result.row.power_percent == 65
    assert result.row.rpm is None
    assert result.row.map_in_hg is None
    assert result.row.ktas == pytest.approx(149.5)
    assert result.row.gph == pytest.approx(15.5)
    assert result.warnings == ()
    assert result.interpolation is not None
    assert result.interpolation.altitude.lower == 4000
    assert result.interpolation.altitude.upper == 6000
    assert result.interpolation.altitude.fraction == pytest.approx(0.5)
    assert result.interpolation.isa_deviation.lower == -15
    assert result.interpolation.isa_deviation.upper == 15
    assert result.interpolation.isa_deviation.fraction == pytest.approx(0.5)
    assert len(result.interpolation.corners) == 4


@pytest.mark.parametrize(
    ("pressure_altitude_ft", "isa_deviation_c", "expected_ktas", "expected_gph"),
    (
        (4_000, 0, 165.0, 15.5),
        (5_000, 15, 165.0, 16.4),
        (6_000, -30, 164.0, 15.4),
        (12_000, 0, 178.0, 15.5),
        (13_500, -30, 176.2, 15.4),
    ),
)
def test_issue_15_cruise_workbook_golden_points(
    pressure_altitude_ft: float,
    isa_deviation_c: float,
    expected_ktas: float,
    expected_gph: float,
) -> None:
    repository = PerformanceRepository.from_directory(Path("data/performance"))

    result = CruisePerformanceSelectionPolicy(repository.cruise_rows).select(
        pressure_altitude_ft,
        isa_deviation_c,
        distance_nm=100,
        true_course_deg=0,
        wind_direction_deg_from=None,
        wind_speed_kt=0,
    )

    assert result.row.ktas == expected_ktas
    assert result.row.gph == expected_gph


@pytest.mark.parametrize(
    ("pressure_altitude_ft", "isa_deviation_c"),
    ((1_500, 0), (5_000, 31)),
)
def test_cruise_altitude_and_isa_interpolation_never_extrapolate(
    pressure_altitude_ft: float,
    isa_deviation_c: float,
) -> None:
    repository = PerformanceRepository.from_directory(Path("data/performance"))

    with pytest.raises(CruisePerformanceError, match="outside the performance table"):
        CruisePerformanceSelectionPolicy(repository.cruise_rows).select(
            pressure_altitude_ft,
            isa_deviation_c,
            distance_nm=100,
            true_course_deg=0,
            wind_direction_deg_from=None,
            wind_speed_kt=0,
        )


def test_cruise_policy_extrapolates_missing_65_percent_from_nearest_power_rows() -> None:
    repository = PerformanceRepository.from_directory(Path("data/performance"))

    result = CruisePerformanceSelectionPolicy(
        repository.cruise_rows,
        use_table_boundaries=True,
    ).select(
        2_000,
        0,
        distance_nm=100,
        true_course_deg=0,
        wind_direction_deg_from=None,
        wind_speed_kt=0,
    )

    assert result.row.ktas == 161.8
    assert result.row.gph == 15.2
    assert result.warnings == ()
    assert result.interpolation is not None
    assert len(result.interpolation.boundary_provenance) == 1
    provenance = result.interpolation.boundary_provenance[0]
    assert provenance.axis == "POWER_PERCENT"
    assert provenance.requested_value == 65.0
    assert provenance.available_min == 72.0
    assert provenance.available_max == 98.0
    assert provenance.adopted_value == 65.0
    assert provenance.pressure_altitude_ft == 2_000
    assert provenance.isa_deviation_c == 0
    assert provenance.supporting_lower_value == 72.0
    assert provenance.supporting_upper_value == 76.0
    assert provenance.supporting_fraction == pytest.approx(-1.75)
    assert provenance.extrapolated is True
    assert provenance.source_pages == ("5-32",)


def test_cruise_policy_uses_65_percent_extrapolation_without_boundary_clamping() -> None:
    repository = PerformanceRepository.from_directory(Path("data/performance"))

    result = CruisePerformanceSelectionPolicy(repository.cruise_rows).select(
        3_000,
        0,
        distance_nm=100,
        true_course_deg=0,
        wind_direction_deg_from=None,
        wind_speed_kt=0,
    )

    assert (result.row.ktas, result.row.gph) == (163.4, 15.4)
    assert result.warnings == ()


def test_cruise_extrapolation_provenance_records_each_affected_corner_once() -> None:
    repository = PerformanceRepository.from_directory(Path("data/performance"))

    result = CruisePerformanceSelectionPolicy(
        repository.cruise_rows,
        use_table_boundaries=True,
    ).select(
        13_000,
        15,
        distance_nm=100,
        true_course_deg=0,
        wind_direction_deg_from=None,
        wind_speed_kt=0,
    )

    assert (result.row.ktas, result.row.gph) == (179.6, 15.7)
    assert result.warnings == ()
    assert result.interpolation is not None
    assert [
        (
            item.pressure_altitude_ft,
            item.isa_deviation_c,
            item.available_min,
            item.available_max,
            item.adopted_value,
            item.supporting_lower_value,
            item.supporting_upper_value,
            item.supporting_fraction,
            item.extrapolated,
            item.source_pages,
        )
        for item in result.interpolation.boundary_provenance
    ] == [
        (12_000.0, 30.0, 45.0, 64.0, 65.0, 61.0, 64.0, pytest.approx(4 / 3), True, ("5-33",)),
        (14_000.0, 0.0, 47.0, 62.0, 65.0, 60.0, 62.0, pytest.approx(2.5), True, ("5-33",)),
        (14_000.0, 30.0, 45.0, 59.0, 65.0, 57.0, 59.0, pytest.approx(4.0), True, ("5-33",)),
    ]


def test_issue_15_climb_table_contains_500ft_workbook_points() -> None:
    repository = PerformanceRepository.from_directory(Path("data/performance"))
    by_altitude = {row.pressure_altitude_ft: row for row in repository.climb_rows}

    assert len(repository.climb_rows) == 36
    assert by_altitude[500].cumulative_time_min == 0.4
    assert by_altitude[7_500].cumulative_distance_nm == 14.15
    assert by_altitude[14_500].cumulative_fuel_gal == 6.0
    assert by_altitude[17_500].cumulative_time_min == 30.0
