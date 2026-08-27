import { expect, test } from "@playwright/test";

import { buildFuelPlanDisplay } from "../src/fuelPlanDisplay";

import type {
  AdoptedValue,
  CalculationOutcome,
  FlightPhase,
} from "../src/types";

function automatic(value: number): AdoptedValue<number> {
  return {
    automatic_value: value,
    automatic_metadata: {},
    manual_override: null,
    adopted_source: "AUTOMATIC",
  };
}

function section(
  phase: FlightPhase,
  fuelGal: number,
  eteSeconds: number,
): CalculationOutcome["sections"][number] {
  return {
    phase,
    section_fuel_gal: automatic(fuelGal),
    zone_ete_seconds: automatic(eteSeconds),
  } as CalculationOutcome["sections"][number];
}

function tenths(value: number): number {
  return Math.round(value * 10);
}

test("Fuel Plan display arithmetic uses already-rounded visible operands", () => {
  const outcome = {
    sections: [
      section("CLIMB", 0.14, 89.4),
      section("CLIMB", 0.14, 89.4),
      section("CRUISE", 0.14, 29.4),
      section("CRUISE", 0.14, 29.4),
      section("DESCENT", 0.14, 59.4),
      section("VISUAL_ARRIVAL", 0.14, 59.4),
    ],
    fuel_plan: {
      total_usable_gal: 20.04,
      taxi_runup_minutes: 10,
      taxi_runup_gal: 1.46,
      climb_gal: 0.28,
      cruise_gal: 0.28,
      descent_gal: 0.28,
      additional_gal: 2.75,
      tgl_gal: 2,
      reserve_gal: 12.36,
      bof_gal: 5.59,
      min_required_gal: 19.41,
      extra_gal: 0.63,
      extra_endurance_seconds: 137.45,
    },
  } as CalculationOutcome;
  const exactBefore = structuredClone(outcome);

  const display = buildFuelPlanDisplay(outcome);

  expect(display).toMatchObject({
    taxiRunupGal: 1.5,
    climbGal: 0.2,
    cruiseGal: 0.2,
    descentGal: 0.2,
    tglGal: 2,
    additionalGal: 2.8,
    reserveGal: 12.4,
    bofGal: 5.4,
    minRequiredGal: 19.3,
    extraGal: 0.7,
    totalGal: 20,
    climbMinutes: 3,
    cruiseMinutes: 1,
    descentMinutes: 2,
    minRequiredMinutes: 78,
    extraMinutes: 3,
    totalMinutes: 81,
  });
  expect(tenths(display.bofGal!)).toBe(
    tenths(display.climbGal!) + tenths(display.cruiseGal!)
      + tenths(display.descentGal!) + tenths(display.tglGal)
      + tenths(display.additionalGal),
  );
  expect(tenths(display.minRequiredGal!)).toBe(
    tenths(display.taxiRunupGal) + tenths(display.bofGal!)
      + tenths(display.reserveGal),
  );
  expect(tenths(display.extraGal!)).toBe(
    tenths(display.totalGal) - tenths(display.minRequiredGal!),
  );
  expect(tenths(display.totalGal)).toBe(
    tenths(display.minRequiredGal!) + tenths(display.extraGal!),
  );
  expect(display.minRequiredMinutes).toBe(
    display.taxiRunupMinutes + display.climbMinutes! + display.cruiseMinutes!
      + display.descentMinutes! + display.tglMinutes + display.additionalMinutes
      + display.reserveMinutes,
  );
  expect(display.totalMinutes).toBe(
    display.minRequiredMinutes! + display.extraMinutes!,
  );
  expect(outcome).toEqual(exactBefore);
});
