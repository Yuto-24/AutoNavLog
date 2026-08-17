import type {
  RjfmDepartureGuidance,
  RjfmGuidanceStatus,
  RjfmRunwayGuidance,
} from "../types";

export const rjfmStatusLabels: Record<RjfmGuidanceStatus, string> = {
  VALID: "成立",
  WARNING: "成立（注意）",
  HARD_INVALID: "不成立",
  UNAVAILABLE: "算出不可",
};

const turnDirectionLabels: Record<RjfmRunwayGuidance["turn_direction"], string> = {
  LEFT: "左",
  RIGHT: "右",
};

const centerRouteTurnMethodLabels: Record<RjfmRunwayGuidance["turn_method"], string> = {
  FIXED_BANK_20: "20°バンク",
  ADJUSTED_MAX_RADIUS: "最大半径へ調整",
  NONE: "旋回解なし",
};

export function rjfmCandidateClass(candidate: RjfmRunwayGuidance): string {
  if (candidate.status === "HARD_INVALID") return "is-invalid";
  if (candidate.status === "UNAVAILABLE") return "is-unavailable";
  if (candidate.status === "WARNING") return `is-rwy-${candidate.runway} is-warning`;
  return `is-rwy-${candidate.runway}`;
}

function formatRjfmTurns(candidate: RjfmRunwayGuidance): string {
  const partial = candidate.partial_turn_deg;
  if (candidate.full_turns === 0 && partial === null) return "旋回なし";
  const direction = `${turnDirectionLabels[candidate.turn_direction]}旋回`;
  if (candidate.full_turns === 0 && partial !== null) {
    return `${direction} ${partial.toFixed(1)}°`;
  }
  return partial === null
    ? `${direction} ${candidate.full_turns}周`
    : `${direction} ${candidate.full_turns}周 + ${partial.toFixed(1)}°`;
}

function formatMzePosition(candidate: RjfmRunwayGuidance): string {
  if (candidate.turn_entry_radial_deg === null || candidate.turn_entry_dme_nm === null) {
    return "—";
  }
  const radial = String(Math.round(candidate.turn_entry_radial_deg) % 360).padStart(3, "0");
  return `R${radial}° / ${candidate.turn_entry_dme_nm.toFixed(1)} DME`;
}

function formatRjfmTimeDelta(seconds: number | null): string {
  if (seconds === null) return "—";
  const halfMinuteValue = Math.round(Math.abs(seconds) / 30) * 0.5;
  if (halfMinuteValue === 0) return "±0.0 min";
  const value = halfMinuteValue.toFixed(1);
  return seconds > 0 ? `LOSS +${value} min` : `GAIN −${value} min`;
}

function formatRjfmResiduals(candidate: RjfmRunwayGuidance): string {
  const values = [
    candidate.position_residual_nm === null
      ? null
      : `位置 ${candidate.position_residual_nm.toFixed(3)} NM`,
    candidate.altitude_residual_ft === null
      ? null
      : `高度 ${Math.round(candidate.altitude_residual_ft)} ft`,
    candidate.tangent_residual_deg === null
      ? null
      : `接線 ${candidate.tangent_residual_deg.toFixed(1)}°`,
  ].filter((value): value is string => value !== null);
  return values.length ? values.join(" / ") : "—";
}

function rjfmSourceLabel(key: string): string {
  const normalized = key.toLowerCase();
  if (normalized.includes("training") || normalized.includes("procedure")) {
    return "訓練飛行実施要領";
  }
  if (normalized.includes("aip")) return "AIP RJFM";
  if (normalized.includes("pca")) return "宮崎空港PCA";
  if (normalized.includes("mze") || normalized.includes("navaid")) return "MZE資料";
  return key.replaceAll("_", " ");
}

export function RjfmGuidancePanel({ guidance }: { guidance: RjfmDepartureGuidance }) {
  const sourceDates = Object.entries(guidance.source_effective_dates)
    .sort(([left], [right]) => left.localeCompare(right));
  return (
    <section className="rjfm-guidance" aria-label="RJFM北方面出発ガイダンス">
      <div className="rjfm-guidance-heading">
        <div>
          <span className="rjfm-guidance-eyebrow">RJFM NORTHBOUND EXCEPTION</span>
          <h3>Newta CENTER Route 出発ガイダンス</h3>
        </div>
        <span className="rjfm-reference-revision">参照 {guidance.reference_revision}</span>
      </div>
      <p className="rjfm-guidance-intro">
        UMKを5,500 ft MSLで通過するPOH上昇時間を基準に、RWY09は左旋回、RWY27は右旋回の候補を表示しています。
      </p>
      <div className="rjfm-candidate-grid">
        {guidance.candidates.map((candidate) => {
          const passedConstraints = candidate.constraints.filter((item) => item.passed).length;
          const dmeWarning = candidate.turn_entry_dme_nm !== null
            && candidate.turn_entry_dme_nm < 4;
          const directionLabel = turnDirectionLabels[candidate.turn_direction];
          return (
            <article
              key={candidate.runway}
              className={`rjfm-candidate ${rjfmCandidateClass(candidate)}`}
              aria-label={`RWY ${candidate.runway} 候補 ${rjfmStatusLabels[candidate.status]}`}
            >
              <div className="rjfm-candidate-heading">
                <h4>RWY {candidate.runway}</h4>
                <span className="rjfm-status">{rjfmStatusLabels[candidate.status]}</span>
              </div>
              <dl className="rjfm-candidate-metrics">
                <div>
                  <dt>{directionLabel}旋回</dt>
                  <dd>{formatRjfmTurns(candidate)}</dd>
                </div>
                <div>
                  <dt>旋回モデル</dt>
                  <dd>
                    {candidate.turn_method === "FIXED_BANK_20"
                      ? `${directionLabel}${centerRouteTurnMethodLabels.FIXED_BANK_20}`
                      : centerRouteTurnMethodLabels[candidate.turn_method]}
                  </dd>
                </div>
                <div>
                  <dt>MZE位置</dt>
                  <dd>{formatMzePosition(candidate)}</dd>
                </div>
                <div>
                  <dt>旋回開始高度</dt>
                  <dd>
                    {candidate.turn_entry_altitude_ft_msl === null
                      ? "—"
                      : `${Math.round(candidate.turn_entry_altitude_ft_msl).toLocaleString("ja-JP")} ft MSL`}
                  </dd>
                </div>
                <div>
                  <dt>到達条件</dt>
                  <dd>UMK 5,500 ft MSL</dd>
                </div>
                <div>
                  <dt>直線Legとの差</dt>
                  <dd>{formatRjfmTimeDelta(candidate.expected_time_delta_seconds)}</dd>
                </div>
                <div>
                  <dt>全周旋回後ドリフト</dt>
                  <dd>
                    {candidate.exit_drift_nm === null
                      ? "—"
                      : `${candidate.exit_drift_nm.toFixed(2)} NM`}
                  </dd>
                </div>
                <div>
                  <dt>制約判定</dt>
                  <dd>
                    {candidate.constraints.length
                      ? `${passedConstraints}/${candidate.constraints.length} 適合`
                      : "判定なし"}
                  </dd>
                </div>
              </dl>
              <p className="rjfm-residuals">
                <strong>解の残差</strong>
                {formatRjfmResiduals(candidate)}
              </p>
              {dmeWarning && (
                <p className="rjfm-dme-warning">
                  MZE 4 DME未満です。これは非ブロッキング注意で、候補自体は表示を継続します。
                </p>
              )}
              {candidate.constraints.length > 0 && (
                <ul className="rjfm-constraint-list" aria-label={`RWY ${candidate.runway} 制約判定`}>
                  {candidate.constraints.map((constraint) => (
                    <li
                      key={constraint.code}
                      className={constraint.passed
                        ? "is-passed"
                        : constraint.hard ? "is-failed" : "is-advisory"}
                    >
                      <span>
                        {constraint.passed ? "適合" : constraint.hard ? "不適合" : "注意"}
                      </span>
                      <p>{constraint.message}</p>
                    </li>
                  ))}
                </ul>
              )}
              {candidate.notes.length > 0 && (
                <ul className="rjfm-candidate-notes">
                  {candidate.notes.map((note) => <li key={note}>{note}</li>)}
                </ul>
              )}
            </article>
          );
        })}
      </div>
      <div className="rjfm-provenance">
        <div>
          <strong>適用資料</strong>
          {sourceDates.length ? (
            <dl>
              {sourceDates.map(([key, value]) => (
                <div key={key}>
                  <dt>{rjfmSourceLabel(key)}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p>資料日付なし</p>
          )}
        </div>
        <div>
          <strong>制限事項</strong>
          {guidance.limitations.length ? (
            <ul>{guidance.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
          ) : (
            <p>追加の制限事項なし</p>
          )}
        </div>
      </div>
    </section>
  );
}
