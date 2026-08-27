import { useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import {
  DEFAULT_VOR_STATION,
  formatVorRadialDistance,
  nearestVorStation,
  orderVorStationsForRoute,
  VOR_AUTO_SELECTION,
  VOR_DATASET_EFFECTIVE_CYCLE,
  VOR_STATIONS,
} from "../vorRadial";
import { draftFromSection } from "../navLogEditing";
import { roundHalfUp } from "../displayRounding";
import { buildFuelPlanDisplay } from "../fuelPlanDisplay";
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

function fixedQuantum(quantum: number, fractionDigits: number): NumberFormatter {
  return (value) => roundHalfUp(value, quantum).toFixed(fractionDigits);
}

const integer = fixedQuantum(1, 0);
const BASE_NAV_LOG_WIDTH_PX = 1776;
const VOR_COLUMN_WIDTH_PX = 96;

interface VorColumn {
  id: number;
  stationIdentifier: string | null;
}

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
            min="1"
            max="360"
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

function FuelTime({ minutes }: { minutes: number | null }) {
  if (minutes === null) return <div className="fuel-time" />;
  const sign = minutes < 0 ? "-" : "";
  const absoluteMinutes = Math.abs(minutes);
  const hours = Math.floor(absoluteMinutes / 60);
  const remaining = absoluteMinutes % 60;
  return (
    <div className="fuel-time">
      <span>{sign}{hours}</span>
      <span>:</span>
      <span>{remaining.toString().padStart(2, "0")}</span>
    </div>
  );
}

function FuelAmount({ amount }: { amount: number | null }) {
  return (
    <div className="fuel-amount">
      <span>{amount === null ? "" : amount.toFixed(1)}</span>
      <span>G</span>
    </div>
  );
}

function FuelPlanTable({ outcome }: { outcome: CalculationOutcome }) {
  const display = buildFuelPlanDisplay(outcome);
  const bofRows = [
    ["CLIMB", display.climbMinutes, display.climbGal],
    ["CRUISE", display.cruiseMinutes, display.cruiseGal],
    ["DESCENT", display.descentMinutes, display.descentGal],
    ["TGL", display.tglMinutes, display.tglGal],
    ["ADDITIONAL", display.additionalMinutes, display.additionalGal],
  ] as const;
  return (
    <table className="fuel-plan-table">
        <colgroup><col /><col /><col /><col /><col /></colgroup>
        <thead><tr><th colSpan={3} /><th>TIME</th><th>FUEL</th></tr></thead>
        <tbody>
          <tr>
            <td className="fuel-gray" />
            <td colSpan={2} className="fuel-strong">TAXI・RUN UP</td>
            <td><FuelTime minutes={display.taxiRunupMinutes} /></td>
            <td><FuelAmount amount={display.taxiRunupGal} /></td>
          </tr>
          {bofRows.map(([label, minutes, amount], index) => (
            <tr key={label}>
              {index === 0 && <td rowSpan={6} className="fuel-gray" />}
              {index === 0 && (
                <td rowSpan={5} className="fuel-bof">
                  BOF<br /><small>{display.bofGal === null ? "" : `${display.bofGal.toFixed(1)} G`}</small>
                </td>
              )}
              <td className="fuel-phase">{label}</td>
              <td><FuelTime minutes={minutes} /></td>
              <td><FuelAmount amount={amount} /></td>
            </tr>
          ))}
          <tr className="fuel-reserve-row">
            <td colSpan={2} className="fuel-strong">RESERVE</td>
            <td><FuelTime minutes={display.reserveMinutes} /></td>
            <td><FuelAmount amount={display.reserveGal} /></td>
          </tr>
          <tr className="fuel-min-row">
            <td colSpan={3} className="fuel-gray fuel-strong">MIN REQUIRED</td>
            <td className="fuel-gray"><FuelTime minutes={display.minRequiredMinutes} /></td>
            <td className="fuel-gray"><FuelAmount amount={display.minRequiredGal} /></td>
          </tr>
          <tr className="fuel-extra-row">
            <td colSpan={3} className="fuel-strong">EXTRA</td>
            <td><FuelTime minutes={display.extraMinutes} /></td>
            <td><FuelAmount amount={display.extraGal} /></td>
          </tr>
          <tr>
            <td colSpan={3} className="fuel-strong">TOTAL</td>
            <td><FuelTime minutes={display.totalMinutes} /></td>
            <td><FuelAmount amount={display.totalGal} /></td>
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
  const [vorColumns, setVorColumns] = useState<VorColumn[]>([
    { id: 0, stationIdentifier: null },
  ]);
  const automaticVorStation = useMemo(() => {
    const firstFromRow = displayRows.find(
      (row) => row.from_latitude_deg != null && row.from_longitude_deg != null,
    );
    return firstFromRow?.from_latitude_deg != null
      && firstFromRow.from_longitude_deg != null
      ? nearestVorStation(firstFromRow.from_latitude_deg, firstFromRow.from_longitude_deg)
      : DEFAULT_VOR_STATION;
  }, [displayRows]);
  const selectedVorStations = vorColumns.map((column) => {
    if (column.stationIdentifier === null) return automaticVorStation;
    if (column.stationIdentifier === "") return null;
    return VOR_STATIONS.find(
      (station) => station.identifier === column.stationIdentifier,
    ) ?? null;
  });
  const navLogColumnCount = 19 + vorColumns.length;
  const navLogWidth = BASE_NAV_LOG_WIDTH_PX
    + (vorColumns.length - 1) * VOR_COLUMN_WIDTH_PX;
  const orderedVorStations = useMemo(() => {
    const route = [...project.route_nodes].sort((left, right) => left.sequence - right.sequence);
    return orderVorStationsForRoute(route);
  }, [project.route_nodes]);

  const addVorColumn = () => {
    setVorColumns((current) => [
      {
        id: current.reduce((maxId, column) => Math.max(maxId, column.id), -1) + 1,
        stationIdentifier: "",
      },
      ...current,
    ]);
  };
  const removeVorColumn = (columnId: number) => {
    setVorColumns((current) => (
      current.length === 1 ? current : current.filter((column) => column.id !== columnId)
    ));
  };
  const selectVorStation = (columnId: number, stationIdentifier: string) => {
    setVorColumns((current) => current.map((column) => (
      column.id === columnId
        ? {
            ...column,
            stationIdentifier: stationIdentifier === VOR_AUTO_SELECTION
              ? null
              : stationIdentifier,
          }
        : column
    )));
  };

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
      <DestinationWindSummary forecast={destinationWind} />
      <div className="nav-log-edit-guide" id="nav-log-edit-guide">
        <span className="nav-log-editable-key">編集可: PA / TOAT / TAS / WIND</span>
        <span className="nav-log-readonly-key">読取専用: 航法・距離・時間・燃料などの派生値</span>
        <span className={`nav-log-edit-status nav-log-edit-status-${editStatus.kind}`} role="status" aria-live="polite">
          {editStatus.message}
        </span>
      </div>
      <div className="nav-log-column-actions">
        <button
          className="secondary-button nav-log-add-column-button"
          type="button"
          onClick={addVorColumn}
        >
          <Plus aria-hidden="true" size={15} />
          VOR/DME列を追加
        </button>
        <span>各列で基準局を選び、不要な列は見出しから削除できます。</span>
      </div>
      <div className="table-scroll nav-log-scroll">
        <div className="nav-log-tables">
          <table
            className="nav-log-table official-nav-log-table"
            style={{ flexBasis: navLogWidth, width: navLogWidth, minWidth: navLogWidth }}
          >
          <thead>
            <tr>
              {vorColumns.map((column, index) => (
                <th className="vor-reference-header" key={column.id}>
                  <div className="vor-reference-heading">
                    <label htmlFor={`nav-log-vor-station-${column.id}`}>
                      {vorColumns.length === 1 ? "VOR/DME" : `VOR/DME ${index + 1}`}
                    </label>
                    {vorColumns.length > 1 && (
                      <button
                        className="vor-column-remove-button"
                        type="button"
                        aria-label={`VOR/DME ${index + 1}列目を削除`}
                        title="このVOR/DME列を削除"
                        onClick={() => removeVorColumn(column.id)}
                      >
                        <Trash2 aria-hidden="true" size={12} />
                      </button>
                    )}
                  </div>
                  <select
                    id={`nav-log-vor-station-${column.id}`}
                    aria-label={index === 0 ? "VOR基準局" : `VOR基準局 ${index + 1}`}
                    title={`AIP ${VOR_DATASET_EFFECTIVE_CYCLE} / 局からTOへのradial・距離`}
                    value={column.stationIdentifier ?? VOR_AUTO_SELECTION}
                    onChange={(event) => selectVorStation(column.id, event.target.value)}
                  >
                    {column.stationIdentifier === "" && <option value="">局を選択</option>}
                    <option value={VOR_AUTO_SELECTION}>
                      {`自動 ${automaticVorStation.identifier}`}
                    </option>
                    {orderedVorStations.map((station) => (
                      <option key={station.identifier} value={station.identifier}>
                        {`${station.identifier} — ${station.name} (${station.type})`}
                      </option>
                    ))}
                  </select>
                </th>
              ))}
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
              <tr><td colSpan={navLogColumnCount} className="unavailable-value">表示行を再計算してください</td></tr>
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
                    <td colSpan={navLogColumnCount} />
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
                  {selectedVorStations.map((station, index) => {
                    const vorReference = station === null
                      ? "—"
                      : formatVorRadialDistance(
                          station,
                          row.to_latitude_deg,
                          row.to_longitude_deg,
                        );
                    return (
                      <td
                        className="vor-reference-cell derived-readonly-cell"
                        key={vorColumns[index]!.id}
                        title={station === null
                          ? "VOR/DME基準局を選択してください。"
                          : `${station.identifier}からTOへのradial / 距離（表示専用セル）`}
                        data-display-text={vorReference}
                        data-vor-column={index + 1}
                      >
                        {vorReference}
                      </td>
                    );
                  })}
                  <td
                    className="route-name-cell route-from-cell"
                    data-display-text={row.from_name}
                  >
                    {row.from_name}
                  </td>
                  <td
                    className="route-name-cell route-to-cell"
                    data-display-text={row.to_name}
                  >
                    {row.to_name}
                  </td>
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
        本表示は地上準備用です。運航の可否を決定する資料ではありません。
        最新の気象・NOTAM・AIPおよび適用可能な原資料を確認してください。
      </p>
    </section>
  );
}
