import type { FlightPhase, NavSection } from "./types";

export type NavLogEditableField =
  | "plannedAltitude"
  | "temperature"
  | "tas"
  | "windDirection"
  | "windSpeed";

export interface NavLogEditDraft {
  plannedAltitude: string;
  temperatureByPhase: Partial<Record<FlightPhase, string>>;
  tas: string;
  windDirection: string;
  windSpeed: string;
}

export type NavLogEditDrafts = Record<string, NavLogEditDraft>;
export type NavLogEditErrors = Record<
  string,
  Partial<Record<NavLogEditableField, string>>
>;

export function draftFromSection(section: NavSection): NavLogEditDraft {
  const temperatureByPhase: Partial<Record<FlightPhase, string>> = {};
  for (const [phase, value] of Object.entries(section.manual_temperature_c_by_phase ?? {})) {
    temperatureByPhase[phase as FlightPhase] = String(value);
  }
  temperatureByPhase[section.phase] = section.manual_temperature_c?.toString() ?? "";
  return {
    plannedAltitude: String(section.planned_altitude_ft_msl),
    temperatureByPhase,
    tas: section.manual_tas_kt?.toString() ?? "",
    windDirection: section.manual_wind_direction_deg?.toString() ?? "",
    windSpeed: section.manual_wind_speed_kt?.toString() ?? "",
  };
}

function finiteNumber(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function validateNavLogDrafts(
  sections: NavSection[],
  drafts: NavLogEditDrafts,
): NavLogEditErrors {
  const errors: NavLogEditErrors = {};
  for (const section of sections) {
    const draft = drafts[section.id];
    if (!draft) continue;
    const sectionErrors: Partial<Record<NavLogEditableField, string>> = {};
    const isVisualArrival = section.phase === "VISUAL_ARRIVAL";

    if (!isVisualArrival) {
      const altitude = finiteNumber(draft.plannedAltitude);
      if (
        altitude === null ||
        altitude < 100 ||
        altitude > 25_000 ||
        altitude % 100 !== 0
      ) {
        sectionErrors.plannedAltitude = "100～25,000 ftの範囲で100 ft単位にしてください。";
      }
    }

    for (const temperatureDraft of Object.values(draft.temperatureByPhase)) {
      if (!temperatureDraft?.trim()) continue;
      const temperature = finiteNumber(temperatureDraft);
      if (temperature === null || temperature < -80 || temperature > 60) {
        sectionErrors.temperature = "-80～60 °Cの範囲にしてください。";
        break;
      }
    }

    if (!isVisualArrival) {
      const tas = finiteNumber(draft.tas);
      if (draft.tas.trim() && (tas === null || tas <= 0 || tas > 300)) {
        sectionErrors.tas = "0より大きく300 kt以下にしてください。";
      }

      const hasDirection = Boolean(draft.windDirection.trim());
      const hasSpeed = Boolean(draft.windSpeed.trim());
      if (hasDirection !== hasSpeed) {
        const message = "風向と風速は両方入力するか、両方空欄にしてください。";
        sectionErrors.windDirection = message;
        sectionErrors.windSpeed = message;
      } else if (hasDirection && hasSpeed) {
        const direction = finiteNumber(draft.windDirection);
        const speed = finiteNumber(draft.windSpeed);
        if (direction === null || direction < 0 || direction >= 360) {
          sectionErrors.windDirection = "0以上360未満の度数にしてください。";
        }
        if (speed === null || speed < 0 || speed > 200) {
          sectionErrors.windSpeed = "0～200 ktの範囲にしてください。";
        }
      }
    }

    if (Object.keys(sectionErrors).length) errors[section.id] = sectionErrors;
  }
  return errors;
}

export function applyDraftToSection(
  section: NavSection,
  draft: NavLogEditDraft | undefined,
): NavSection {
  if (!draft) return section;
  const isVisualArrival = section.phase === "VISUAL_ARRIVAL";
  const manualTemperatureByPhase = Object.fromEntries(
    Object.entries(draft.temperatureByPhase)
      .filter(([phase]) => phase !== section.phase)
      .flatMap(([phase, value]) => {
        const parsed = finiteNumber(value ?? "");
        return parsed === null ? [] : [[phase, parsed]];
      }),
  ) as Partial<Record<FlightPhase, number>>;
  return {
    ...section,
    planned_altitude_ft_msl: isVisualArrival
      ? section.planned_altitude_ft_msl
      : Number(draft.plannedAltitude),
    manual_temperature_c: finiteNumber(draft.temperatureByPhase[section.phase] ?? ""),
    manual_temperature_c_by_phase: manualTemperatureByPhase,
    manual_tas_kt: isVisualArrival ? section.manual_tas_kt : finiteNumber(draft.tas),
    manual_wind_direction_deg: isVisualArrival
      ? section.manual_wind_direction_deg
      : finiteNumber(draft.windDirection),
    manual_wind_speed_kt: isVisualArrival
      ? section.manual_wind_speed_kt
      : finiteNumber(draft.windSpeed),
  };
}

export function hasNavLogEditErrors(errors: NavLogEditErrors): boolean {
  return Object.keys(errors).length > 0;
}
