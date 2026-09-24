import { ClipboardPaste, Route } from "lucide-react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import type { PlanningForm } from "../forms";
import type { AirportOption, ImportState } from "../types";

interface ImportPlanPanelProps {
  importState: ImportState;
  airports: AirportOption[];
  form: PlanningForm;
  setForm: Dispatch<SetStateAction<PlanningForm>>;
  projectExists: boolean;
  busy: boolean;
  fileInput: ReactNode;
  onPaste: () => void;
  onResumeKmz?: () => void;
  onResumePaste?: () => void;
}

function destinationAirportLabel(airport: AirportOption): string {
  const roundedPatternAltitude =
    Math.floor(airport.patternAltitudeFtMsl / 100 + 0.5) * 100;
  const verification =
    airport.patternAltitudeValidationStatus === "VERIFIED" ? "" : "・未検証";
  return `${airport.icao} ${airport.name}（場周 ${roundedPatternAltitude.toLocaleString("ja-JP")} ft${verification}）`;
}

function routeCandidateLabel(
  candidate: ImportState["candidates"][number],
  duplicateName: boolean,
  ordinal: number,
): string {
  const containerLabel = candidate.containerPath?.join(" / ");
  const contextualName = containerLabel && containerLabel !== candidate.name
    ? containerLabel
    : candidate.name;
  const candidateName = duplicateName
    ? `${contextualName}（候補${ordinal + 1}）`
    : candidate.name;
  if (candidate.kind === "connected_lines") {
    const legCount = candidate.legCount ?? Math.max(0, candidate.vertexCount - 1);
    return `${candidateName} · ${legCount} Leg${
      candidate.distanceNm === null ? "" : ` · ${candidate.distanceNm} NM`
    }`;
  }
  return `${candidateName} · ${candidate.vertexCount}点${
    candidate.distanceNm === null ? "" : ` · ${candidate.distanceNm} NM`
  }`;
}

export function ImportPlanPanel({
  importState,
  airports,
  form,
  setForm,
  projectExists,
  busy,
  fileInput,
  onPaste,
  onResumeKmz,
  onResumePaste,
}: ImportPlanPanelProps) {
  const update = <Key extends keyof PlanningForm>(key: Key, value: PlanningForm[Key]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };
  const selectedDeparture = airports.find(
    (airport) => airport.id === form.departureAirportId,
  );
  const selectedDestination = airports.find(
    (airport) => airport.id === form.destinationAirportId,
  );
  const candidateNameCounts = new Map<string, number>();
  for (const candidate of importState.candidates) {
    candidateNameCounts.set(
      candidate.name,
      (candidateNameCounts.get(candidate.name) ?? 0) + 1,
    );
  }
  const routeSelectionRequired = importState.candidates.length > 0 && !form.candidateKey;

  return (
    <aside className="input-rail" aria-label="経路と飛行計画">
      {onResumePaste && (
        <button className="secondary-button" onClick={onResumePaste}>貼付KMLの編集を続ける</button>
      )}
      {onResumeKmz && (
        <button className="secondary-button" onClick={onResumeKmz}>KMZ文書の選択を続ける</button>
      )}
      <section className="rail-section">
        <div className="section-heading-row">
          <h2>経路を取り込む</h2>
          {projectExists && <span className="quiet-state">確定済み</span>}
        </div>
        {!projectExists && (
          <>
            {fileInput}
            <button
              className="secondary-button full-width"
              type="button"
              onClick={onPaste}
              disabled={busy}
            >
              <ClipboardPaste aria-hidden="true" size={17} />
              KMLを貼り付け
            </button>
            <p className="quiet-state">ブラウザに貼り付けの確認が表示されたら、ペーストを選択してください。</p>
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
          <div className={`field-group candidate-control ${
            routeSelectionRequired ? "is-required" : ""
          }`}>
            <div className="candidate-control-heading">
              <label htmlFor="route-candidate">経路選択</label>
              {routeSelectionRequired && <span className="required-badge">選択必須</span>}
            </div>
            {routeSelectionRequired && (
              <p className="candidate-required-message" id="route-candidate-required">
                使用する飛行経路を選択してください。
              </p>
            )}
            <select
              id="route-candidate"
              aria-label="飛行経路候補"
              aria-describedby={routeSelectionRequired ? "route-candidate-required" : undefined}
              aria-invalid={routeSelectionRequired}
              required
              value={form.candidateKey}
              onChange={(event) => {
                const candidateKey = event.target.value;
                setForm((current) => ({
                  ...current,
                  candidateKey,
                  departureAirportId: "",
                  destinationAirportId: "",
                  destinationPatternAltitudeFtMsl: "",
                  routeUseConfirmed: false,
                  polygonRouteConfirmed: false,
                }));
              }}
            >
              <option value="">経路を選択</option>
              {importState.candidates.map((candidate, index) => (
                <option
                  key={`${candidate.kind}:${candidate.index}`}
                  value={`${candidate.kind}:${candidate.index}`}
                >
                  {routeCandidateLabel(
                    candidate,
                    (candidateNameCounts.get(candidate.name) ?? 0) > 1,
                    index,
                  )}
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
      </section>

      <section className="rail-section flight-plan-section">
        <div className="section-heading-row">
          <h2>飛行計画</h2>
          {projectExists && <span className="quiet-state">経路 確定済み</span>}
        </div>
        <div className="form-grid">
          <label className="flight-date-field">
            <span>DATE</span>
            <input
              type="date"
              value={form.flightDate}
              onChange={(event) => update("flightDate", event.target.value)}
            />
          </label>
          <label className="flight-time-field">
            <span>ETD JST</span>
            <input
              type="time"
              value={form.departureTimeJst}
              onChange={(event) => update("departureTimeJst", event.target.value)}
            />
          </label>
          <div className="endpoint-row span-two">
            <label>
              <span>FROM（自動取得）</span>
              <input
                aria-label="FROM"
                type="text"
                readOnly
                value={selectedDeparture ? `${selectedDeparture.icao} ${selectedDeparture.name}` : ""}
                placeholder="経路を選択すると自動設定"
                aria-invalid={Boolean(form.candidateKey && !selectedDeparture)}
              />
            </label>
            <label>
              <span>TO（自動取得）</span>
              <input
                aria-label="TO"
                type="text"
                readOnly
                value={selectedDestination ? destinationAirportLabel(selectedDestination) : ""}
                placeholder="経路を選択すると自動設定"
                aria-invalid={Boolean(form.candidateKey && !selectedDestination)}
              />
            </label>
            {form.candidateKey && (!selectedDeparture || !selectedDestination) && (
              <small className="field-help field-error span-two">
                KML端点から5 NM以内に空港が見つかりません。
              </small>
            )}
          </div>
          <div className="fuel-input-row span-two">
            <label>
              <span>FUEL gal</span>
              <input
                type="number"
                min="0.1"
                max="200"
                step="0.1"
                value={form.totalUsableFuelGal}
                onChange={(event) => update("totalUsableFuelGal", event.target.value)}
              />
            </label>
            <label>
              <span>TGL</span>
              <input
                type="number"
                min="0"
                max="20"
                step="1"
                value={form.tglCount}
                onChange={(event) => update("tglCount", event.target.value)}
              />
            </label>
          </div>
          <fieldset className="descent-rate-control span-two">
            <legend>計画降下率</legend>
            <div className="descent-rate-segments">
              {([500, 1000] as const).map((rate) => (
                <label key={rate}>
                  <input
                    type="radio"
                    name="descent-rate-fpm"
                    value={rate}
                    checked={form.descentRateFpm === rate}
                    onChange={() => update("descentRateFpm", rate)}
                  />
                  <span>
                    {rate} fpm
                    {rate === 500 && <small>標準</small>}
                  </span>
                </label>
              ))}
            </div>
            {form.descentRateFpm === 1000 && (
              <small className="descent-rate-note">標準計画値は500 fpmです。</small>
            )}
          </fieldset>
          <div className="fuel-option-row span-two">
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={form.runUpIncluded}
                onChange={(event) => update("runUpIncluded", event.target.checked)}
              />
              <span>RUN UP あり</span>
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={form.airConditioningEnabled}
                onChange={(event) => update("airConditioningEnabled", event.target.checked)}
              />
              <span>A/C ON (巡航速度 -2 kt)</span>
            </label>
            <label className="checkbox-row nose-fairing-option">
              <input
                type="checkbox"
                checked={form.noseFairingEnabled}
                onChange={(event) => update("noseFairingEnabled", event.target.checked)}
              />
              <span>ノーズフェアリングあり (OFF: -10 kt)</span>
            </label>
          </div>
          <label className="span-two">
            <span>気象モード</span>
            <select
              aria-label="気象モード"
              value={form.weatherMode}
              onChange={(event) =>
                update("weatherMode", event.target.value as "FORECAST" | "FTD")
              }
            >
              <option value="FORECAST">予報気象</option>
              <option value="FTD">FTD固定気象</option>
            </select>
          </label>
          {form.weatherMode === "FTD" && (
            <div className="ftd-weather-fields span-two" aria-label="FTD固定気象入力">
              <p>
                地上から5,000 ftまでは風ベクトルを線形補間し、それ以上は
                5,000 ftの風を使用します。気温は各高度の標準大気です。
              </p>
              <label>
                <span>地上風向 ° FROM</span>
                <input
                  type="number"
                  min="1"
                  max="360"
                  step="1"
                  value={form.ftdSurfaceWindDirection}
                  onChange={(event) =>
                    update("ftdSurfaceWindDirection", event.target.value)
                  }
                />
              </label>
              <label>
                <span>地上風速 kt</span>
                <input
                  type="number"
                  min="0"
                  max="200"
                  step="1"
                  value={form.ftdSurfaceWindSpeed}
                  onChange={(event) => update("ftdSurfaceWindSpeed", event.target.value)}
                />
              </label>
              <label>
                <span>5,000 ft風向 ° FROM</span>
                <input
                  type="number"
                  min="1"
                  max="360"
                  step="1"
                  value={form.ftdWind5000Direction}
                  onChange={(event) => update("ftdWind5000Direction", event.target.value)}
                />
              </label>
              <label>
                <span>5,000 ft風速 kt</span>
                <input
                  type="number"
                  min="0"
                  max="200"
                  step="1"
                  value={form.ftdWind5000Speed}
                  onChange={(event) => update("ftdWind5000Speed", event.target.value)}
                />
              </label>
            </div>
          )}
        </div>
      </section>
    </aside>
  );
}
