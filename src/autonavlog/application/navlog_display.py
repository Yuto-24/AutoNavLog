from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import floor, isclose, isfinite
from re import sub
from typing import Any
from uuid import UUID

from autonavlog.application.vertical_profile import descent_profile_from_metadata
from autonavlog.domain.calculation import (
    CalculationOutcome,
    NavLogDisplayCell,
    NavLogDisplayRow,
    SectionResult,
)
from autonavlog.domain.enums import (
    AdoptedSource,
    Availability,
    DisplayCellState,
    FlightPhase,
    PressureAltitudeDisplayKind,
)
from autonavlog.domain.project import Airport, Project
from autonavlog.domain.values import AdoptedValue
from autonavlog.domain.weather import WeatherResult
from autonavlog.nav.rounding import round_half_up
from autonavlog.weather.destination_taf import DestinationWindForecast


@dataclass(frozen=True)
class NavLogPhysicalLeg:
    """Minimum physical-Leg data required by the display projection."""

    section_ids: tuple[UUID, ...]
    phase: FlightPhase
    start_name: str
    end_name: str
    adopted_distance_nm: float | None = None
    start_node_id: UUID | None = None
    end_node_id: UUID | None = None
    summary_true_course_deg: float | None = None
    summary_variation_deg_east: float | None = None
    summary_magnetic_course_deg: float | None = None

    def __post_init__(self) -> None:
        if not self.section_ids:
            raise ValueError("NavLogPhysicalLeg requires at least one source section")
        if len(set(self.section_ids)) != len(self.section_ids):
            raise ValueError("NavLogPhysicalLeg source sections must be unique")

    @property
    def section_id(self) -> UUID:
        """Stable parent-row identity for existing display-row consumers."""

        return self.section_ids[0]


def reproject_route_node_labels(
    project: Project,
    outcome: CalculationOutcome,
    *,
    node_id: UUID,
    previous_name: str,
) -> CalculationOutcome:
    """Refresh route-node labels without touching calculated values.

    Only the node being renamed is reprojected. Node IDs are carried only by
    physical endpoints. Derived RCA/EOC and CP markers intentionally have no
    route-node identity. The old name is retained as the exact prefix guard so
    a user-entered slash is never mistaken for a derived-label separator.
    """

    if outcome.project_id != project.id:
        raise ValueError("calculation outcome belongs to a different project")
    node = next((item for item in project.route_nodes if item.id == node_id), None)
    if node is None:
        raise ValueError("renamed route node is not in the project")

    def label(current: str, endpoint_node_id: UUID | None) -> str:
        if endpoint_node_id != node_id or not current:
            return current
        if current == previous_name:
            return node.name
        if current.startswith(f"{previous_name} / "):
            return f"{node.name}{current[len(previous_name):]}"
        return current

    sections = [
        section.model_copy(
            update={
                "from_name": label(section.from_name, section.from_node_id),
                "to_name": label(section.to_name, section.to_node_id),
            }
        )
        for section in outcome.sections
    ]
    display_rows = [
        row.model_copy(
            update={
                "from_name": label(row.from_name, row.from_node_id),
                "to_name": label(row.to_name, row.to_node_id),
            }
        )
        for row in outcome.display_rows
    ]
    return outcome.model_copy(update={"sections": sections, "display_rows": display_rows})


def _blank() -> NavLogDisplayCell:
    return NavLogDisplayCell(state=DisplayCellState.BLANK)


def _inherit(value: float | str | None, *, manual: bool = False) -> NavLogDisplayCell:
    return NavLogDisplayCell(
        state=DisplayCellState.INHERIT,
        effective_value=value,
        manual=manual,
    )


def _unavailable(reason_code: str) -> NavLogDisplayCell:
    return NavLogDisplayCell(
        state=DisplayCellState.UNAVAILABLE,
        text="未取得",
        reason_code=reason_code,
    )


def _display(
    text: str,
    value: float | str,
    *,
    manual: bool = False,
) -> NavLogDisplayCell:
    return NavLogDisplayCell(
        state=DisplayCellState.DISPLAY_VALUE,
        text=text,
        effective_value=value,
        manual=manual,
    )


def _symbol(text: str, meaning: str) -> NavLogDisplayCell:
    return NavLogDisplayCell(
        state=DisplayCellState.STATE_SYMBOL,
        text=text,
        effective_value=meaning,
    )


def _reason(value: AdoptedValue[Any], fallback: str) -> str:
    raw = value.automatic_metadata.get("reason_code")
    return raw if isinstance(raw, str) and raw else fallback


def _from_adopted(
    value: AdoptedValue[float],
    formatter: Callable[[float], str],
    *,
    fallback_reason: str,
) -> NavLogDisplayCell:
    adopted = value.adopted()
    if adopted is None:
        return _unavailable(_reason(value, fallback_reason))
    return _display(
        formatter(adopted),
        adopted,
        manual=value.adopted_source == AdoptedSource.MANUAL,
    )


def _rounded_integer(value: float) -> str:
    return f"{round_half_up(value, 1.0):.0f}"


def _bearing(value: float) -> str:
    rounded = round_half_up(value % 360.0, 1.0) % 360.0
    return "360" if rounded == 0 else f"{rounded:03.0f}"


def _signed(value: float) -> str:
    rounded = round_half_up(value, 1.0)
    if rounded == 0:
        return "0"
    return f"{rounded:+.0f}"


def _temperature(value: float) -> str:
    return f"{round_half_up(value, 0.1):.1f}"


def _distance(value: float) -> str:
    return f"{round_half_up(value, 0.5):.1f}"


def _duration(value: float) -> str:
    return f"{round_half_up(value / 60.0, 0.5):.1f}"


def _fuel(value: float) -> str:
    return f"{round_half_up(value, 0.1):.1f}"


def _wind(
    direction: AdoptedValue[float],
    speed: AdoptedValue[float],
) -> NavLogDisplayCell:
    speed_value = speed.adopted()
    direction_value = direction.adopted()
    if speed_value is None:
        return _unavailable(_reason(speed, "WIND_SPEED_UNAVAILABLE"))
    if speed_value < 0.5:
        return _display(
            "CALM",
            "CALM",
            manual=speed.adopted_source == AdoptedSource.MANUAL,
        )
    if direction_value is None:
        return _unavailable(_reason(direction, "WIND_DIRECTION_UNAVAILABLE"))
    text = f"{_bearing(direction_value)}/{_rounded_integer(speed_value)}"
    return _display(
        text,
        text,
        manual=(
            direction.adopted_source == AdoptedSource.MANUAL
            or speed.adopted_source == AdoptedSource.MANUAL
        ),
    )


def _fuel_tenths(value: float) -> int:
    return int(round(round_half_up(value, 0.1) * 10))


def _allocate_proportional_largest_remainder_ticks(
    values: list[float],
    target_ticks: int,
) -> list[int] | None:
    """Allocate a rounded parent total proportionally in route order."""

    if target_ticks < 0 or not values or any(
        not isfinite(value) or value < 0 for value in values
    ):
        return None
    total = sum(values)
    if not isfinite(total) or total < 0:
        return None
    if total == 0:
        return None
    exact_ticks = [value / total * target_ticks for value in values]
    allocated = [floor(value) for value in exact_ticks]
    remaining = target_ticks - sum(allocated)
    if remaining < 0 or remaining > len(allocated):
        return None
    order = sorted(
        range(len(values)),
        key=lambda index: (-(exact_ticks[index] - allocated[index]), index),
    )
    for index in order[:remaining]:
        allocated[index] += 1
    return allocated


def _allocate_raw_largest_remainder_ticks(
    exact_ticks: list[float],
    target_ticks: int,
) -> list[int] | None:
    """Allocate raw units by floor plus fractional remainder in route order.

    RJFM inbound ETE has always rounded its profile total once, then allocated
    that total from each Zone's *raw* 30-second units.  Unlike DIST, it does
    not proportionally normalize those units to the rounded total.
    """

    if target_ticks < 0 or not exact_ticks or any(
        not isfinite(value) or value < 0 for value in exact_ticks
    ):
        return None
    allocated = [floor(value) for value in exact_ticks]
    remaining = target_ticks - sum(allocated)
    if remaining < 0 or remaining > len(allocated):
        return None
    order = sorted(
        range(len(exact_ticks)),
        key=lambda index: (-(exact_ticks[index] - allocated[index]), index),
    )
    for index in order[:remaining]:
        allocated[index] += 1
    return allocated


def _inbound_ete_display_seconds(
    sections: list[SectionResult],
) -> dict[int, int]:
    """Allocate the once-rounded inbound profile in 30-second units."""
    candidates = [
        section
        for section in sections
        if section.performance_metadata.get("type")
        == "rjfm_inbound_operational_descent"
    ]
    values = [(section, section.zone_ete_seconds.adopted()) for section in candidates]
    if not values or any(value is None for _, value in values):
        return {}
    exact_units = [float(value) / 30.0 for _, value in values if value is not None]
    target_units = int(round_half_up(sum(exact_units), 1.0))
    allocated = _allocate_raw_largest_remainder_ticks(exact_units, target_units)
    if allocated is None:
        return {}
    return {
        section.sequence: units * 30
        for (section, _), units in zip(values, allocated, strict=True)
    }


def _display_fixed_ete_combined(
    zones: list[SectionResult],
    prior_display_cumulative: float | None,
    allocations: dict[int, int],
) -> tuple[NavLogDisplayCell, float | None]:
    if prior_display_cumulative is None or any(
        zone.sequence not in allocations for zone in zones
    ):
        return _unavailable("DISPLAY_ETE_SUBTOTAL_UNAVAILABLE"), None
    display_total = sum(allocations[zone.sequence] for zone in zones)
    display_cumulative = prior_display_cumulative + display_total
    exact_total = sum(
        value for zone in zones if (value := zone.zone_ete_seconds.adopted()) is not None
    )
    exact_cumulative = zones[-1].cumulative_ete_seconds.adopted()
    if exact_cumulative is None:
        return _unavailable("DISPLAY_ETE_SUBTOTAL_UNAVAILABLE"), None
    return (
        _display(
            f"{_duration(display_total)} / {_duration(display_cumulative)}",
            f"{exact_total}/{exact_cumulative}",
        ),
        display_cumulative,
    )


def _display_fuel_combined(
    values: list[AdoptedValue[float]],
    remaining: AdoptedValue[float],
    *,
    prior_display_remaining_tenths: int | None,
    fallback_reason: str,
) -> tuple[NavLogDisplayCell, int | None]:
    """Build SECT/REM from the fuel values visible in the NAV LOG.

    Exact section and remaining fuel stay authoritative in CalculationOutcome.
    Only this presentation cell uses rounded child operands and sequential REM.
    """

    adopted = [value.adopted() for value in values]
    remaining_value = remaining.adopted()
    if any(value is None for value in adopted):
        return _unavailable(fallback_reason), None
    if remaining_value is None:
        return _unavailable(_reason(remaining, fallback_reason)), None
    if prior_display_remaining_tenths is None:
        return _unavailable(fallback_reason), None

    display_section_tenths = sum(
        _fuel_tenths(value) for value in adopted if value is not None
    )
    display_remaining_tenths = (
        prior_display_remaining_tenths - display_section_tenths
    )
    exact_section = sum(value for value in adopted if value is not None)
    return (
        _display(
            f"{display_section_tenths / 10:.1f} / {display_remaining_tenths / 10:.1f}",
            f"{exact_section}/{remaining_value}",
            manual=(
                any(value.adopted_source == AdoptedSource.MANUAL for value in values)
                or remaining.adopted_source == AdoptedSource.MANUAL
            ),
        ),
        display_remaining_tenths,
    )


def _display_rounded_combined(
    values: list[AdoptedValue[float]],
    cumulative: AdoptedValue[float],
    formatter: Callable[[float], str],
    *,
    quantum: float,
    unit_scale: float,
    prior_display_cumulative: float | None,
    fallback_reason: str,
) -> tuple[NavLogDisplayCell, float | None]:
    """Build a parent ZONE/CUM cell from the values visible in child rows.

    Authoritative exact totals remain in ``CalculationOutcome.sections``. The
    parent uses each child after display rounding so visible arithmetic stays
    self-consistent.
    """

    adopted = [value.adopted() for value in values]
    cumulative_value = cumulative.adopted()
    if any(value is None for value in adopted):
        return _unavailable(fallback_reason), None
    if cumulative_value is None:
        return _unavailable(_reason(cumulative, fallback_reason)), None
    if prior_display_cumulative is None:
        return _unavailable(fallback_reason), None

    display_total = sum(
        round_half_up(value / unit_scale, quantum) * unit_scale
        for value in adopted
        if value is not None
    )
    display_cumulative = prior_display_cumulative + display_total
    return (
        _display(
            f"{formatter(display_total)} / {formatter(display_cumulative)}",
            f"{display_total}/{display_cumulative}",
            manual=(
                any(value.adopted_source == AdoptedSource.MANUAL for value in values)
                or cumulative.adopted_source == AdoptedSource.MANUAL
            ),
        ),
        display_cumulative,
    )


def _display_distance_cells(
    leg: NavLogPhysicalLeg,
    zones: list[SectionResult],
    *,
    prior_display_cumulative: float | None,
) -> tuple[NavLogDisplayCell, float | None, list[NavLogDisplayCell]]:
    """Project DIST from the rounded Physical Leg total into its Calculation Zones.

    A Physical Leg's adopted distance is the canonical display total.  Its
    0.5-NM ticks are allocated to child zones by largest remainder, using route
    order to break exact fractional ties.  This intentionally changes only
    child ``text``: their effective values remain the exact calculation values.
    """

    fallback_reason = "DISPLAY_DISTANCE_ALLOCATION_UNAVAILABLE"
    adopted_values = [zone.zone_distance_nm.adopted() for zone in zones]
    exact_values = [float(value) for value in adopted_values if value is not None]
    canonical = leg.adopted_distance_nm
    if (
        canonical is None
        or not isfinite(canonical)
        or canonical < 0
        or len(exact_values) != len(zones)
        or not isclose(sum(exact_values), canonical, rel_tol=0.0, abs_tol=1e-7)
    ):
        unavailable = _unavailable(fallback_reason)
        return unavailable, None, [unavailable for _ in zones]

    target_ticks = int(round_half_up(canonical, 0.5) / 0.5)
    allocated_ticks = _allocate_proportional_largest_remainder_ticks(
        exact_values,
        target_ticks,
    )
    if allocated_ticks is None or prior_display_cumulative is None:
        unavailable = _unavailable(fallback_reason)
        return unavailable, None, [unavailable for _ in zones]

    display_total = target_ticks * 0.5
    display_cumulative = prior_display_cumulative + display_total
    child_cells = [
        _display(
            _distance(ticks * 0.5),
            value,
            manual=zone.zone_distance_nm.adopted_source == AdoptedSource.MANUAL,
        )
        for zone, value, ticks in zip(zones, exact_values, allocated_ticks, strict=True)
    ]
    return (
        _display(
            f"{_distance(display_total)} / {_distance(display_cumulative)}",
            f"{display_total}/{display_cumulative}",
            manual=any(
                zone.zone_distance_nm.adopted_source == AdoptedSource.MANUAL
                for zone in zones
            ),
        ),
        display_cumulative,
        child_cells,
    )


def _weather_temperature(result: WeatherResult | None, reason_code: str) -> NavLogDisplayCell:
    if result is None or result.availability != Availability.AVAILABLE:
        return _unavailable(
            reason_code if result is None else result.reason_code or reason_code
        )
    raw = result.values.get("temperature_c")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return _unavailable(reason_code)
    value = float(raw)
    return _display(_temperature(value), value)


def _destination_wind_cell(
    destination: Airport,
    forecast: DestinationWindForecast | None,
) -> NavLogDisplayCell:
    if (
        forecast is None
        or forecast.airport_icao.strip().upper() != destination.icao.strip().upper()
        or forecast.availability != Availability.AVAILABLE
        or forecast.wind_speed_kt is None
        or forecast.variable_direction
        or (
            forecast.wind_speed_kt > 0
            and forecast.wind_direction_deg_from is None
        )
    ):
        reason = (
            "DESTINATION_WIND_UNAVAILABLE"
            if forecast is None
            else forecast.reason_code or "DESTINATION_WIND_UNAVAILABLE"
        )
        return _unavailable(reason)
    speed = float(forecast.wind_speed_kt)
    if speed < 0.5:
        return _display("CALM", "CALM")
    direction = float(forecast.wind_direction_deg_from or 0)
    text = f"{_bearing(direction)}/{_rounded_integer(speed)}"
    return _display(text, text)


def _label_markers(label: str) -> set[str]:
    return {
        part.strip()
        for part in label.replace(" / ", "/").split("/")
        if part.strip()
    }


def _is_eoc_descent_start(zone: SectionResult) -> bool:
    """Return whether this is the first DESCENT zone after an EOC boundary."""

    return zone.phase == FlightPhase.DESCENT and "EOC" in _label_markers(zone.from_name)


def _wind_source_section_id(zone: SectionResult) -> UUID:
    raw = zone.performance_metadata.get("descent_wind_source_section_id")
    if raw is None:
        return zone.section_id
    try:
        return UUID(str(raw))
    except ValueError:
        return zone.section_id


def _strip_checkpoint_prefix(label: str) -> str:
    return " / ".join(sub(r"^CP:\s*", "", part.strip()) for part in label.split(" / "))


def _same_effective(first: NavLogDisplayCell, second: NavLogDisplayCell) -> bool:
    if first.state == DisplayCellState.UNAVAILABLE or second.state == DisplayCellState.UNAVAILABLE:
        return (
            first.state == second.state
            and first.reason_code == second.reason_code
        )
    first_value = first.effective_value
    second_value = second.effective_value
    if isinstance(first_value, float) and isinstance(second_value, float):
        return isclose(first_value, second_value, rel_tol=0.0, abs_tol=1e-9)
    return first_value == second_value


def _project_with_inheritance(
    candidate: NavLogDisplayCell,
    current: NavLogDisplayCell | None,
) -> tuple[NavLogDisplayCell, NavLogDisplayCell]:
    if current is not None and _same_effective(candidate, current):
        return _inherit(candidate.effective_value, manual=candidate.manual), current
    return candidate, candidate


def _magnetic_heading(zone: SectionResult) -> NavLogDisplayCell:
    """Render MH from the displayed 1-degree MC and WCA operands.

    The calculation result retains the unrounded `MC + WCA` value as the
    effective value.  NAV LOG transcription uses the displayed integer
    operands, so examples such as `291 + (-4) = 287` remain internally legible.
    """

    magnetic_course = zone.magnetic_course_deg.adopted()
    wca = zone.wca_deg.adopted()
    magnetic_heading = zone.magnetic_heading_deg.adopted()
    if magnetic_course is None:
        return _unavailable(
            _reason(zone.magnetic_course_deg, "MAGNETIC_COURSE_UNAVAILABLE")
        )
    if wca is None:
        return _unavailable(_reason(zone.wca_deg, "WCA_UNAVAILABLE"))
    if magnetic_heading is None:
        return _unavailable(
            _reason(zone.magnetic_heading_deg, "MAGNETIC_HEADING_UNAVAILABLE")
        )
    displayed_heading = round_half_up(magnetic_course, 1.0) + round_half_up(wca, 1.0)
    return _display(
        _bearing(displayed_heading),
        magnetic_heading,
        manual=(
            zone.magnetic_course_deg.adopted_source == AdoptedSource.MANUAL
            or zone.wca_deg.adopted_source == AdoptedSource.MANUAL
            or zone.magnetic_heading_deg.adopted_source == AdoptedSource.MANUAL
        ),
    )


def _zone_cells(zone: SectionResult) -> dict[str, NavLogDisplayCell]:
    return {
        "toat": _from_adopted(
            zone.temperature_c,
            _temperature,
            fallback_reason="TEMPERATURE_UNAVAILABLE",
        ),
        "cas": _from_adopted(
            zone.cas_kt,
            _rounded_integer,
            fallback_reason="CAS_UNAVAILABLE",
        ),
        "tas": _from_adopted(
            zone.tas_kt,
            _rounded_integer,
            fallback_reason="TAS_UNAVAILABLE",
        ),
        "tc": _from_adopted(
            zone.true_course_deg,
            _bearing,
            fallback_reason="TRUE_COURSE_UNAVAILABLE",
        ),
        "variation": _from_adopted(
            zone.variation_deg_east,
            _signed,
            fallback_reason="VARIATION_UNAVAILABLE",
        ),
        "mc": _from_adopted(
            zone.magnetic_course_deg,
            _bearing,
            fallback_reason="MAGNETIC_COURSE_UNAVAILABLE",
        ),
        "wind": _wind(zone.wind_direction_deg_from, zone.wind_speed_kt),
        "wca": _from_adopted(
            zone.wca_deg,
            _signed,
            fallback_reason="WCA_UNAVAILABLE",
        ),
        "mh": _magnetic_heading(zone),
        "gs": _from_adopted(
            zone.ground_speed_kt,
            _rounded_integer,
            fallback_reason="GROUND_SPEED_UNAVAILABLE",
        ),
    }


def _numeric_pa(zone: SectionResult) -> NavLogDisplayCell:
    return _from_adopted(
        zone.pressure_altitude_planning_ft,
        _rounded_integer,
        fallback_reason="PRESSURE_ALTITUDE_UNAVAILABLE",
    )


def _estimated_descent_altitudes(
    sections: list[SectionResult],
) -> dict[int, float]:
    estimates: dict[int, float] = {}
    elapsed_seconds = 0.0
    in_descent = False
    for section in sections:
        if section.phase != FlightPhase.DESCENT:
            in_descent = False
            elapsed_seconds = 0.0
            continue
        if not in_descent:
            in_descent = True
            elapsed_seconds = 0.0
        zone_seconds = section.zone_ete_seconds.adopted()
        if zone_seconds is None:
            continue
        elapsed_seconds += zone_seconds
        profile = descent_profile_from_metadata(section.performance_metadata)
        if profile is not None:
            estimates[section.sequence] = profile.altitude_at_elapsed(elapsed_seconds)
    return estimates


def _zone_pa(
    zone: SectionResult,
    *,
    is_physical_endpoint: bool,
    is_vrep_endpoint: bool,
    estimated_altitudes: dict[int, float],
) -> tuple[PressureAltitudeDisplayKind, NavLogDisplayCell]:
    markers = {part.strip() for part in zone.to_name.replace(" / ", "/").split("/")}
    if "RCA" in markers:
        return PressureAltitudeDisplayKind.CLIMB, _symbol("↗", "CLIMB")
    if "EOC" in markers:
        if zone.performance_metadata.get("rjfm_inbound_fixed_altitude") is True:
            return PressureAltitudeDisplayKind.NUMERIC, _numeric_pa(zone)
        return PressureAltitudeDisplayKind.DESCENT, _symbol("↘", "DESCENT")
    if zone.phase == FlightPhase.CLIMB:
        return PressureAltitudeDisplayKind.CLIMB, _symbol("↗", "CLIMB")
    if zone.phase == FlightPhase.DESCENT:
        if is_vrep_endpoint:
            target = zone.performance_metadata.get("target_altitude_ft_msl")
            value = (
                float(target)
                if isinstance(target, (int, float)) and not isinstance(target, bool)
                else None
            )
            if value is None:
                return (
                    PressureAltitudeDisplayKind.UNAVAILABLE,
                    _unavailable("VREP_ALTITUDE_UNAVAILABLE"),
                )
            return (
                PressureAltitudeDisplayKind.NUMERIC,
                _display(_rounded_integer(value), value),
            )
        if is_physical_endpoint:
            estimate = estimated_altitudes.get(zone.sequence)
            if estimate is None:
                return (
                    PressureAltitudeDisplayKind.UNAVAILABLE,
                    _unavailable("ESTIMATED_DESCENT_ALTITUDE_UNAVAILABLE"),
                )
            return (
                PressureAltitudeDisplayKind.ESTIMATED,
                _display(f"({_rounded_integer(estimate)})", estimate),
            )
        return PressureAltitudeDisplayKind.DESCENT, _symbol("↘", "DESCENT")
    return PressureAltitudeDisplayKind.NUMERIC, _numeric_pa(zone)


def _all_same(cells: list[NavLogDisplayCell]) -> bool:
    return bool(cells) and all(_same_effective(cells[0], cell) for cell in cells[1:])


def _row_cells_blank() -> dict[str, NavLogDisplayCell]:
    return {
        "pa": _blank(),
        "toat": _blank(),
        "cas": _blank(),
        "tas": _blank(),
        "tc": _blank(),
        "variation": _blank(),
        "mc": _blank(),
        "wind": _blank(),
        "wca": _blank(),
        "mh": _blank(),
        "distance": _blank(),
        "gs": _blank(),
        "ete": _blank(),
        "eto": _blank(),
        "ato": _blank(),
        "ate": _blank(),
        "fuel": _blank(),
    }


def build_navlog_display_rows(
    physical_legs: list[NavLogPhysicalLeg],
    sections: list[SectionResult],
    departure: Airport,
    destination: Airport,
    departure_weather: WeatherResult | None,
    destination_weather: WeatherResult | None,
    destination_wind: DestinationWindForecast | None,
    *,
    total_usable_fuel_gal: float,
    run_up_included: bool,
) -> list[NavLogDisplayRow]:
    """Build the Golden NAV LOG projection without changing calculation totals."""

    estimated_altitudes = _estimated_descent_altitudes(sections)
    inbound_ete_display_seconds = _inbound_ete_display_seconds(sections)
    rows: list[NavLogDisplayRow] = []
    display_cumulative_distance_nm: float | None = 0.0
    display_cumulative_ete_seconds: float | None = 0.0
    display_remaining_fuel_tenths: int | None = (
        _fuel_tenths(total_usable_fuel_gal)
        - _fuel_tenths(1.5 if run_up_included else 0.0)
    )
    eoc_boundary_pending = False

    def append(row: NavLogDisplayRow) -> None:
        rows.append(row.model_copy(update={"sequence": len(rows)}))

    for leg_index, leg in enumerate(physical_legs):
        source_section_ids = set(leg.section_ids)
        zones = [
            section for section in sections if section.section_id in source_section_ids
        ]
        if not zones:
            continue
        first = zones[0]
        last = zones[-1]
        candidate_cells = [_zone_cells(zone) for zone in zones]
        is_departure_leg = leg_index == 0 and leg.phase == FlightPhase.CLIMB
        is_final_visual = (
            leg_index == len(physical_legs) - 1
            and leg.phase == FlightPhase.VISUAL_ARRIVAL
        )

        (
            distance_cell,
            display_cumulative_distance_nm,
            zone_distance_cells,
        ) = _display_distance_cells(
            leg,
            zones,
            prior_display_cumulative=display_cumulative_distance_nm,
        )
        if inbound_ete_display_seconds and any(
            zone.sequence in inbound_ete_display_seconds for zone in zones
        ):
            ete_cell, display_cumulative_ete_seconds = _display_fixed_ete_combined(
                zones,
                display_cumulative_ete_seconds,
                inbound_ete_display_seconds,
            )
        else:
            ete_cell, display_cumulative_ete_seconds = _display_rounded_combined(
                [zone.zone_ete_seconds for zone in zones],
                last.cumulative_ete_seconds,
                _duration,
                quantum=0.5,
                unit_scale=60.0,
                prior_display_cumulative=display_cumulative_ete_seconds,
                fallback_reason="DISPLAY_ETE_SUBTOTAL_UNAVAILABLE",
            )
        fuel_cell, display_remaining_fuel_tenths = _display_fuel_combined(
            [zone.section_fuel_gal for zone in zones],
            last.remaining_fuel_gal,
            prior_display_remaining_tenths=display_remaining_fuel_tenths,
            fallback_reason="DISPLAY_FUEL_SUBTOTAL_UNAVAILABLE",
        )

        if is_departure_leg:
            pa_value = float(departure.elevation_ft_msl)
            parent_pa = _display(_rounded_integer(pa_value), pa_value)
            parent_pa_kind = PressureAltitudeDisplayKind.NUMERIC
            parent_values = {
                "toat": _weather_temperature(
                    departure_weather,
                    "DEPARTURE_TEMPERATURE_UNAVAILABLE",
                ),
                "cas": _blank(),
                "tas": _blank(),
                "tc": candidate_cells[0]["tc"],
                "variation": candidate_cells[0]["variation"],
                "mc": candidate_cells[0]["mc"],
                "wind": _blank(),
                "wca": _blank(),
                "mh": _blank(),
                "gs": _blank(),
            }
            direct_summary_values = {
                "tc": leg.summary_true_course_deg,
                "variation": leg.summary_variation_deg_east,
                "mc": leg.summary_magnetic_course_deg,
            }
            for name, value in direct_summary_values.items():
                if value is None:
                    continue
                formatter = _signed if name == "variation" else _bearing
                parent_values[name] = _display(formatter(value), value)
        else:
            if first.phase in {FlightPhase.DESCENT, FlightPhase.VISUAL_ARRIVAL}:
                parent_pa = _symbol("↘", "DESCENT")
                parent_pa_kind = PressureAltitudeDisplayKind.DESCENT
            else:
                parent_pa = _numeric_pa(first)
                parent_pa_kind = (
                    PressureAltitudeDisplayKind.UNAVAILABLE
                    if parent_pa.state == DisplayCellState.UNAVAILABLE
                    else PressureAltitudeDisplayKind.NUMERIC
                )
            parent_values = {
                name: candidate_cells[0][name]
                for name in (
                    "toat",
                    "cas",
                    "tas",
                    "tc",
                    "variation",
                    "mc",
                    "wind",
                    "wca",
                    "mh",
                )
            }
            gs_cells = [candidate["gs"] for candidate in candidate_cells]
            parent_values["gs"] = gs_cells[0] if _all_same(gs_cells) else _blank()

        append(
            NavLogDisplayRow(
                section_id=leg.section_id,
                wind_source_section_id=_wind_source_section_id(first),
                sequence=0,
                source_result_sequence=first.sequence,
                phase=first.phase,
                row_type="PHYSICAL_LEG_SUMMARY",
                from_name=leg.start_name,
                to_name=leg.end_name,
                from_node_id=leg.start_node_id,
                to_node_id=leg.end_node_id,
                from_latitude_deg=first.from_latitude_deg,
                from_longitude_deg=first.from_longitude_deg,
                to_latitude_deg=last.to_latitude_deg,
                to_longitude_deg=last.to_longitude_deg,
                pa_display_kind=parent_pa_kind,
                pa=parent_pa,
                toat=parent_values["toat"],
                cas=parent_values["cas"],
                tas=parent_values["tas"],
                tc=parent_values["tc"],
                variation=parent_values["variation"],
                mc=parent_values["mc"],
                wind=parent_values["wind"],
                wca=parent_values["wca"],
                mh=parent_values["mh"],
                distance=distance_cell,
                gs=parent_values["gs"],
                ete=ete_cell,
                eto=_blank(),
                ato=_blank(),
                ate=_blank(),
                fuel=fuel_cell,
            )
        )

        if not is_final_visual:
            context: dict[str, NavLogDisplayCell | None] = {
                "pa": parent_pa,
                **{
                    name: (
                        cell if cell.state != DisplayCellState.BLANK else None
                    )
                    for name, cell in parent_values.items()
                    if name != "gs"
                },
            }
            parent_gs_context = (
                parent_values["gs"]
                if parent_values["gs"].state != DisplayCellState.BLANK
                else None
            )
            next_leg = physical_legs[leg_index + 1] if leg_index + 1 < len(physical_legs) else None
            for zone_index, (zone, candidates, zone_distance_cell) in enumerate(
                zip(zones, candidate_cells, zone_distance_cells, strict=True)
            ):
                is_endpoint = zone_index == len(zones) - 1
                is_vrep = bool(
                    is_endpoint
                    and next_leg is not None
                    and next_leg.phase == FlightPhase.VISUAL_ARRIVAL
                )
                pa_kind, pa_candidate = _zone_pa(
                    zone,
                    is_physical_endpoint=is_endpoint,
                    is_vrep_endpoint=is_vrep,
                    estimated_altitudes=estimated_altitudes,
                )
                pa_cell, new_pa_context = _project_with_inheritance(
                    pa_candidate,
                    context.get("pa"),
                )
                context["pa"] = new_pa_context
                shown: dict[str, NavLogDisplayCell] = {}
                eoc_wind_boundary = zone.phase == FlightPhase.DESCENT and (
                    eoc_boundary_pending
                    or _is_eoc_descent_start(zone)
                )
                for name in (
                    "toat",
                    "cas",
                    "tas",
                    "tc",
                    "variation",
                    "mc",
                    "wind",
                    "wca",
                    "mh",
                ):
                    if eoc_wind_boundary and name in {"wind", "wca", "mh"}:
                        # EOC changes the applicable wind.  Preserve a calculated
                        # UNAVAILABLE state too; it must not become an inherited blank.
                        shown[name] = candidates[name]
                        context[name] = candidates[name]
                    else:
                        shown[name], context[name] = _project_with_inheritance(
                            candidates[name],
                            context.get(name),
                        )
                if eoc_wind_boundary:
                    gs_cell = candidates["gs"]
                else:
                    gs_cell = (
                        _inherit(
                            candidates["gs"].effective_value,
                            manual=candidates["gs"].manual,
                        )
                        if parent_gs_context is not None
                        and _same_effective(candidates["gs"], parent_gs_context)
                        else candidates["gs"]
                    )
                append(
                    NavLogDisplayRow(
                        section_id=zone.section_id,
                        wind_source_section_id=_wind_source_section_id(zone),
                        sequence=0,
                        source_result_sequence=zone.sequence,
                        phase=zone.phase,
                        row_type="CALCULATION_ZONE",
                        from_name="",
                        to_name=_strip_checkpoint_prefix(zone.to_name),
                        from_node_id=zone.from_node_id,
                        to_node_id=zone.to_node_id,
                        from_latitude_deg=zone.from_latitude_deg,
                        from_longitude_deg=zone.from_longitude_deg,
                        to_latitude_deg=zone.to_latitude_deg,
                        to_longitude_deg=zone.to_longitude_deg,
                        pa_display_kind=pa_kind,
                        pa=pa_cell,
                        toat=shown["toat"],
                        cas=shown["cas"],
                        tas=shown["tas"],
                        tc=shown["tc"],
                        variation=shown["variation"],
                        mc=shown["mc"],
                        wind=shown["wind"],
                        wca=shown["wca"],
                        mh=shown["mh"],
                        distance=zone_distance_cell,
                        gs=gs_cell,
                        ete=(
                            _display(
                                _duration(inbound_ete_display_seconds[zone.sequence]),
                                inbound_ete_display_seconds[zone.sequence],
                            )
                            if zone.sequence in inbound_ete_display_seconds
                            else _from_adopted(
                                zone.zone_ete_seconds,
                                _duration,
                                fallback_reason="ZONE_ETE_UNAVAILABLE",
                            )
                        ),
                        eto=_blank(),
                        ato=_blank(),
                        ate=_blank(),
                        fuel=_from_adopted(
                            zone.section_fuel_gal,
                            _fuel,
                            fallback_reason="SECTION_FUEL_UNAVAILABLE",
                        ),
                    )
                )

                if eoc_wind_boundary:
                    eoc_boundary_pending = False
                elif "EOC" in _label_markers(zone.to_name):
                    eoc_boundary_pending = True

        if is_final_visual:
            destination_pa = float(destination.elevation_ft_msl)
            blank = _row_cells_blank()
            append(
                NavLogDisplayRow(
                    section_id=leg.section_id,
                    sequence=0,
                    phase=FlightPhase.VISUAL_ARRIVAL,
                    row_type="DESTINATION_INFO",
                    from_name="",
                    to_name=destination.icao,
                    to_node_id=leg.end_node_id,
                    to_latitude_deg=destination.latitude_deg,
                    to_longitude_deg=destination.longitude_deg,
                    pa_display_kind=PressureAltitudeDisplayKind.NUMERIC,
                    pa=_display(_rounded_integer(destination_pa), destination_pa),
                    toat=_weather_temperature(
                        destination_weather,
                        "DESTINATION_TEMPERATURE_UNAVAILABLE",
                    ),
                    cas=blank["cas"],
                    tas=blank["tas"],
                    tc=blank["tc"],
                    variation=blank["variation"],
                    mc=blank["mc"],
                    wind=_destination_wind_cell(destination, destination_wind),
                    wca=blank["wca"],
                    mh=blank["mh"],
                    distance=blank["distance"],
                    gs=blank["gs"],
                    ete=blank["ete"],
                    eto=blank["eto"],
                    ato=blank["ato"],
                    ate=blank["ate"],
                    fuel=blank["fuel"],
                )
            )

        append(
            NavLogDisplayRow(
                sequence=0,
                phase=leg.phase,
                row_type="LEG_SEPARATOR",
                pa_display_kind=PressureAltitudeDisplayKind.BLANK,
                pa=_blank(),
                toat=_blank(),
                cas=_blank(),
                tas=_blank(),
                tc=_blank(),
                variation=_blank(),
                mc=_blank(),
                wind=_blank(),
                wca=_blank(),
                mh=_blank(),
                distance=_blank(),
                gs=_blank(),
                ete=_blank(),
                eto=_blank(),
                ato=_blank(),
                ate=_blank(),
                fuel=_blank(),
            )
        )

    return rows
