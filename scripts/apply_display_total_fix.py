from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(
            f"unexpected occurrence count in {path}: {old[:80]!r} -> {text.count(old)}"
        )
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


navlog_path = "src/autonavlog/application/navlog_display.py"
replace_once(
    navlog_path,
    """def _weather_temperature(result: WeatherResult | None, reason_code: str) -> NavLogDisplayCell:\n""",
    """def _summed_transcription_combined(\n    values: list[AdoptedValue[float]],\n    cumulative: AdoptedValue[float],\n    formatter: Callable[[float], str],\n    *,\n    quantum: float,\n    unit_scale: float,\n    prior_display_cumulative: float | None,\n    fallback_reason: str,\n) -> tuple[NavLogDisplayCell, float | None, float | None, float | None]:\n    \"\"\"Build a parent ZONE/CUM cell from the values visible in child rows.\n\n    Exact totals remain available separately for calculation/audit.  The rendered\n    parent uses each child value after the same transcription rounding applied to\n    the child row, so visible arithmetic remains self-consistent.\n    \"\"\"\n\n    adopted = [value.adopted() for value in values]\n    cumulative_value = cumulative.adopted()\n    if any(value is None for value in adopted):\n        return _unavailable(fallback_reason), None, cumulative_value, None\n    exact_total = sum(value for value in adopted if value is not None)\n    if cumulative_value is None:\n        return (\n            _unavailable(_reason(cumulative, fallback_reason)),\n            exact_total,\n            None,\n            None,\n        )\n    if prior_display_cumulative is None:\n        return _unavailable(fallback_reason), exact_total, cumulative_value, None\n\n    display_total = sum(\n        round_half_up(value / unit_scale, quantum) * unit_scale\n        for value in adopted\n        if value is not None\n    )\n    display_cumulative = prior_display_cumulative + display_total\n    return (\n        _display(\n            f\"{formatter(display_total)} / {formatter(display_cumulative)}\",\n            f\"{display_total}/{display_cumulative}\",\n            manual=(\n                any(value.adopted_source == AdoptedSource.MANUAL for value in values)\n                or cumulative.adopted_source == AdoptedSource.MANUAL\n            ),\n        ),\n        exact_total,\n        cumulative_value,\n        display_cumulative,\n    )\n\n\ndef _weather_temperature(result: WeatherResult | None, reason_code: str) -> NavLogDisplayCell:\n""",
)
replace_once(
    navlog_path,
    """    estimated_altitudes = _estimated_descent_altitudes(sections)\n    rows: list[NavLogDisplayRow] = []\n\n    def append(row: NavLogDisplayRow) -> None:\n""",
    """    estimated_altitudes = _estimated_descent_altitudes(sections)\n    rows: list[NavLogDisplayRow] = []\n    display_cumulative_distance_nm: float | None = 0.0\n    display_cumulative_ete_seconds: float | None = 0.0\n\n    def append(row: NavLogDisplayRow) -> None:\n""",
)
replace_once(
    navlog_path,
    """        distance_cell, distance_total, cumulative_distance = _summed_combined(\n            [zone.zone_distance_nm for zone in zones],\n            last.cumulative_distance_nm,\n            _distance,\n            fallback_reason=\"DISPLAY_DISTANCE_SUBTOTAL_UNAVAILABLE\",\n        )\n        ete_cell, ete_total, cumulative_ete = _summed_combined(\n            [zone.zone_ete_seconds for zone in zones],\n            last.cumulative_ete_seconds,\n            _duration,\n            fallback_reason=\"DISPLAY_ETE_SUBTOTAL_UNAVAILABLE\",\n        )\n""",
    """        (\n            distance_cell,\n            distance_total,\n            cumulative_distance,\n            display_cumulative_distance_nm,\n        ) = _summed_transcription_combined(\n            [zone.zone_distance_nm for zone in zones],\n            last.cumulative_distance_nm,\n            _distance,\n            quantum=0.5,\n            unit_scale=1.0,\n            prior_display_cumulative=display_cumulative_distance_nm,\n            fallback_reason=\"DISPLAY_DISTANCE_SUBTOTAL_UNAVAILABLE\",\n        )\n        (\n            ete_cell,\n            ete_total,\n            cumulative_ete,\n            display_cumulative_ete_seconds,\n        ) = _summed_transcription_combined(\n            [zone.zone_ete_seconds for zone in zones],\n            last.cumulative_ete_seconds,\n            _duration,\n            quantum=0.5,\n            unit_scale=60.0,\n            prior_display_cumulative=display_cumulative_ete_seconds,\n            fallback_reason=\"DISPLAY_ETE_SUBTOTAL_UNAVAILABLE\",\n        )\n""",
)

replace_once(
    "tests/integration/test_issue_43_golden.py",
    """    for section in golden_project.ordered_sections()[:-1]:\n        group = [row for row in rows if row.section_id == section.id]\n""",
    """    displayed_cumulative_distance = 0.0\n    displayed_cumulative_ete = 0.0\n    for section in golden_project.ordered_sections()[:-1]:\n        group = [row for row in rows if row.section_id == section.id]\n""",
)
replace_once(
    "tests/integration/test_issue_43_golden.py",
    """        assert all(row.cumulative_distance_nm_exact is None for row in details)\n        assert all(row.cumulative_ete_seconds_exact is None for row in details)\n\n    check_point_names = [\n""",
    """        assert all(row.cumulative_distance_nm_exact is None for row in details)\n        assert all(row.cumulative_ete_seconds_exact is None for row in details)\n\n        displayed_zone_distance = sum(float(row.distance.text or \"nan\") for row in details)\n        displayed_zone_ete = sum(float(row.ete.text or \"nan\") for row in details)\n        displayed_cumulative_distance += displayed_zone_distance\n        displayed_cumulative_ete += displayed_zone_ete\n        assert group[0].distance.text == (\n            f\"{displayed_zone_distance:.1f} / {displayed_cumulative_distance:.1f}\"\n        )\n        assert group[0].ete.text == (\n            f\"{displayed_zone_ete:.1f} / {displayed_cumulative_ete:.1f}\"\n        )\n\n    check_point_names = [\n""",
)

replace_once(
    "docs/calculation_rules.md",
    """- `RJFM→OMARU`親行のDIST・ETE・燃料は内包する子区間の未丸め合計です。親行の\n  TC・VAR・MCはRJFMからOMARUへのWGS84直行測地線値を表示し、子行には各Sectionの\n""",
    """- `RJFM→OMARU`親行のDIST・ETEは、転記時の可読性を保つため、各子区間を規程の\n  0.5 NM・0.5分単位へ丸めた表示値の合計をZONEとして表示し、CUMも表示済みZONEの\n  累計とします。計算・監査用のexact値と燃料計算は未丸め値を保持します。燃料は内包する\n  子区間の未丸め合計です。親行のTC・VAR・MCはRJFMからOMARUへのWGS84直行測地線値を\n  表示し、子行には各Sectionの\n""",
)
