import type { AdoptedValue, CalculationOutcome } from "../types";

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

function numberValue(value: AdoptedValue<number>): FormattedValue {
  const selected = adopted(value);
  return {
    text: selected === null ? "未取得" : String(selected),
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

function ValueCell({ value }: { value: AdoptedValue<number> }) {
  const formatted = numberValue(value);
  return (
    <td className={valueClass(formatted)}>
      {formatted.text}
      {formatted.manual && <small>手入力</small>}
    </td>
  );
}

function CombinedValueCell({
  first,
  second,
}: {
  first: AdoptedValue<number>;
  second: AdoptedValue<number>;
}) {
  const firstValue = numberValue(first);
  const secondValue = numberValue(second);
  const formatted: FormattedValue = {
    text: firstValue.text + " / " + secondValue.text,
    manual: firstValue.manual || secondValue.manual,
    unavailable: firstValue.unavailable || secondValue.unavailable,
  };
  return (
    <td className={valueClass(formatted)}>
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
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds - minutes * 60;
  return {
    text:
      minutes.toString().padStart(2, "0") +
      ":" +
      String(remainder).padStart(2, "0"),
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
      String(directionValue).padStart(3, "0") + "/" + String(speedValue),
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
    <td className={valueClass(formatted)}>
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
    <td className={valueClass(formatted)}>
      {formatted.text}
      {formatted.manual && <small>手入力</small>}
    </td>
  );
}

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
              <th>ZONE / CUM<br /><small>ETE</small></th>
              <th>ETO</th>
              <th>ATO</th>
              <th>ATE</th>
              <th>SECT / REM<br /><small>FUEL gal</small></th>
            </tr>
          </thead>
          <tbody>
            {outcome.sections.map((section) => {
              const variationValue = adopted(section.variation_deg_east);
              const variation: FormattedValue = {
                text:
                  variationValue === null
                    ? "未取得"
                    : (variationValue >= 0 ? "E " : "W ") +
                      String(Math.abs(variationValue)),
                manual: section.variation_deg_east.adopted_source === "MANUAL",
                unavailable: variationValue === null,
              };
              return (
                <tr key={section.section_id}>
                  <td className="route-name-cell">{section.from_name}</td>
                  <td className="route-name-cell">{section.to_name}</td>
                  <ValueCell value={section.pressure_altitude_planning_ft} />
                  <ValueCell value={section.temperature_c} />
                  <ValueCell value={section.cas_kt} />
                  <ValueCell value={section.tas_kt} />
                  <ValueCell value={section.true_course_deg} />
                  <td className={valueClass(variation)}>
                    {variation.text}
                    {variation.manual && <small>手入力</small>}
                  </td>
                  <ValueCell value={section.magnetic_course_deg} />
                  <WindCell
                    direction={section.wind_direction_deg_from}
                    speed={section.wind_speed_kt}
                  />
                  <ValueCell value={section.wca_deg} />
                  <ValueCell value={section.magnetic_heading_deg} />
                  <CombinedValueCell
                    first={section.zone_distance_nm}
                    second={section.cumulative_distance_nm}
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
                  />
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="fuel-summary">
        <div><span>搭載</span><strong>{String(fuel.total_usable_gal)} gal</strong></div>
        <div>
          <span>最低必要</span>
          <strong>
            {fuel.min_required_gal === null ? "未確定" : String(fuel.min_required_gal)} gal
          </strong>
        </div>
        <div><span>予備</span><strong>{String(fuel.reserve_gal)} gal</strong></div>
        <div><span>TGL</span><strong>{String(fuel.tgl_gal)} gal</strong></div>
        <div>
          <span>EXTRA</span>
          <strong>{fuel.extra_gal === null ? "未確定" : String(fuel.extra_gal)} gal</strong>
        </div>
      </div>
      <p className="nav-log-disclaimer">
        本表示は地上準備の転記補助です。運航の可否を決定する資料ではありません。
        最新の気象・NOTAM・AIPおよび適用可能な原資料を確認してください。
      </p>
    </section>
  );
}
