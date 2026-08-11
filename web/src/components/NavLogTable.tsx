import type { AdoptedValue, CalculationOutcome } from "../types";

function adopted<T>(value: AdoptedValue<T>): T | null {
  return value.adopted_source === "MANUAL"
    ? value.manual_override
    : value.automatic_value;
}

interface FormattedValue {
  text: string;
  manual: boolean;
  unavailable: boolean;
}

type NumberFormatter = (value: number) => string;

function roundHalfUp(value: number, quantum: number): number {
  const scaled = Math.abs(value) / quantum;
  const rounded =
    Math.sign(value) *
    Math.floor(scaled + 0.5 + Number.EPSILON * Math.max(1, scaled)) *
    quantum;
  return Object.is(rounded, -0) ? 0 : rounded;
}

function fixedQuantum(quantum: number, fractionDigits: number): NumberFormatter {
  return (value) => roundHalfUp(value, quantum).toFixed(fractionDigits);
}

const integer = fixedQuantum(1, 0);
const distance = fixedQuantum(0.5, 1);
const durationMinutes = fixedQuantum(0.5, 1);
const fuelAmount = fixedQuantum(0.1, 1);
const CLIMB_PHASES = new Set<string>(["CLIMB"]);
const CRUISE_PHASES = new Set<string>(["CRUISE"]);
const DESCENT_PHASES = new Set<string>(["DESCENT", "VISUAL_ARRIVAL"]);

const bearing = (value: number) => {
  const normalized = ((value % 360) + 360) % 360;
  const rounded = roundHalfUp(normalized, 1) % 360;
  return `${rounded.toFixed(0).padStart(3, "0")}°`;
};
const signedAngle = (value: number) => {
  const rounded = roundHalfUp(value, 1);
  return `${rounded >= 0 ? "+" : ""}${rounded.toFixed(0)}°`;
};

function numberValue(
  value: AdoptedValue<number>,
  formatter: NumberFormatter = integer,
): FormattedValue {
  const selected = adopted(value);
  return {
    text: selected === null ? "未取得" : formatter(selected),
    manual: value.adopted_source === "MANUAL",
    unavailable: selected === null,
  };
}

function valueClass(formatted: FormattedValue): string {
  return formatted.unavailable
    ? "unavailable-value"
    : formatted.manual
      ? "manual-value"
      : "";
}

function ValueCell({
  value,
  formatter = integer,
}: {
  value: AdoptedValue<number>;
  formatter?: NumberFormatter;
}) {
  const formatted = numberValue(value, formatter);
  return (
    <td className={valueClass(formatted)}>
      {formatted.text}
      {formatted.manual && <small>手入力</small>}
    </td>
  );
}

function CombinedValueCell({
  first,
  second,
  formatter = integer,
}: {
  first: AdoptedValue<number>;
  second: AdoptedValue<number>;
  formatter?: NumberFormatter;
}) {
  const firstValue = numberValue(first, formatter);
  const secondValue = numberValue(second, formatter);
  const formatted: FormattedValue = {
    text: firstValue.text + " / " + secondValue.text,
    manual: firstValue.manual || secondValue.manual,
    unavailable: firstValue.unavailable || secondValue.unavailable,
  };
  return (
    <td className={valueClass(formatted)}>
      {formatted.text}
      {formatted.manual && <small>手入力</small>}
    </td>
  );
}

function ete(value: AdoptedValue<number>): FormattedValue {
  const seconds = adopted(value);
  if (seconds === null) {
    return { text: "未取得", manual: false, unavailable: true };
  }
  return {
    text: durationMinutes(seconds / 60),
    manual: value.adopted_source === "MANUAL",
    unavailable: false,
  };
}

function wind(
  direction: AdoptedValue<number>,
  speed: AdoptedValue<number>,
): FormattedValue {
  const directionValue = adopted(direction);
  const speedValue = adopted(speed);
  const manual =
    direction.adopted_source === "MANUAL" || speed.adopted_source === "MANUAL";
  if (speedValue === null || (speedValue >= 0.5 && directionValue === null)) {
    return { text: "未取得", manual, unavailable: true };
  }
  if (speedValue < 0.5) {
    return { text: "CALM", manual, unavailable: false };
  }
  return {
    text:
      integer(((directionValue ?? 0) % 360 + 360) % 360).padStart(3, "0") +
      "/" +
      integer(speedValue),
    manual,
    unavailable: false,
  };
}

function WindCell({
  direction,
  speed,
}: {
  direction: AdoptedValue<number>;
  speed: AdoptedValue<number>;
}) {
  const formatted = wind(direction, speed);
  return (
    <td className={valueClass(formatted)}>
      {formatted.text}
      {formatted.manual && <small>手入力</small>}
    </td>
  );
}

function CombinedEteCell({
  first,
  second,
}: {
  first: AdoptedValue<number>;
  second: AdoptedValue<number>;
}) {
  const firstValue = ete(first);
  const secondValue = ete(second);
  const formatted: FormattedValue = {
    text: firstValue.text + " / " + secondValue.text,
    manual: firstValue.manual || secondValue.manual,
    unavailable: firstValue.unavailable || secondValue.unavailable,
  };
  return (
    <td className={valueClass(formatted)}>
      {formatted.text}
      {formatted.manual && <small>手入力</small>}
    </td>
  );
}


function phaseMinutes(outcome: CalculationOutcome, phases: Set<string>): number | null {
  const matching = outcome.sections.filter((section) => phases.has(section.phase));
  const seconds = matching.map((section) => adopted(section.zone_ete_seconds));
  if (outcome.sections.length === 0 || seconds.some((value) => value === null)) return null;
  return seconds.reduce<number>((sum, value) => sum + (value ?? 0), 0) / 60;
}

function FuelTime({ minutes }: { minutes: number | null }) {
  if (minutes === null) return <div className="fuel-time" />;
  const rounded = roundHalfUp(minutes, 1);
  const hours = Math.floor(rounded / 60);
  const remaining = Math.round(rounded % 60);
  return (
    <div className="fuel-time">
      <span>{hours}</span>
      <span>:</span>
      <span>{remaining.toString().padStart(2, "0")}</span>
    </div>
  );
}

function FuelAmount({ amount }: { amount: number | null }) {
  return (
    <div className="fuel-amount">
      <span>{amount === null ? "" : fuelAmount(amount)}</span>
      <span>G</span>
    </div>
  );
}

function FuelPlanTable({ outcome }: { outcome: CalculationOutcome }) {
  const fuel = outcome.fuel_plan;
  const climb = phaseMinutes(outcome, CLIMB_PHASES);
  const cruise = phaseMinutes(outcome, CRUISE_PHASES);
  const descent = phaseMinutes(outcome, DESCENT_PHASES);
  const tgl = fuel.tgl_gal / 2 * 7;
  const required = [10, climb, cruise, descent, tgl, 10, 45];
  const minRequired = required.some((value) => value === null)
    ? null
    : required.reduce<number>((sum, value) => sum + (value ?? 0), 0);
  const extra =
    fuel.extra_endurance_seconds === null ? null : fuel.extra_endurance_seconds / 60;
  const total = minRequired === null || extra === null ? null : minRequired + extra;
  const bofRows = [
    ["CLIMB", climb, fuel.climb_gal],
    ["CRUISE", cruise, fuel.cruise_gal],
    ["DESCENT", descent, fuel.descent_gal],
    ["TGL", tgl, fuel.tgl_gal],
    ["ADDITIONAL", 10, fuel.additional_gal],
  ] as const;
  return (
    <table className="fuel-plan-table">
        <colgroup><col /><col /><col /><col /><col /></colgroup>
        <thead><tr><th colSpan={3} /><th>TIME</th><th>FUEL</th></tr></thead>
        <tbody>
          <tr>
            <td className="fuel-gray" />
            <td colSpan={2} className="fuel-strong">TAXI・RUN UP</td>
            <td><FuelTime minutes={10} /></td>
            <td><FuelAmount amount={fuel.taxi_runup_gal} /></td>
          </tr>
          {bofRows.map(([label, minutes, amount], index) => (
            <tr key={label}>
              {index === 0 && <td rowSpan={6} className="fuel-gray" />}
              {index === 0 && <td rowSpan={5} className="fuel-bof">BOF</td>}
              <td className="fuel-phase">{label}</td>
              <td><FuelTime minutes={minutes} /></td>
              <td><FuelAmount amount={amount} /></td>
            </tr>
          ))}
          <tr className="fuel-reserve-row">
            <td colSpan={2} className="fuel-strong">RESERVE</td>
            <td><FuelTime minutes={45} /></td>
            <td><FuelAmount amount={fuel.reserve_gal} /></td>
          </tr>
          <tr className="fuel-min-row">
            <td colSpan={3} className="fuel-gray fuel-strong">MIN REQUIRED</td>
            <td className="fuel-gray"><FuelTime minutes={minRequired} /></td>
            <td className="fuel-gray"><FuelAmount amount={fuel.min_required_gal} /></td>
          </tr>
          <tr className="fuel-extra-row">
            <td colSpan={3} className="fuel-strong">EXTRA</td>
            <td><FuelTime minutes={extra} /></td>
            <td><FuelAmount amount={fuel.extra_gal} /></td>
          </tr>
          <tr>
            <td colSpan={3} className="fuel-strong">TOTAL</td>
            <td><FuelTime minutes={total} /></td>
            <td><FuelAmount amount={fuel.total_usable_gal} /></td>
          </tr>
        </tbody>
    </table>
  );
}

export function NavLogTable({ outcome }: { outcome: CalculationOutcome }) {
  return (
    <section className="nav-log-section" aria-labelledby="nav-log-title">
      <div className="nav-log-heading">
        <div>
          <h2 id="nav-log-title">NAV LOG</h2>
          <p>計画値を確認し、公式様式へ手書きで転記するための非公式補助です。</p>
        </div>
        <span>Forecast Run: {outcome.selected_forecast_run_id ?? "未選択"}</span>
      </div>
      <div className="table-scroll nav-log-scroll">
        <div className="nav-log-tables">
          <table className="nav-log-table official-nav-log-table">
          <thead>
            <tr>
              <th>FROM</th>
              <th>TO</th>
              <th>PA<br /><small>ft</small></th>
              <th>TOAT<br /><small>°C</small></th>
              <th>CAS<br /><small>kt</small></th>
              <th>TAS<br /><small>kt</small></th>
              <th>TC</th>
              <th>VAR</th>
              <th>MC</th>
              <th>WIND</th>
              <th>WCA</th>
              <th>MH</th>
              <th>ZONE / CUM<br /><small>DIST NM</small></th>
              <th>GS<br /><small>kt</small></th>
              <th>ZONE / CUM<br /><small>ETE min</small></th>
              <th>ETO</th>
              <th>ATO</th>
              <th>ATE</th>
              <th>SECT / REM<br /><small>FUEL gal</small></th>
            </tr>
          </thead>
          <tbody>
            {outcome.sections.map((section) => {
              const variationValue = adopted(section.variation_deg_east);
              const variation: FormattedValue = {
                text:
                  variationValue === null
                    ? "未取得"
                    : (variationValue >= 0 ? "E " : "W ") +
                      integer(Math.abs(variationValue)) + "°",
                manual: section.variation_deg_east.adopted_source === "MANUAL",
                unavailable: variationValue === null,
              };
              return (
                <tr key={section.section_id}>
                  <td className="route-name-cell">{section.from_name}</td>
                  <td className="route-name-cell">{section.to_name}</td>
                  <ValueCell value={section.planned_altitude_ft_msl} />
                  <ValueCell value={section.temperature_c} />
                  <ValueCell value={section.cas_kt} />
                  <ValueCell value={section.tas_kt} />
                  <ValueCell value={section.true_course_deg} formatter={bearing} />
                  <td className={valueClass(variation)}>
                    {variation.text}
                    {variation.manual && <small>手入力</small>}
                  </td>
                  <ValueCell value={section.magnetic_course_deg} formatter={bearing} />
                  <WindCell
                    direction={section.wind_direction_deg_from}
                    speed={section.wind_speed_kt}
                  />
                  <ValueCell value={section.wca_deg} formatter={signedAngle} />
                  <ValueCell value={section.magnetic_heading_deg} formatter={bearing} />
                  <CombinedValueCell
                    first={section.zone_distance_nm}
                    second={section.cumulative_distance_nm}
                    formatter={distance}
                  />
                  <ValueCell value={section.ground_speed_kt} />
                  <CombinedEteCell
                    first={section.zone_ete_seconds}
                    second={section.cumulative_ete_seconds}
                  />
                  <td className="manual-entry-cell" aria-label="ETO転記欄" />
                  <td className="manual-entry-cell" aria-label="ATO転記欄" />
                  <td className="manual-entry-cell" aria-label="ATE転記欄" />
                  <CombinedValueCell
                    first={section.section_fuel_gal}
                    second={section.remaining_fuel_gal}
                    formatter={fuelAmount}
                  />
                </tr>
              );
            })}
          </tbody>
          </table>
          <FuelPlanTable outcome={outcome} />
        </div>
      </div>
      <p className="nav-log-disclaimer">
        本表示は地上準備の転記補助です。運航の可否を決定する資料ではありません。
        最新の気象・NOTAM・AIPおよび適用可能な原資料を確認してください。
      </p>
    </section>
  );
}
