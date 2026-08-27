import { roundHalfUp } from "./displayRounding";
import type {
  AdoptedValue,
  CalculationOutcome,
  FlightPhase,
} from "./types";

const CLIMB_PHASES = new Set<FlightPhase>(["CLIMB"]);
const CRUISE_PHASES = new Set<FlightPhase>(["CRUISE"]);
const DESCENT_PHASES = new Set<FlightPhase>(["DESCENT", "VISUAL_ARRIVAL"]);
const TGL_MINUTES_PER_CIRCUIT = 7;
const TGL_GAL_PER_CIRCUIT = 2;
const ADDITIONAL_MINUTES = 10;
const RESERVE_MINUTES = 45;
const EXTRA_ENDURANCE_GPH = 16.5;

export interface FuelPlanDisplay {
  taxiRunupMinutes: number;
  taxiRunupGal: number;
  climbMinutes: number | null;
  climbGal: number | null;
  cruiseMinutes: number | null;
  cruiseGal: number | null;
  descentMinutes: number | null;
  descentGal: number | null;
  tglMinutes: number;
  tglGal: number;
  additionalMinutes: number;
  additionalGal: number;
  reserveMinutes: number;
  reserveGal: number;
  bofGal: number | null;
  minRequiredMinutes: number | null;
  minRequiredGal: number | null;
  extraMinutes: number | null;
  extraGal: number | null;
  totalMinutes: number | null;
  totalGal: number;
}

function adopted<T>(value: AdoptedValue<T>): T | null {
  return value.adopted_source === "MANUAL"
    ? value.manual_override
    : value.automatic_value;
}

function fuelTenths(value: number): number {
  return Math.round(roundHalfUp(value, 0.1) * 10);
}

function gallons(tenths: number | null): number | null {
  return tenths === null ? null : tenths / 10;
}

function phaseFuelTenths(
  outcome: CalculationOutcome,
  phases: Set<FlightPhase>,
): number | null {
  if (outcome.sections.length === 0) return null;
  const amounts = outcome.sections
    .filter((section) => phases.has(section.phase))
    .map((section) => adopted(section.section_fuel_gal));
  if (amounts.some((amount) => amount === null)) return null;
  return amounts.reduce<number>(
    (sum, amount) => sum + fuelTenths(amount ?? 0),
    0,
  );
}

function phaseMinutes(
  outcome: CalculationOutcome,
  phases: Set<FlightPhase>,
): number | null {
  if (outcome.sections.length === 0) return null;
  const seconds = outcome.sections
    .filter((section) => phases.has(section.phase))
    .map((section) => adopted(section.zone_ete_seconds));
  if (seconds.some((value) => value === null)) return null;
  const totalSeconds = seconds.reduce<number>((sum, value) => sum + (value ?? 0), 0);
  return roundHalfUp(totalSeconds / 60, 1);
}

function sumKnown(values: Array<number | null>): number | null {
  return values.some((value) => value === null)
    ? null
    : values.reduce<number>((sum, value) => sum + (value ?? 0), 0);
}

/** Build every Fuel Plan cell from the operands visible to the user. */
export function buildFuelPlanDisplay(outcome: CalculationOutcome): FuelPlanDisplay {
  const exact = outcome.fuel_plan;
  const climbFuel = phaseFuelTenths(outcome, CLIMB_PHASES);
  const cruiseFuel = phaseFuelTenths(outcome, CRUISE_PHASES);
  const descentFuel = phaseFuelTenths(outcome, DESCENT_PHASES);
  const taxiFuel = fuelTenths(exact.taxi_runup_gal);
  const tglFuel = fuelTenths(exact.tgl_gal);
  const additionalFuel = fuelTenths(exact.additional_gal);
  const reserveFuel = fuelTenths(exact.reserve_gal);
  const totalFuel = fuelTenths(exact.total_usable_gal);
  const bofFuel = sumKnown([
    climbFuel,
    cruiseFuel,
    descentFuel,
    tglFuel,
    additionalFuel,
  ]);
  const minRequiredFuel = bofFuel === null
    ? null
    : taxiFuel + bofFuel + reserveFuel;
  const extraFuel = minRequiredFuel === null ? null : totalFuel - minRequiredFuel;

  const climbTime = phaseMinutes(outcome, CLIMB_PHASES);
  const cruiseTime = phaseMinutes(outcome, CRUISE_PHASES);
  const descentTime = phaseMinutes(outcome, DESCENT_PHASES);
  const tglTime = exact.tgl_gal / TGL_GAL_PER_CIRCUIT * TGL_MINUTES_PER_CIRCUIT;
  const minRequiredTime = sumKnown([
    exact.taxi_runup_minutes,
    climbTime,
    cruiseTime,
    descentTime,
    tglTime,
    ADDITIONAL_MINUTES,
    RESERVE_MINUTES,
  ]);
  const extraTime = extraFuel === null
    ? null
    : roundHalfUp((extraFuel / 10) / EXTRA_ENDURANCE_GPH * 60, 1);
  const totalTime = minRequiredTime === null || extraTime === null
    ? null
    : minRequiredTime + extraTime;

  return {
    taxiRunupMinutes: exact.taxi_runup_minutes,
    taxiRunupGal: taxiFuel / 10,
    climbMinutes: climbTime,
    climbGal: gallons(climbFuel),
    cruiseMinutes: cruiseTime,
    cruiseGal: gallons(cruiseFuel),
    descentMinutes: descentTime,
    descentGal: gallons(descentFuel),
    tglMinutes: tglTime,
    tglGal: tglFuel / 10,
    additionalMinutes: ADDITIONAL_MINUTES,
    additionalGal: additionalFuel / 10,
    reserveMinutes: RESERVE_MINUTES,
    reserveGal: reserveFuel / 10,
    bofGal: gallons(bofFuel),
    minRequiredMinutes: minRequiredTime,
    minRequiredGal: gallons(minRequiredFuel),
    extraMinutes: extraTime,
    extraGal: gallons(extraFuel),
    totalMinutes: totalTime,
    totalGal: totalFuel / 10,
  };
}
