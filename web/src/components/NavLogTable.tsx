import { draftFromSection } from "../navLogEditing";
import type {
  NavLogEditDrafts,
  NavLogEditErrors,
  NavLogEditableField,
} from "../navLogEditing";
import type {
  AdoptedValue, AltitudeGuidance, CalculationOutcome, DestinationWindForecast,
  NavSection, Project, RjfmDepartureGuidance,
  FlightPhase,
  NavLogDisplayCell, NavLogDisplayRow, SectionResult,
} from "../types";
import { RjfmGuidancePanel } from "./RjfmGuidancePanel";

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
const fuelAmount = fixedQuantum(0.1, 1);
const CLIMB_PHASES = new Set<string>(["CLIMB"]);
const CRUISE_PHASES = new Set<string>(["CRUISE"]);
const DESCENT_PHASES = new Set<string>(["DESCENT", "VISUAL_ARRIVAL"]);

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
  displayPlaceholder,
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
  displayPlaceholder?: string;
  required?: boolean;
}) {
  const formatted = numberValue(value, formatter);
  return (
    <td
      className={`nav-log-editable-cell ${error ? "nav-log-invalid-cell" : ""}`}
      data-display-text={displayPlaceholder ?? formatted.text}
    >
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
        placeholder={displayPlaceholder ?? formatted.text}
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
  displayText,
  onChange,
}: {
  direction: AdoptedValue<number>;
  speed: AdoptedValue<number>;
  directionValue: string;
  speedValue: string;
  label: string;
  errors: Partial<Record<NavLogEditableField, string>>;
  displayText?: string;
  onChange: (field: NavLogEditableField, value: string) => void;
}) {
  const invalid = Boolean(errors.windDirection || errors.windSpeed);
  const automaticDirection = adopted(direction);
  const automaticSpeed = adopted(speed);
  const [displayDirection, displaySpeed] = displayText?.includes("/")
    ? displayText.split("/", 2)
    : [undefined, displayText === "CALM" ? "0" : undefined];
  const directionPlaceholder = displayDirection ?? (
    automaticDirection === null
      ? "DIR"
      : integer(((automaticDirection % 360) + 360) % 360).padStart(3, "0")
  );
  const speedPlaceholder = displaySpeed ?? (
    automaticSpeed === null ? "kt" : integer(automaticSpeed)
  );
  return (
    <td
      className={`nav-log-editable-cell ${invalid ? "nav-log-invalid-cell" : ""}`}
      data-display-text={displayText ?? ""}
    >
      <div className="nav-log-wind-editor">
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
            placeholder={directionPlaceholder}
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
            placeholder={speedPlaceholder}
            onChange={(event) => onChange("windSpeed", event.target.value)}
          />
        </div>
      </div>
      {invalid && <small className="nav-log-field-error">風向・風速を確認</small>}
      {!invalid && (directionValue.trim() || speedValue.trim()) && <small>手入力</small>}
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

function displayCellClass(cell: NavLogDisplayCell): string {
  return [
    "derived-readonly-cell",
    `display-${cell.state.toLowerCase().replaceAll("_", "-")}`,
    cell.state === "UNAVAILABLE" ? "unavailable-value" : "",
    cell.manual ? "manual-value" : "",
  ].filter(Boolean).join(" ");
}

function DisplayCell({
  cell,
  extraClass = "",
}: {
  cell: NavLogDisplayCell;
  extraClass?: string;
}) {
  return (
    <td
      className={`${displayCellClass(cell)} ${extraClass}`.trim()}
      title={cell.reason_code ?? "計算結果から生成した表示専用セルです。"}
      data-display-text={cell.text ?? ""}
      data-cell-state={cell.state}
    >
      {cell.text ?? ""}
      {cell.manual && <small>手入力</small>}
    </td>
  );
}

function isVisibleCell(cell: NavLogDisplayCell): boolean {
  return cell.state === "DISPLAY_VALUE" || cell.state === "UNAVAILABLE";
}

function DisplayResultCells({
  row,
  source,
  inputSection,
  altitudeFixed,
  drafts,
  editErrors,
  onEdit,
}: {
  row: NavLogDisplayRow;
  source?: SectionResult;
  inputSection?: NavSection;
  altitudeFixed: boolean;
  drafts: NavLogEditDrafts;
  editErrors: NavLogEditErrors;
  onEdit: (
    sectionId: string,
    phase: FlightPhase,
    field: NavLogEditableField,
    value: string,
  ) => void;
}) {
  const editable = source !== undefined && inputSection !== undefined && row.phase !== null;
  const draft = inputSection === undefined
    ? undefined
    : drafts[inputSection.id] ?? draftFromSection(inputSection);
  const errors = inputSection === undefined ? {} : editErrors[inputSection.id] ?? {};
  const isVisualArrival = row.phase === "VISUAL_ARRIVAL";
  const altitudeEditable = editable
    && !altitudeFixed
    && row.phase === "CRUISE"
    && row.pa_display_kind === "NUMERIC"
    && isVisibleCell(row.pa);
  const inputLabel = `${source?.from_name ?? row.from_name}→${source?.to_name ?? row.to_name}`;
  const change = (field: NavLogEditableField, value: string) => {
    if (inputSection !== undefined && row.phase !== null) {
      onEdit(inputSection.id, row.phase, field, value);
    }
  };
  return (
    <>
      {!altitudeEditable || source === undefined || draft === undefined ? (
        <DisplayCell cell={row.pa} />
      ) : (
        <EditableNumberCell
          value={source.planned_altitude_ft_msl}
          draftValue={draft.plannedAltitude}
          field="plannedAltitude"
          label={`${inputLabel} 計画高度`}
          error={errors.plannedAltitude}
          min={100}
          max={25_000}
          step={100}
          displayPlaceholder={row.pa.text ?? undefined}
          required
          onChange={change}
        />
      )}
      {editable && source !== undefined && draft !== undefined && isVisibleCell(row.toat) && !(
        row.row_type === "PHYSICAL_LEG_SUMMARY" && source.phase === "CLIMB"
      ) ? (
        <EditableNumberCell
          value={source.temperature_c}
          draftValue={draft.temperatureByPhase[row.phase!] ?? ""}
          field="temperature"
          label={`${inputLabel} 手動気温`}
          error={errors.temperature}
          min={-80}
          max={60}
          step={0.1}
          displayPlaceholder={row.toat.text ?? undefined}
          onChange={change}
        />
      ) : (
        <DisplayCell cell={row.toat} />
      )}
      <DisplayCell cell={row.cas} />
      {isVisualArrival || !editable || source === undefined || draft === undefined || !isVisibleCell(row.tas) ? (
        <DisplayCell cell={row.tas} />
      ) : (
        <EditableNumberCell
          value={source.tas_kt}
          draftValue={draft.tas}
          field="tas"
          label={`${inputLabel} 手動TAS`}
          error={errors.tas}
          min={1}
          max={300}
          step={1}
          displayPlaceholder={row.tas.text ?? undefined}
          onChange={change}
        />
      )}
      <DisplayCell cell={row.tc} />
      <DisplayCell cell={row.variation} />
      <DisplayCell cell={row.mc} />
      {isVisualArrival || !editable || source === undefined || draft === undefined || !isVisibleCell(row.wind) ? (
        <DisplayCell cell={row.wind} />
      ) : (
        <EditableWindCell
          direction={source.wind_direction_deg_from}
          speed={source.wind_speed_kt}
          directionValue={draft.windDirectionByPhase[row.phase!] ?? ""}
          speedValue={draft.windSpeedByPhase[row.phase!] ?? ""}
          label={inputLabel}
          errors={errors}
          displayText={row.wind.text ?? undefined}
          onChange={change}
        />
      )}
      <DisplayCell cell={row.wca} />
      <DisplayCell cell={row.mh} />
      <DisplayCell cell={row.distance} />
      <DisplayCell cell={row.gs} />
      <DisplayCell cell={row.ete} />
      <DisplayCell cell={row.eto} extraClass="manual-entry-cell" />
      <DisplayCell cell={row.ato} extraClass="manual-entry-cell" />
      <DisplayCell cell={row.ate} extraClass="manual-entry-cell" />
      <DisplayCell cell={row.fuel} />
    </>
  );
}

function formatJst(value: string): string {
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function DestinationWindSummary({
  forecast,
}: {
  forecast: DestinationWindForecast | null;
}) {
  if (forecast === null) return null;
  const available =
    forecast.availability === "AVAILABLE" && forecast.wind_speed_kt !== null;
  const wind =
    !available
      ? "取得できませんでした"
      : forecast.wind_speed_kt === 0
        ? "CALM"
        : `${
            forecast.variable_direction
              ? "VRB"
              : String(forecast.wind_direction_deg_from ?? 0).padStart(3, "0")
          }/${forecast.wind_speed_kt}${
            forecast.wind_gust_kt === null ? "" : `G${forecast.wind_gust_kt}`
          } kt`;
  return (
    <section className="destination-wind-summary" aria-label="目的地空港の風予報">
      <strong>目的地風: {wind}</strong>
      <span>{forecast.airport_icao}</span>
      {forecast.valid_time_utc && (
        <span>到着予定 {formatJst(forecast.valid_time_utc)} JST</span>
      )}
      <span>出典: {forecast.source_label}</span>
      {available && forecast.forecast_change && (
        <span>区分: {forecast.forecast_change}</span>
      )}
      {forecast.raw_taf && (
        <details>
          <summary>TAF原文</summary>
          <code>{forecast.raw_taf}</code>
        </details>
      )}
      <p>
        目的地風はNAV LOG最終行への表示専用です。到着区間の計算はCALMです。
      </p>
    </section>
  );
}

export function NavLogTable({
  outcome,
  destinationWind,
  altitudeGuidance,
  rjfmGuidance,
  project,
  drafts,
  editErrors,
  editStatus,
  onEdit,
}: {
  outcome: CalculationOutcome;
  destinationWind: DestinationWindForecast | null;
  altitudeGuidance: AltitudeGuidance;
  rjfmGuidance: RjfmDepartureGuidance | null;
  project: Project;
  drafts: NavLogEditDrafts;
  editErrors: NavLogEditErrors;
  editStatus: { kind: "idle" | "pending" | "saving" | "saved" | "error"; message: string };
  onEdit: (
    sectionId: string,
    phase: FlightPhase,
    field: NavLogEditableField,
    value: string,
  ) => void;
}) {
  const displayRows: NavLogDisplayRow[] = outcome.display_rows;
  const inputSections = new Map(project.sections.map((section) => [section.id, section]));
  const altitudeGuidanceBySection = new Map(
    altitudeGuidance.sections.map((guidance) => [guidance.sectionId, guidance]),
  );
  const sourceResults = new Map(
    outcome.sections.map((section) => [section.sequence, section]),
  );
  return (
    <section className="nav-log-section" aria-labelledby="nav-log-title">
      <div className="nav-log-heading">
        <div>
          <h2 id="nav-log-title">NAV LOG</h2>
          <p>入力色の欄は直接編集でき、約0.7秒後に自動再計算します。</p>
        </div>
        <span>Forecast Run: {outcome.selected_forecast_run_id ?? "未選択"}</span>
      </div>
      <section className="qnh-summary" aria-label="NAV LOG高度ポリシー">
        <strong>PA = MSL</strong>
        <span>QNH補正はNAV LOG計算に使用しません。</span>
      </section>
      <DestinationWindSummary forecast={destinationWind} />
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
            {displayRows.length === 0 && (
              <tr><td colSpan={19} className="unavailable-value">表示行を再計算してください</td></tr>
            )}
            {displayRows.map((row) => {
              if (row.row_type === "LEG_SEPARATOR") {
                return (
                  <tr
                    className="nav-leg-spacer-row"
                    aria-hidden="true"
                    key={row.sequence}
                    data-row-type={row.row_type}
                    data-row-sequence={row.sequence}
                  >
                    <td colSpan={19} />
                  </tr>
                );
              }
              const source = row.source_result_sequence === null
                ? undefined
                : sourceResults.get(row.source_result_sequence);
              const inputSection = row.section_id === null
                ? undefined
                : inputSections.get(row.section_id);
              const rowClass = row.row_type === "PHYSICAL_LEG_SUMMARY"
                ? "nav-leg-heading-row"
                : row.row_type === "DESTINATION_INFO"
                  ? "nav-destination-info-row"
                  : "nav-leg-detail-row";
              return (
                <tr
                  className={rowClass}
                  key={row.sequence}
                  data-row-type={row.row_type}
                  data-row-sequence={row.sequence}
                >
                  <td className="route-name-cell" data-display-text={row.from_name}>{row.from_name}</td>
                  <td className="route-name-cell" data-display-text={row.to_name}>{row.to_name}</td>
                  <DisplayResultCells
                    row={row}
                    source={source}
                    inputSection={inputSection}
                    altitudeFixed={
                      inputSection !== undefined
                      && altitudeGuidanceBySection.get(inputSection.id)?.inputMode !== undefined
                      && altitudeGuidanceBySection.get(inputSection.id)?.inputMode !== "EDITABLE"
                    }
                    drafts={drafts}
                    editErrors={editErrors}
                    onEdit={onEdit}
                  />
                </tr>
              );
            })}
          </tbody>
          </table>
          <FuelPlanTable outcome={outcome} />
        </div>
      </div>
      {rjfmGuidance && <RjfmGuidancePanel guidance={rjfmGuidance} />}
      <p className="nav-log-disclaimer">
        本表示は地上準備の転記補助です。運航の可否を決定する資料ではありません。
        最新の気象・NOTAM・AIPおよび適用可能な原資料を確認してください。
      </p>
    </section>
  );
}
