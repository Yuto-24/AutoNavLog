import {
  AlertTriangle,
  Calculator,
  CloudSun,
  CheckCircle2,
  Database,
  Download,
  XCircle,
} from "lucide-react";
import type { ReadinessState, RuntimeState } from "../types";

interface StatusPanelProps {
  runtime: RuntimeState;
  readiness: ReadinessState;
  projectExists: boolean;
  canCalculate: boolean;
  destinationConfirmed: boolean;
  destinationReady: boolean;
  outcomeExists: boolean;
  busy: boolean;
  onCalculate: () => void;
  onConfirmDestination: () => void;
  onAcknowledge: (ackKey: string, checked: boolean) => void;
  onDownload: () => void;
}

export function StatusPanel({
  runtime,
  readiness,
  projectExists,
  canCalculate,
  destinationConfirmed,
  destinationReady,
  outcomeExists,
  busy,
  onCalculate,
  onConfirmDestination,
  onAcknowledge,
  onDownload,
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
          <dd>{runtime.weatherLabel}</dd>
        </div>
      </dl>

      {runtime.developmentWeather && (
        <div className="demo-weather-warning">
          <AlertTriangle aria-hidden="true" size={18} />
          <p>
            <strong>開発用気象モード</strong>
            固定気象で画面と計算を試せますが、転記補助HTMLは出力できません。
          </p>
        </div>
      )}

      {projectExists && (
        <div className="destination-confirmation-action">
          {destinationConfirmed ? (
            <p className="destination-confirmed-state">
              <CheckCircle2 aria-hidden="true" size={18} />
              目的空港・場周高度は確定済みです
            </p>
          ) : (
            <button
              className="primary-button full-width"
              type="button"
              onClick={onConfirmDestination}
              disabled={!destinationReady || busy}
            >
              目的空港・場周高度を確定
            </button>
          )}
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
          {outcomeExists ? "NAV LOGを再計算" : "NAV LOGを作る"}
        </button>
        <button
          className="secondary-button full-width output-button"
          type="button"
          onClick={onDownload}
          disabled={!readiness.transferAidAllowed || busy}
        >
          <Download aria-hidden="true" size={18} />
          A4転記補助HTMLを出力
        </button>
        {!readiness.transferAidAllowed && outcomeExists && (
          <p className="button-reason">ブロッカー解消・確認事項の承認・再計算後に有効になります。</p>
        )}
      </div>
    </aside>
  );
}
