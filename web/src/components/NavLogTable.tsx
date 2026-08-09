import type { AdoptedValue, CalculationOutcome } from "../types";

function adopted<T>(value: AdoptedValue<T>): T | null {
  return value.adopted_source === "MANUAL"
    ? value.manual_override
    : value.automatic_value;
}

function numberValue(
  value: AdoptedValue<number>,
  digits = 0,
): { text: string; manual: boolean; unavailable: boolean } {
  const selected = adopted(value);
  return {
    text: selected === null ? "未取得" : selected.toFixed(digits),
    manual: value.adopted_source === "MANUAL",
    unavailable: selected === null,
  };
}

function ValueCell({
  value,
  digits = 0,
  suffix,
}: {
  value: AdoptedValue<number>;
  digits?: number;
  suffix?: string;
}) {
  const formatted = numberValue(value, digits);
  return (
    <td className={formatted.unavailable ? "unavailable-value" : formatted.manual ? "manual-value" : ""}>
      {formatted.text}{formatted.unavailable ? "" : suffix}
      {formatted.manual && <small>手入力</small>}
    </td>
  );
}

function ete(value: AdoptedValue<number>): string {
  const seconds = adopted(value);
  if (seconds === null) return "未取得";
  const rounded = Math.round(seconds);
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return `${minutes.toString().padStart(2, "0")}:${remainder
    .toString()
    .padStart(2, "0")}`;
}

function wind(
  direction: AdoptedValue<number>,
  speed: AdoptedValue<number>,
): string {
  const directionValue = adopted(direction);
  const speedValue = adopted(speed);
  if (speedValue === null) return "未取得";
  if (speedValue < 0.5 || directionValue === null) return "CALM";
  return `${Math.round(directionValue).toString().padStart(3, "0")}/${Math.round(
    speedValue,
  )}`;
}

const phaseLabels: Record<string, string> = {
  CLIMB: "上昇",
  CRUISE: "巡航",
  DESCENT: "降下",
  VISUAL_ARRIVAL: "場周進入",
};

export function NavLogTable({ outcome }: { outcome: CalculationOutcome }) {
  const fuel = outcome.fuel_plan;
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
        <table className="nav-log-table">
          <thead>
            <tr>
              <th>LEG</th>
              <th>PHASE</th>
              <th>TC°</th>
              <th>DIST<br />NM</th>
              <th>ALT<br />ft</th>
              <th>TAS<br />kt</th>
              <th>W/V</th>
              <th>MC°</th>
              <th>VAR</th>
              <th>MH°</th>
              <th>GS<br />kt</th>
              <th>ETE</th>
              <th>FUEL<br />gal</th>
              <th>REM<br />gal</th>
            </tr>
          </thead>
          <tbody>
            {outcome.sections.map((section) => {
              const variation = numberValue(section.variation_deg_east, 1);
              const mc = numberValue(section.magnetic_course_deg, 0);
              const mh = numberValue(section.magnetic_heading_deg, 0);
              const eteText = ete(section.zone_ete_seconds);
              return (
                <tr key={`${section.section_id}-${section.sequence}`}>
                  <td>
                    <span className="leg-sequence">{section.sequence + 1}</span>
                    <strong>{section.from_name} – {section.to_name}</strong>
                    {section.segment_label && <small>{section.segment_label}</small>}
                  </td>
                  <td>{phaseLabels[section.phase] ?? section.phase}</td>
                  <ValueCell value={section.true_course_deg} />
                  <ValueCell value={section.zone_distance_nm} digits={1} />
                  <ValueCell value={section.planned_altitude_ft_msl} />
                  <ValueCell value={section.tas_kt} />
                  <td
                    className={
                      section.wind_speed_kt.adopted_source === "MANUAL" ? "manual-value" : ""
                    }
                  >
                    {wind(section.wind_direction_deg_from, section.wind_speed_kt)}
                    {section.wind_speed_kt.adopted_source === "MANUAL" && <small>手入力</small>}
                  </td>
                  <td className={mc.unavailable ? "unavailable-value" : ""}>{mc.text}</td>
                  <td className={variation.unavailable ? "unavailable-value" : ""}>
                    {variation.unavailable
                      ? variation.text
                      : `${Number(variation.text) >= 0 ? "E" : "W"} ${Math.abs(
                          Number(variation.text),
                        ).toFixed(1)}`}
                  </td>
                  <td className={mh.unavailable ? "unavailable-value" : ""}>{mh.text}</td>
                  <ValueCell value={section.ground_speed_kt} />
                  <td className={eteText === "未取得" ? "unavailable-value" : ""}>
                    {eteText}
                  </td>
                  <ValueCell value={section.section_fuel_gal} digits={1} />
                  <ValueCell value={section.remaining_fuel_gal} digits={1} />
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="fuel-summary">
        <div><span>搭載</span><strong>{fuel.total_usable_gal.toFixed(1)} gal</strong></div>
        <div><span>最低必要</span><strong>{fuel.min_required_gal?.toFixed(1) ?? "未確定"} gal</strong></div>
        <div><span>予備</span><strong>{fuel.reserve_gal.toFixed(1)} gal</strong></div>
        <div><span>TGL</span><strong>{fuel.tgl_gal.toFixed(1)} gal</strong></div>
        <div><span>EXTRA</span><strong>{fuel.extra_gal?.toFixed(1) ?? "未確定"} gal</strong></div>
      </div>
      <p className="nav-log-disclaimer">
        本表示は地上準備の転記補助です。運航の可否を決定する資料ではありません。
        最新の気象・NOTAM・AIPおよび適用可能な原資料を確認してください。
      </p>
    </section>
  );
}
