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

export function rjfmCandidateClass(candidate: RjfmRunwayGuidance): string {
  if (candidate.status === "HARD_INVALID") return "is-invalid";
  if (candidate.status === "UNAVAILABLE") return "is-unavailable";
  if (candidate.status === "WARNING") return `is-rwy-${candidate.runway} is-warning`;
  return `is-rwy-${candidate.runway}`;
}

function northUpHorizontalPosition(runway: RjfmRunwayGuidance["runway"]): number {
  const runwayNumber = Number.parseInt(runway.slice(0, 2), 10);
  if (!Number.isFinite(runwayNumber)) return 0;
  const departureHeadingDeg = (runwayNumber * 10) % 360;
  return Math.sin(departureHeadingDeg * Math.PI / 180);
}

export function compareRjfmCandidatesForNorthUp(
  left: RjfmRunwayGuidance,
  right: RjfmRunwayGuidance,
): number {
  const horizontalDifference =
    northUpHorizontalPosition(left.runway) - northUpHorizontalPosition(right.runway);
  return Math.abs(horizontalDifference) > 1e-9
    ? horizontalDifference
    : left.runway.localeCompare(right.runway);
}

function formatRjfmTimeDelta(seconds: number | null): string {
  if (seconds === null) return "—";
  const halfMinuteValue = Math.round(Math.abs(seconds) / 30) * 0.5;
  if (halfMinuteValue === 0) return "±0.0 min";
  const value = halfMinuteValue.toFixed(1);
  return seconds > 0 ? `LOSS +${value} min` : `GAIN −${value} min`;
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
  const orderedCandidates = [...guidance.candidates].sort(compareRjfmCandidatesForNorthUp);
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
        UMKを5,500 ft MSLで通過するPOH上昇時間を基準にしたRWY別候補です。
        カードはNorth Up上の滑走路出発方位に合わせ、左から右へ配置しています。
      </p>
      <div className="rjfm-guidance-explainer">
        <p>
          <strong>旋回モデル</strong>
          固定20°バンクを基本とし、必要時は最大半径調整モデルを想定します。
        </p>
        <p>
          <strong>NAV LOG直線Legとの差</strong>
          候補経路のUMK到達時間から、NAV LOG主経路のRJFM→UMK/RCA直線距離を
          CLIMB GSで飛行した基準時間を差し引いた値です。LOSSは基準より長く、
          GAINは短いことを示します。
        </p>
      </div>
      <div className="rjfm-candidate-grid">
        {orderedCandidates.map((candidate) => {
          const dmeWarning = candidate.turn_entry_dme_nm !== null
            && candidate.turn_entry_dme_nm < 4;
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
                  <dt>旋回開始高度</dt>
                  <dd>
                    {candidate.turn_entry_altitude_ft_msl === null
                      ? "—"
                      : `${Math.round(candidate.turn_entry_altitude_ft_msl).toLocaleString("ja-JP")} ft MSL`}
                  </dd>
                </div>
                <div>
                  <dt>NAV LOG直線Legとの差</dt>
                  <dd>{formatRjfmTimeDelta(candidate.expected_time_delta_seconds)}</dd>
                </div>
              </dl>
              {dmeWarning && (
                <p className="rjfm-dme-warning">
                  宮崎VORTAC（MZE）から4 DME未満で旋回開始します。
                  これは非ブロッキング注意で、候補自体は表示を継続します。
                </p>
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
