import { ClipboardPaste, FileUp, Route } from "lucide-react";
import type { Dispatch, DragEvent, SetStateAction } from "react";
import { convertQnhValue, variationForDeparture } from "../forms";
import type { PlanningForm, QnhUnit } from "../forms";
import type { AirportOption, ImportState } from "../types";

interface ImportPlanPanelProps {
  importState: ImportState;
  airports: AirportOption[];
  form: PlanningForm;
  setForm: Dispatch<SetStateAction<PlanningForm>>;
  projectExists: boolean;
  busy: boolean;
  onFile: (file: File) => void;
  onPaste: () => void;
  onConfirmRoute: () => void;
}

function destinationAirportLabel(airport: AirportOption): string {
  const roundedPatternAltitude =
    Math.floor(airport.patternAltitudeFtMsl / 100 + 0.5) * 100;
  const verification =
    airport.patternAltitudeValidationStatus === "VERIFIED" ? "" : "・未検証";
  return `${airport.icao} ${airport.name}（場周 ${roundedPatternAltitude.toLocaleString("ja-JP")} ft${verification}）`;
}

export function ImportPlanPanel({
  importState,
  airports,
  form,
  setForm,
  projectExists,
  busy,
  onFile,
  onPaste,
  onConfirmRoute,
}: ImportPlanPanelProps) {
  const update = <Key extends keyof PlanningForm>(key: Key, value: PlanningForm[Key]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };
  const selectedKind = form.candidateKey.split(":", 1)[0] ?? "";
  const selectedDeparture = airports.find(
    (airport) => airport.id === form.departureAirportId,
  );
  const selectedDestination = airports.find(
    (airport) => airport.id === form.destinationAirportId,
  );
  const acceptDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (busy) return;
    const file = event.dataTransfer.files[0];
    if (file) onFile(file);
  };

  return (
    <aside className="input-rail" aria-label="経路と飛行計画">
      <section className="rail-section">
        <div className="section-heading-row">
          <h2>経路を取り込む</h2>
          {projectExists && <span className="quiet-state">確定済み</span>}
        </div>
        {!projectExists && (
          <>
            <div
              className={`drop-zone ${busy ? "is-disabled" : ""}`}
              aria-disabled={busy}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = busy ? "none" : "copy";
              }}
              onDrop={acceptDrop}
            >
              <FileUp aria-hidden="true" size={26} />
              <strong>KML / KMZをドロップ</strong>
              <span>10 MiB以下</span>
              <label className="file-picker" htmlFor="route-file">
                ファイルを選択
              </label>
              <input
                id="route-file"
                className="visually-hidden"
                type="file"
                accept=".kml,.kmz,application/vnd.google-earth.kml+xml,application/vnd.google-earth.kmz"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) onFile(file);
                  event.target.value = "";
                }}
                disabled={busy}
              />
            </div>
            <button
              className="secondary-button full-width"
              type="button"
              onClick={onPaste}
              disabled={busy}
            >
              <ClipboardPaste aria-hidden="true" size={17} />
              KMLを貼り付け
            </button>
          </>
        )}
        {importState.filename && (
          <div className="imported-file">
            <Route aria-hidden="true" size={18} />
            <div>
              <strong>{importState.filename}</strong>
              <span>{importState.sourceFiles.join(" / ") || "KML"}</span>
            </div>
          </div>
        )}
        {!projectExists && importState.candidates.length > 0 && (
          <div className="field-group candidate-control">
            <label htmlFor="route-candidate">飛行経路にする形状</label>
            <select
              id="route-candidate"
              value={form.candidateKey}
              onChange={(event) => {
                update("candidateKey", event.target.value);
                update("routeUseConfirmed", false);
                update("polygonRouteConfirmed", false);
              }}
            >
              <option value="">形状を選択</option>
              {importState.candidates.map((candidate) => (
                <option
                  key={`${candidate.kind}:${candidate.index}`}
                  value={`${candidate.kind}:${candidate.index}`}
                >
                  {candidate.name} · {candidate.vertexCount}点
                  {candidate.distanceNm === null ? "" : ` · ${candidate.distanceNm} NM`}
                </option>
              ))}
            </select>
          </div>
        )}
        {importState.warnings.map((warning) => (
          <p className="inline-warning" key={warning}>
            {warning}
          </p>
        ))}
        {!projectExists && form.candidateKey && (
          <div className="confirmation-box">
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={form.routeUseConfirmed}
                onChange={(event) => update("routeUseConfirmed", event.target.checked)}
              />
              <span>地図とKML記載順を確認しました</span>
            </label>
            {selectedKind === "polygon" && (
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={form.polygonRouteConfirmed}
                  onChange={(event) =>
                    update("polygonRouteConfirmed", event.target.checked)
                  }
                />
                <span>Polygon境界の開始点・進行方向を記載順で使います</span>
              </label>
            )}
          </div>
        )}
      </section>

      <section className="rail-section flight-plan-section">
        <div className="section-heading-row">
          <h2>飛行計画</h2>
          {projectExists && <span className="quiet-state">経路 確定済み</span>}
        </div>
        <div className="form-grid">
          <label>
            <span>DATE</span>
            <input
              type="date"
              value={form.flightDate}
              onChange={(event) => update("flightDate", event.target.value)}
            />
          </label>
          <label>
            <span>ETD JST</span>
            <input
              type="time"
              value={form.departureTimeJst}
              onChange={(event) => update("departureTimeJst", event.target.value)}
            />
          </label>
          <label>
            <span>FROM（経路始点から自動設定・変更可）</span>
            <select
              aria-label="FROM"
              value={form.departureAirportId}
              aria-invalid={Boolean(form.candidateKey && !selectedDeparture)}
              onChange={(event) => {
                const departureAirportId = event.target.value;
                const departure = airports.find((airport) => airport.id === departureAirportId);
                setForm((current) => ({
                  ...current,
                  departureAirportId,
                  variationDegEast: variationForDeparture(departure),
                  manualQnhConfirmed: false,
                }));
              }}
            >
              <option value="">経路を選択すると自動設定</option>
              {airports.map((airport) => (
                <option key={airport.id} value={airport.id}>
                  {airport.icao} {airport.name}
                </option>
              ))}
            </select>
            {form.candidateKey && !selectedDeparture && (
              <small className="field-help field-error">
                KML始点から5 NM以内に出発空港が見つかりません。
              </small>
            )}
          </label>
          <label className="span-two">
            <span>TO（経路終点から自動設定）</span>
            <input
              aria-label="TO"
              type="text"
              readOnly
              value={selectedDestination ? destinationAirportLabel(selectedDestination) : ""}
              placeholder="経路を選択すると自動設定"
              aria-invalid={Boolean(form.candidateKey && !selectedDestination)}
            />
            {form.candidateKey && !selectedDestination && (
              <small className="field-help field-error">
                KML終点から5 NM以内に目的空港が見つかりません。
              </small>
            )}
          </label>
          <label>
            <span>FUEL gal</span>
            <input
              type="number"
              min="0.1"
              max="200"
              step="0.1"
              value={form.totalUsableFuelGal}
              onChange={(event) => {
                if (Number.isFinite(event.target.valueAsNumber)) {
                  update("totalUsableFuelGal", event.target.valueAsNumber);
                }
              }}
            />
          </label>
          <div className="form-grid-field span-two">
            <span>QNH（未入力は自動取得）</span>
            <div className="qnh-input-row">
              <input
                aria-label="QNH値"
                type="number"
                min={form.qnhUnit === "hPa" ? "800" : "23.63"}
                max={form.qnhUnit === "hPa" ? "1100" : "32.48"}
                step={form.qnhUnit === "hPa" ? "0.1" : "0.01"}
                value={form.manualQnhValue}
                onChange={(event) => {
                  update("manualQnhValue", event.target.value);
                  update("manualQnhConfirmed", false);
                }}
                placeholder="自動取得"
              />
              <select
                aria-label="QNH単位"
                value={form.qnhUnit}
                onChange={(event) => {
                  const nextUnit = event.target.value as QnhUnit;
                  setForm((current) => ({
                    ...current,
                    manualQnhValue: convertQnhValue(
                      current.manualQnhValue,
                      current.qnhUnit,
                      nextUnit,
                    ),
                    qnhUnit: nextUnit,
                    manualQnhConfirmed: false,
                  }));
                }}
              >
                <option value="hPa">hPa</option>
                <option value="inHg">inHg</option>
              </select>
            </div>
          </div>
          <label>
            <span>TGL</span>
            <input
              type="number"
              min="0"
              max="20"
              step="1"
              value={form.tglCount}
              onChange={(event) => {
                if (Number.isFinite(event.target.valueAsNumber)) {
                  update("tglCount", event.target.valueAsNumber);
                }
              }}
            />
          </label>
        </div>
        {form.manualQnhValue && (
          <div className="confirmation-box confirmation-box-plan">
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={form.manualQnhConfirmed}
                onChange={(event) => update("manualQnhConfirmed", event.target.checked)}
              />
              <span>このDATE・ETD・FROMのQNHとして確認しました</span>
            </label>
          </div>
        )}
        {!projectExists && (
          <button
            className="primary-button full-width"
            type="button"
            onClick={onConfirmRoute}
            disabled={
              !form.candidateKey ||
              !form.routeUseConfirmed ||
              !selectedDeparture ||
              !selectedDestination ||
              busy
            }
          >
            経路を確定
          </button>
        )}
      </section>
    </aside>
  );
}
