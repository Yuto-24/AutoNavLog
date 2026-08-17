from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isclose
from re import sub
from typing import Any
from uuid import UUID

from autonavlog.domain.calculation import (
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
from autonavlog.domain.project import Airport
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
    return f"{rounded:03.0f}"


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


def _summed(
    values: list[AdoptedValue[float]],
    formatter: Callable[[float], str],
    *,
    fallback_reason: str,
) -> tuple[NavLogDisplayCell, float | None]:
    adopted = [value.adopted() for value in values]
    if any(value is None for value in adopted):
        return _unavailable(fallback_reason), None
    total = sum(value for value in adopted if value is not None)
    return (
        _display(
            formatter(total),
            total,
            manual=any(value.adopted_source == AdoptedSource.MANUAL for value in values),
        ),
        total,
    )


def _summed_combined(
    values: list[AdoptedValue[float]],
    cumulative: AdoptedValue[float],
    formatter: Callable[[float], str],
    *,
    fallback_reason: str,
) -> tuple[NavLogDisplayCell, float | None, float | None]:
    primary, total = _summed(
        values,
        formatter,
        fallback_reason=fallback_reason,
    )
    cumulative_value = cumulative.adopted()
    if total is None:
        return primary, None, cumulative_value
    if cumulative_value is None:
        return _unavailable(_reason(cumulative, fallback_reason)), total, None
    return (
        _display(
            f"{formatter(total)} / {formatter(cumulative_value)}",
            f"{total}/{cumulative_value}",
            manual=(
                primary.manual or cumulative.adopted_source == AdoptedSource.MANUAL
            ),
        ),
        total,
        cumulative_value,
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
        metadata = section.performance_metadata
        cruise = metadata.get("cruise_altitude_ft_msl")
        target = metadata.get("target_altitude_ft_msl")
        addition = metadata.get("operational_addition_seconds", 60.0)
        if not isinstance(cruise, (int, float)) or isinstance(cruise, bool):
            continue
        if not isinstance(target, (int, float)) or isinstance(target, bool):
            continue
        operational_seconds = (
            float(addition)
            if isinstance(addition, (int, float)) and not isinstance(addition, bool)
            else 60.0
        )
        vertical_seconds = max(0.0, elapsed_seconds - operational_seconds)
        estimates[section.sequence] = max(
            float(target),
            float(cruise) - vertical_seconds / 60.0 * 500.0,
        )
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
) -> list[NavLogDisplayRow]:
    """Build the Golden NAV LOG projection without changing calculation totals."""

    estimated_altitudes = _estimated_descent_altitudes(sections)
    rows: list[NavLogDisplayRow] = []

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

        distance_cell, distance_total, cumulative_distance = _summed_combined(
            [zone.zone_distance_nm for zone in zones],
            last.cumulative_distance_nm,
            _distance,
            fallback_reason="DISPLAY_DISTANCE_SUBTOTAL_UNAVAILABLE",
        )
        ete_cell, ete_total, cumulative_ete = _summed_combined(
            [zone.zone_ete_seconds for zone in zones],
            last.cumulative_ete_seconds,
            _duration,
            fallback_reason="DISPLAY_ETE_SUBTOTAL_UNAVAILABLE",
        )
        fuel_cell, _fuel_total, _remaining = _summed_combined(
            [zone.section_fuel_gal for zone in zones],
            last.remaining_fuel_gal,
            _fuel,
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
                sequence=0,
                source_result_sequence=first.sequence,
                phase=first.phase,
                row_type="PHYSICAL_LEG_SUMMARY",
                counts_toward_totals=False,
                from_name=leg.start_name,
                to_name=leg.end_name,
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
                zone_distance_nm_exact=distance_total,
                cumulative_distance_nm_exact=cumulative_distance,
                zone_ete_seconds_exact=ete_total,
                cumulative_ete_seconds_exact=cumulative_ete,
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
            for zone_index, (zone, candidates) in enumerate(
                zip(zones, candidate_cells, strict=True)
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
                    shown[name], context[name] = _project_with_inheritance(
                        candidates[name],
                        context.get(name),
                    )
                gs_cell = (
                    _inherit(candidates["gs"].effective_value, manual=candidates["gs"].manual)
                    if parent_gs_context is not None
                    and _same_effective(candidates["gs"], parent_gs_context)
                    else candidates["gs"]
                )
                zone_distance = zone.zone_distance_nm.adopted()
                zone_ete = zone.zone_ete_seconds.adopted()
                append(
                    NavLogDisplayRow(
                        section_id=zone.section_id,
                        sequence=0,
                        source_result_sequence=zone.sequence,
                        phase=zone.phase,
                        row_type="CALCULATION_ZONE",
                        counts_toward_totals=True,
                        from_name="",
                        to_name=_strip_checkpoint_prefix(zone.to_name),
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
                        distance=_from_adopted(
                            zone.zone_distance_nm,
                            _distance,
                            fallback_reason="ZONE_DISTANCE_UNAVAILABLE",
                        ),
                        gs=gs_cell,
                        ete=_from_adopted(
                            zone.zone_ete_seconds,
                            _duration,
                            fallback_reason="ZONE_ETE_UNAVAILABLE",
                        ),
                        eto=_blank(),
                        ato=_blank(),
                        ate=_blank(),
                        fuel=_from_adopted(
                            zone.section_fuel_gal,
                            _fuel,
                            fallback_reason="SECTION_FUEL_UNAVAILABLE",
                        ),
                        zone_distance_nm_exact=zone_distance,
                        zone_ete_seconds_exact=zone_ete,
                    )
                )

        if is_final_visual:
            destination_pa = float(destination.elevation_ft_msl)
            blank = _row_cells_blank()
            append(
                NavLogDisplayRow(
                    section_id=leg.section_id,
                    sequence=0,
                    phase=FlightPhase.VISUAL_ARRIVAL,
                    row_type="DESTINATION_INFO",
                    counts_toward_totals=False,
                    from_name="",
                    to_name=destination.icao,
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
                counts_toward_totals=False,
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
