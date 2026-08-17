from __future__ import annotations

from collections.abc import Sequence

from autonavlog.domain.calculation import FuelPlan
from autonavlog.domain.enums import FlightPhase

RUN_UP_MINUTES = 10
RUN_UP_GAL = 1.5
ADDITIONAL_GAL = 2.8
TGL_GAL = 2.0
RESERVE_GAL = 12.4
DESCENT_GPH = 12.0
VISUAL_ARRIVAL_GPH = 12.0
EXTRA_ENDURANCE_GPH = 16.5


def fuel_for_section(
    phase: FlightPhase,
    ete_seconds: float | None,
    cruise_gph: float | None = None,
    climb_fuel_gal: float | None = None,
) -> float | None:
    if phase == FlightPhase.CLIMB:
        return climb_fuel_gal
    if ete_seconds is None:
        return None
    if phase == FlightPhase.CRUISE:
        return None if cruise_gph is None else cruise_gph * ete_seconds / 3600.0
    rate = DESCENT_GPH if phase == FlightPhase.DESCENT else VISUAL_ARRIVAL_GPH
    return rate * ete_seconds / 3600.0


def remaining_fuel(
    total_usable_gal: float,
    section_fuels: Sequence[float | None],
    *,
    run_up_included: bool = True,
) -> list[float | None]:
    remaining = total_usable_gal - (RUN_UP_GAL if run_up_included else 0.0)
    output: list[float | None] = []
    determined = True
    for amount in section_fuels:
        if not determined or amount is None:
            determined = False
            output.append(None)
            continue
        remaining -= amount
        output.append(remaining)
    return output


def build_fuel_plan(
    total_usable_gal: float,
    phases: Sequence[FlightPhase],
    section_fuels: Sequence[float | None],
    tgl_count: int,
    *,
    run_up_included: bool = True,
) -> FuelPlan:
    phase_totals: dict[FlightPhase, float | None] = {}
    for phase in FlightPhase:
        values = [
            fuel
            for item_phase, fuel in zip(phases, section_fuels, strict=True)
            if item_phase == phase
        ]
        phase_totals[phase] = (
            None
            if any(value is None for value in values)
            else sum(value for value in values if value is not None)
        )
    taxi_runup_minutes = RUN_UP_MINUTES if run_up_included else 0
    taxi_runup_gal = RUN_UP_GAL if run_up_included else 0.0
    min_required = None
    extra = None
    endurance = None
    bof = None
    if not any(value is None for value in section_fuels):
        route_fuel = sum(value for value in section_fuels if value is not None)
        bof = route_fuel + ADDITIONAL_GAL + tgl_count * TGL_GAL
        min_required = (
            taxi_runup_gal
            + route_fuel
            + ADDITIONAL_GAL
            + tgl_count * TGL_GAL
            + RESERVE_GAL
        )
        extra = total_usable_gal - min_required
        endurance = extra / EXTRA_ENDURANCE_GPH * 3600.0
    descent = phase_totals[FlightPhase.DESCENT]
    visual_arrival = phase_totals[FlightPhase.VISUAL_ARRIVAL]
    descent_total = None if descent is None or visual_arrival is None else descent + visual_arrival
    return FuelPlan(
        total_usable_gal=total_usable_gal,
        taxi_runup_minutes=taxi_runup_minutes,
        taxi_runup_gal=taxi_runup_gal,
        climb_gal=phase_totals[FlightPhase.CLIMB],
        cruise_gal=phase_totals[FlightPhase.CRUISE],
        descent_gal=descent_total,
        additional_gal=ADDITIONAL_GAL,
        tgl_gal=tgl_count * TGL_GAL,
        reserve_gal=RESERVE_GAL,
        bof_gal=bof,
        min_required_gal=min_required,
        extra_gal=extra,
        extra_endurance_seconds=endurance,
    )
