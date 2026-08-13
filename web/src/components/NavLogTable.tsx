import { Fragment } from "react";
import { draftFromSection } from "../navLogEditing";
import type {
  NavLogEditDrafts,
  NavLogEditErrors,
  NavLogEditableField,
} from "../navLogEditing";
import type {
  AdoptedValue, CalculationOutcome, NavSection, Project, SectionResult,
} from "../types";

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

/**
 * Round half-up with exact decimal semantics matching Python's round_half_up().
 * Replicates: Decimal(str(value)) / Decimal(str(quantum)), quantize with ROUND_HALF_UP.
 *
 * Uses exact integer arithmetic (BigInt) to avoid floating-point precision errors.
 *
 * Examples:
 *   roundHalfUp(0.35, 0.1) => 0.4     // Rounds up (half-up)
 *   roundHalfUp(-0.35, 0.1) => -0.4   // Negative half-up
 *   roundHalfUp(1.005, 0.01) => 1.01  // Rounds up
 *   roundHalfUp(2.5, 1) => 3          // Positive half-way case
 *   roundHalfUp(-2.5, 1) => -3        // Negative half-way case
 *   roundHalfUp(7.125, 1) => 7        // Rounds down
 *   roundHalfUp(7.5, 1) => 8          // Rounds up
 *   roundHalfUp(2.375, 1) => 2        // Rounds down
 *   roundHalfUp(-0.4, 1) => 0         // Normalizes -0 to +0
 */
function roundHalfUp(value: number, quantum: number): number {
  // Parse decimal strings into { sign, integer, fraction, scale }
  const parseDecimal = (str: string) => {
    const trimmed = str.trim();
    const sign = trimmed.startsWith("-") ? -1 : 1;
    const unsigned = trimmed.replace(/^[+-]/, "");
    const [intPart = "0", fracPart = ""] = unsigned.split(".");
    return { sign, integer: intPart, fraction: fracPart, scale: fracPart.length };
  };

  const v = parseDecimal(value.toString());
  const q = parseDecimal(quantum.toString());

  // Combine integer and fraction parts into exact BigInt representations
  // Scale both to a common denominator: 10^(max(v.scale, q.scale))
  const maxScale = Math.max(v.scale, q.scale);
  const scaleFactor = 10n ** BigInt(maxScale);

  const vInt = BigInt(v.integer + v.fraction.padEnd(maxScale, "0"));
  const qInt = BigInt(q.integer + q.fraction.padEnd(maxScale, "0"));

  // Perform exact division: scaled = vInt / qInt (with half-up rounding)
  // Half-up: if remainder >= divisor/2, round up
  const absVInt = vInt < 0n ? -vInt : vInt;
  const absQInt = qInt < 0n ? -qInt : qInt;

  const quotient = absVInt / absQInt;
  const remainder = absVInt % absQInt;

  // Half-up: round up if remainder * 2 >= divisor
  const roundedQuotient = remainder * 2n >= absQInt ? quotient + 1n : quotient;

  // Apply original signs
  const resultSign = v.sign * q.sign;
  const signedQuotient = resultSign < 0 ? -roundedQuotient : roundedQuotient;

  // Convert back: result = signedQuotient * quantum
  // Build result string from exact integer arithmetic
  const resultInt = signedQuotient * qInt;
  const resultStr = resultInt.toString();
  const resultSign2 = resultStr.startsWith("-") ? "-" : "";
  const resultUnsigned = resultStr.replace(/^-/, "");

  // Special case: when maxScale === 0, no fractional part exists
  let resultDecimal: string;
  if (maxScale === 0) {
    resultDecimal = resultSign2 + resultUnsigned;
  } else {
    const resultPadded = resultUnsigned.padStart(maxScale + 1, "0");
    const resultIntPart = resultPadded.slice(0, resultPadded.length - maxScale) || "0";
    const resultFracPart = resultPadded.slice(resultPadded.length - maxScale);
    resultDecimal = resultFracPart
      ? resultSign2 + resultIntPart + "." + resultFracPart
      : resultSign2 + resultIntPart;
  }

  const result = parseFloat(resultDecimal);

  // Normalize -0 to +0
  return Object.is(result, -0) ? 0 : result;
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
  return rounded.toFixed(0).padStart(3, "0");
};
const signedInteger = (value: number) => {
  const rounded = roundHalfUp(value, 1);
  return `${rounded >= 0 ? "+" : ""}${rounded.toFixed(0)}`;
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
    <td
      className={`${valueClass(formatted)} derived-readonly-cell`}
      title="計算・出典から導出されるため、この欄は読み取り専用です。"
    >
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
    <td
      className={`${valueClass(formatted)} derived-readonly-cell`}
      title="計算・出典から導出されるため、この欄は読み取り専用です。"
    >
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
    <td
      className={`${valueClass(formatted)} derived-readonly-cell`}
      title="計算・出典から導出されるため、この欄は読み取り専用です。"
    >
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
    <td
      className={`${valueClass(formatted)} derived-readonly-cell`}
      title="計算・出典から導出されるため、この欄は読み取り専用です。"
    >
      {formatted.text}
      {formatted.manual && <small>手入力</small>}
    </td>
  );
}

function EditableNumberCell({
  value,
  draftValue,
  field,
  label,
  error,
  onChange,
  min,
  max,
  step,
  formatter = integer,
  required = false,
}: {
  value: AdoptedValue<number>;
  draftValue: string;
  field: NavLogEditableField;
  label: string;
  error?: string;
  onChange: (field: NavLogEditableField, value: string) => void;
  min: number;
  max: number;
  step: number;
  formatter?: NumberFormatter;
  required?: boolean;
}) {
  const formatted = numberValue(value, formatter);
  return (
    <td className={`nav-log-editable-cell ${error ? "nav-log-invalid-cell" : ""}`}>
      <input
        className="nav-log-number-input"
        type="number"
        min={min}
        max={max}
        step={step}
        required={required}
        aria-label={label}
        aria-invalid={Boolean(error)}
        title={error ?? (required ? "この値は必須です。" : "空欄にすると自動値へ戻ります。")}
        value={draftValue}
        placeholder={formatted.text}
        onChange={(event) => onChange(field, event.target.value)}
      />
      {error && <small className="nav-log-field-error">要確認</small>}
      {!error && !required && draftValue.trim() && <small>手入力</small>}
    </td>
  );
}

function EditableWindCell({
  direction,
  speed,
  directionValue,
  speedValue,
  label,
  errors,
  onChange,
}: {
  direction: AdoptedValue<number>;
  speed: AdoptedValue<number>;
  directionValue: string;
  speedValue: string;
  label: string;
  errors: Partial<Record<NavLogEditableField, string>>;
  onChange: (field: NavLogEditableField, value: string) => void;
}) {
  const formatted = wind(direction, speed);
  const invalid = Boolean(errors.windDirection || errors.windSpeed);
  return (
    <td className={`nav-log-editable-cell ${invalid ? "nav-log-invalid-cell" : ""}`}>
      <div className="nav-log-wind-inputs">
        <input
          className="nav-log-number-input"
          type="number"
          min="0"
          max="359"
          step="1"
          aria-label={`${label} 手動風向`}
          aria-invalid={Boolean(errors.windDirection)}
          title={errors.windDirection ?? "空欄にすると自動値へ戻ります。"}
          value={directionValue}
          placeholder="DIR"
          onChange={(event) => onChange("windDirection", event.target.value)}
        />
        <span>/</span>
        <input
          className="nav-log-number-input"
          type="number"
          min="0"
          max="200"
          step="1"
          aria-label={`${label} 手動風速`}
          aria-invalid={Boolean(errors.windSpeed)}
          title={errors.windSpeed ?? "空欄にすると自動値へ戻ります。"}
          value={speedValue}
          placeholder="kt"
          onChange={(event) => onChange("windSpeed", event.target.value)}
        />
      </div>
      {invalid && <small className="nav-log-field-error">風向・風速を確認</small>}
      {!invalid && (directionValue.trim() || speedValue.trim()) && <small>手入力</small>}
      {!invalid &&
        !directionValue.trim() &&
        !speedValue.trim() &&
        !formatted.manual && (
          <small className="nav-log-automatic-wind">自動: {formatted.text}</small>
        )}
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

function ResultCells({
  section,
  inputSection,
  drafts,
  editErrors,
  onEdit,
}: {
  section: SectionResult;
  inputSection: NavSection;
  drafts: NavLogEditDrafts;
  editErrors: NavLogEditErrors;
  onEdit: (sectionId: string, field: NavLogEditableField, value: string) => void;
}) {
  const variationValue = adopted(section.variation_deg_east);
  const departureLatitude =
    section.variation_deg_east.automatic_metadata.departure_latitude_deg;
  const variationTitle = typeof departureLatitude === "number"
    ? `出発緯度 ${departureLatitude.toFixed(4)}° / 32.0°N基準で自動判定`
    : "Leg出発緯度から自動判定";
  const variation: FormattedValue = {
    text: variationValue === null ? "未取得" : signedInteger(variationValue),
    manual: section.variation_deg_east.adopted_source === "MANUAL",
    unavailable: variationValue === null,
  };
  const draft = drafts[inputSection.id] ?? draftFromSection(inputSection);
  const errors = editErrors[inputSection.id] ?? {};
  const isVisualArrival = inputSection.phase === "VISUAL_ARRIVAL";
  const inputLabel = `${section.from_name}→${section.to_name}`;
  const change = (field: NavLogEditableField, value: string) => {
    onEdit(inputSection.id, field, value);
  };
  return (
    <>
      {isVisualArrival ? (
        <ValueCell value={section.planned_altitude_ft_msl} />
      ) : (
        <EditableNumberCell
          value={section.planned_altitude_ft_msl}
          draftValue={draft.plannedAltitude}
          field="plannedAltitude"
          label={`${inputLabel} 計画高度`}
          error={errors.plannedAltitude}
          min={100}
          max={25_000}
          step={100}
          required
          onChange={change}
        />
      )}
      <EditableNumberCell
        value={section.temperature_c}
        draftValue={draft.temperature}
        field="temperature"
        label={`${inputLabel} 手動気温`}
        error={errors.temperature}
        min={-80}
        max={60}
        step={1}
        onChange={change}
      />
      <ValueCell value={section.cas_kt} />
      {isVisualArrival ? (
        <ValueCell value={section.tas_kt} />
      ) : (
        <EditableNumberCell
          value={section.tas_kt}
          draftValue={draft.tas}
          field="tas"
          label={`${inputLabel} 手動TAS`}
          error={errors.tas}
          min={1}
          max={300}
          step={1}
          onChange={change}
        />
      )}
      <ValueCell value={section.true_course_deg} formatter={bearing} />
      <td
        className={`${valueClass(variation)} derived-readonly-cell`}
        title={`${variationTitle} / Project入力から導出される読み取り専用値です。`}
      >
        {variation.text}
        {variation.manual && <small>手入力</small>}
        {!variation.manual && !variation.unavailable && <small>自動</small>}
      </td>
      <ValueCell value={section.magnetic_course_deg} formatter={bearing} />
      {isVisualArrival ? (
        <WindCell direction={section.wind_direction_deg_from} speed={section.wind_speed_kt} />
      ) : (
        <EditableWindCell
          direction={section.wind_direction_deg_from}
          speed={section.wind_speed_kt}
          directionValue={draft.windDirection}
          speedValue={draft.windSpeed}
          label={inputLabel}
          errors={errors}
          onChange={change}
        />
      )}
      <ValueCell value={section.wca_deg} formatter={signedInteger} />
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
    </>
  );
}

function groupByPhysicalLeg(sections: SectionResult[]): SectionResult[][] {
  const groups: SectionResult[][] = [];
  for (const section of sections) {
    const current = groups.at(-1);
    if (current?.[0]?.section_id === section.section_id) {
      current.push(section);
    } else {
      groups.push([section]);
    }
  }
  return groups;
}

export function NavLogTable({
  outcome,
  project,
  drafts,
  editErrors,
  editStatus,
  onEdit,
}: {
  outcome: CalculationOutcome;
  project: Project;
  drafts: NavLogEditDrafts;
  editErrors: NavLogEditErrors;
  editStatus: { kind: "idle" | "pending" | "saving" | "saved" | "error"; message: string };
  onEdit: (sectionId: string, field: NavLogEditableField, value: string) => void;
}) {
  const physicalLegs = groupByPhysicalLeg(outcome.sections);
  const inputSections = new Map(project.sections.map((section) => [section.id, section]));
  const qnh = adopted(outcome.qnh_hpa);
  const qnhMetadata = outcome.qnh_hpa.automatic_metadata;
  const qnhValues =
    typeof qnhMetadata.values === "object" && qnhMetadata.values !== null
      ? (qnhMetadata.values as Record<string, unknown>)
      : {};
  const qnhMethod =
    outcome.qnh_hpa.adopted_source === "MANUAL"
      ? "MANUAL"
      : String(qnhValues.qnh_method ?? qnhMetadata.qnh_method ?? "未確定");
  const metarTime = qnhValues.metar_observation_time_utc;
  const forecastTime = qnhValues.forecast_time_utc;
  const tendency = qnhValues.msm_tendency_hpa;
  const showAutomaticQnhDetails = outcome.qnh_hpa.adopted_source !== "MANUAL";
  return (
    <section className="nav-log-section" aria-labelledby="nav-log-title">
      <div className="nav-log-heading">
        <div>
          <h2 id="nav-log-title">NAV LOG</h2>
          <p>入力色の欄は直接編集でき、約0.7秒後に自動再計算します。</p>
        </div>
        <span>Forecast Run: {outcome.selected_forecast_run_id ?? "未選択"}</span>
      </div>
      <section className="qnh-summary" aria-label="採用QNHと出典">
        <strong>採用QNH: {qnh === null ? "未取得" : `${qnh.toFixed(1)} hPa`}</strong>
        <span>方式: {qnhMethod}</span>
        {showAutomaticQnhDetails && typeof metarTime === "string" && (
          <span>基準METAR: {metarTime}</span>
        )}
        {showAutomaticQnhDetails && typeof forecastTime === "string" && (
          <span>予報対象: {forecastTime}</span>
        )}
        {showAutomaticQnhDetails && typeof tendency === "number" && (
          <span>MSM変化量: {tendency.toFixed(1)} hPa</span>
        )}
        {(outcome.qnh_hpa.warnings ?? []).map((warning) => (
          <span className="qnh-warning" key={warning}>⚠ {warning}</span>
        ))}
      </section>
      <div className="nav-log-edit-guide" id="nav-log-edit-guide">
        <span className="nav-log-editable-key">編集可: PA / TOAT / TAS / WIND</span>
        <span className="nav-log-readonly-key">読取専用: 航法・距離・時間・燃料などの派生値</span>
        <span className={`nav-log-edit-status nav-log-edit-status-${editStatus.kind}`} role="status" aria-live="polite">
          {editStatus.message}
        </span>
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
            {physicalLegs.map((leg) => {
              const first = leg[0];
              const last = leg.at(-1);
              if (!first || !last) return null;
              return (
                <Fragment key={first.section_id}>
                  <tr className="nav-leg-heading-row">
                    <td className="route-name-cell">{first.from_name}</td>
                    <td className="route-name-cell">{last.to_name}</td>
                    <td colSpan={17} aria-hidden="true" />
                  </tr>
                  {leg.map((section) => (
                    <tr
                      className="nav-leg-detail-row"
                      key={`${section.section_id}-${section.sequence}`}
                    >
                      <td aria-hidden="true" />
                      <td className="route-name-cell">{section.to_name}</td>
                      {inputSections.has(section.section_id) ? (
                        <ResultCells
                          section={section}
                          inputSection={inputSections.get(section.section_id)!}
                          drafts={drafts}
                          editErrors={editErrors}
                          onEdit={onEdit}
                        />
                      ) : (
                        <td colSpan={17} className="unavailable-value">
                          入力元Legが見つかりません
                        </td>
                      )}
                    </tr>
                  ))}
                  <tr className="nav-leg-spacer-row" aria-hidden="true">
                    <td colSpan={19} />
                  </tr>
                </Fragment>
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
