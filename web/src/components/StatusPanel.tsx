import {
  AlertTriangle,
  Calculator,
  CloudSun,
  Database,
  XCircle,
} from "lucide-react";
import type { ReadinessState, RuntimeState } from "../types";

interface StatusPanelProps {
  runtime: RuntimeState;
  readiness: ReadinessState;
  projectExists: boolean;
  weatherMode: "FORECAST" | "FTD";
  canCalculate: boolean;
  outcomeExists: boolean;
  busy: boolean;
  activeOperation: "calculate" | null;
  onCalculate: () => void;
  onAcknowledge: (ackKey: string, checked: boolean) => void;
}

export function StatusPanel({
  runtime,
  readiness,
  projectExists,
  weatherMode,
  canCalculate,
  outcomeExists,
  busy,
  activeOperation,
  onCalculate,
  onAcknowledge,
}: StatusPanelProps) {
  const blockers = readiness.issues.filter((issue) => issue.severity === "BLOCKER");
  const warnings = readiness.issues.filter((issue) => issue.severity === "WARNING");

  return (
    <aside className="status-rail" aria-label="準備状況">
      <h2>準備状況</h2>
      <div className="next-action">
        <span>次の操作</span>
        <strong>{readiness.nextAction}</strong>
      </div>

      <dl className="provenance-list">
        <div>
          <dt><Database aria-hidden="true" size={16} />参照</dt>
          <dd>{runtime.referenceRevision}</dd>
        </div>
        <div>
          <dt><Database aria-hidden="true" size={16} />性能</dt>
          <dd>{runtime.performanceRevision ?? "未確定"}</dd>
        </div>
        <div>
          <dt><CloudSun aria-hidden="true" size={16} />気象</dt>
          <dd>{weatherMode === "FTD" ? "FTD固定気象（ISA）" : runtime.weatherLabel}</dd>
        </div>
      </dl>

      {runtime.developmentWeather && weatherMode !== "FTD" && (
        <div className="demo-weather-warning">
          <AlertTriangle aria-hidden="true" size={18} />
          <p>
            <strong>開発用気象モード</strong>
            固定気象で画面と計算を試せます。
          </p>
        </div>
      )}


      <div className="issue-summary">
        <span>ブロッカー {blockers.length}</span>
        <span>確認事項 {warnings.length}</span>
      </div>

      <div className="issue-list">
        {readiness.issues.length === 0 && projectExists && (
          <p className="no-issues">現在表示すべきブロッカー・確認事項はありません。</p>
        )}
        {readiness.issues.map((issue) => (
          <article
            className={`issue-item ${issue.severity === "BLOCKER" ? "issue-blocker" : "issue-warning"}`}
            key={issue.ackKey}
          >
            {issue.severity === "BLOCKER" ? (
              <XCircle aria-hidden="true" size={18} />
            ) : (
              <AlertTriangle aria-hidden="true" size={18} />
            )}
            <div>
              <strong>{issue.code}</strong>
              <p>{issue.message}</p>
              {issue.code === "CRUISE_POWER_TABLE_BOUNDARY_USED" &&
                issue.boundaryProvenance && (
                  <div className="boundary-provenance" aria-label="巡航出力表端の採用詳細">
                    {issue.location && (
                      <p className="boundary-location">
                        <strong>{issue.location.fromName} → {issue.location.toName}</strong>
                        {issue.location.zoneCount > 1 && (
                          <> · 巡航 Zone {issue.location.zoneOrdinal}/{issue.location.zoneCount}</>
                        )}
                        {(issue.location.zoneFromName !== issue.location.fromName ||
                          issue.location.zoneToName !== issue.location.toName) && (
                          <>（{issue.location.zoneFromName} → {issue.location.zoneToName}）</>
                        )}
                      </p>
                    )}
                    {issue.boundaryProvenance.map((corner) => (
                      <dl key={`${corner.pressureAltitudeFt}-${corner.isaDeviationC}`}>
                        <div><dt>Corner</dt><dd>PA {corner.pressureAltitudeFt?.toLocaleString()} ft / ISA {corner.isaDeviationC}°C</dd></div>
                        <div><dt>Requested</dt><dd>{corner.requestedValue}%</dd></div>
                        <div><dt>Available</dt><dd>{corner.availableMin}–{corner.availableMax}%</dd></div>
                        <div><dt>Adopted</dt><dd>{corner.adoptedValue}%</dd></div>
                      </dl>
                    ))}
                  </div>
                )}
              <small>次の操作: {issue.action}</small>
              {issue.acknowledgementRequired && (
                <label className="checkbox-row issue-acknowledgement">
                  <input
                    type="checkbox"
                    checked={issue.acknowledged}
                    onChange={(event) =>
                      onAcknowledge(issue.ackKey, event.target.checked)
                    }
                    disabled={busy}
                  />
                  <span>この確認事項を確認済みにする</span>
                </label>
              )}
            </div>
          </article>
        ))}
      </div>

      <div className="status-actions">
        <button
          className="primary-button full-width"
          type="button"
          onClick={onCalculate}
          disabled={!canCalculate || busy}
        >
          <Calculator aria-hidden="true" size={18} />
          {activeOperation === "calculate"
            ? "NAV LOGを計算中…"
            : outcomeExists ? "NAV LOGを再計算" : "NAV LOGを作る"}
        </button>
      </div>
    </aside>
  );
}
