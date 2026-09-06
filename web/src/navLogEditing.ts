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
  tasByPhase: Partial<Record<FlightPhase, string>>;
  windDirectionByPhase: Partial<Record<FlightPhase, string>>;
  windSpeedByPhase: Partial<Record<FlightPhase, string>>;
}

export type NavLogEditDrafts = Record<string, NavLogEditDraft>;
export type NavLogEditErrors = Record<
  string,
  Partial<Record<NavLogEditableField, string>>
>;

export function draftFromSection(section: NavSection): NavLogEditDraft {
  const temperatureByPhase: Partial<Record<FlightPhase, string>> = {};
  const tasByPhase: Partial<Record<FlightPhase, string>> = {};
  const windDirectionByPhase: Partial<Record<FlightPhase, string>> = {};
  const windSpeedByPhase: Partial<Record<FlightPhase, string>> = {};
  for (const [phase, value] of Object.entries(section.manual_temperature_c_by_phase ?? {})) {
    temperatureByPhase[phase as FlightPhase] = String(value);
  }
  temperatureByPhase[section.phase] = section.manual_temperature_c?.toString() ?? "";
  for (const [phase, value] of Object.entries(section.manual_tas_kt_by_phase ?? {})) {
    tasByPhase[phase as FlightPhase] = String(value);
  }
  if (tasByPhase[section.phase] === undefined) {
    tasByPhase[section.phase] = section.manual_tas_kt?.toString() ?? "";
  }
  for (const [phase, wind] of Object.entries(section.manual_wind_by_phase ?? {})) {
    windDirectionByPhase[phase as FlightPhase] = String(
      wind.direction_deg_from === 0 ? 360 : wind.direction_deg_from,
    );
    windSpeedByPhase[phase as FlightPhase] = String(wind.speed_kt);
  }
  if (windDirectionByPhase[section.phase] === undefined) {
    windDirectionByPhase[section.phase] =
      section.manual_wind_direction_deg === 0
        ? "360"
        : section.manual_wind_direction_deg?.toString() ?? "";
  }
  if (windSpeedByPhase[section.phase] === undefined) {
    windSpeedByPhase[section.phase] = section.manual_wind_speed_kt?.toString() ?? "";
  }
  return {
    plannedAltitude: String(section.planned_altitude_ft_msl),
    temperatureByPhase,
    tasByPhase,
    windDirectionByPhase,
    windSpeedByPhase,
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
      for (const tasDraft of Object.values(draft.tasByPhase)) {
        const tas = finiteNumber(tasDraft ?? "");
        if (tasDraft?.trim() && (tas === null || tas <= 0 || tas > 300)) {
          sectionErrors.tas = "0より大きく300 kt以下にしてください。";
          break;
        }
      }

      const windPhases = new Set<FlightPhase>([
        ...(Object.keys(draft.windDirectionByPhase) as FlightPhase[]),
        ...(Object.keys(draft.windSpeedByPhase) as FlightPhase[]),
      ]);
      for (const phase of windPhases) {
        const directionDraft = draft.windDirectionByPhase[phase] ?? "";
        const speedDraft = draft.windSpeedByPhase[phase] ?? "";
        const hasDirection = Boolean(directionDraft.trim());
        const hasSpeed = Boolean(speedDraft.trim());
        if (hasDirection !== hasSpeed) {
          const message = "風向と風速は両方入力するか、両方空欄にしてください。";
          sectionErrors.windDirection = message;
          sectionErrors.windSpeed = message;
        } else if (hasDirection && hasSpeed) {
          const direction = finiteNumber(directionDraft);
          const speed = finiteNumber(speedDraft);
          if (
            direction === null ||
            !Number.isInteger(direction) ||
            direction < 1 ||
            direction > 360
          ) {
            sectionErrors.windDirection = "001～360の整数にしてください。";
          }
          if (speed === null || speed < 0 || speed > 200) {
            sectionErrors.windSpeed = "0～200 ktの範囲にしてください。";
          }
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
  const manualTasByPhase = Object.fromEntries(
    Object.entries(draft.tasByPhase)
      .flatMap(([phase, value]) => {
        const parsed = finiteNumber(value ?? "");
        return parsed === null ? [] : [[phase, parsed]];
      }),
  ) as Partial<Record<FlightPhase, number>>;
  const windPhases = new Set<FlightPhase>([
    ...(Object.keys(draft.windDirectionByPhase) as FlightPhase[]),
    ...(Object.keys(draft.windSpeedByPhase) as FlightPhase[]),
  ]);
  const manualWindByPhase = Object.fromEntries(
    [...windPhases]
      .filter((phase) => phase !== section.phase)
      .flatMap((phase) => {
        const direction = finiteNumber(draft.windDirectionByPhase[phase] ?? "");
        const speed = finiteNumber(draft.windSpeedByPhase[phase] ?? "");
        return direction === null || speed === null
          ? []
          : [[phase, { direction_deg_from: direction, speed_kt: speed }]];
      }),
  ) as NonNullable<NavSection["manual_wind_by_phase"]>;
  return {
    ...section,
    planned_altitude_ft_msl: isVisualArrival
      ? section.planned_altitude_ft_msl
      : Number(draft.plannedAltitude),
    manual_temperature_c: finiteNumber(draft.temperatureByPhase[section.phase] ?? ""),
    manual_temperature_c_by_phase: manualTemperatureByPhase,
    manual_tas_kt_by_phase: isVisualArrival
      ? section.manual_tas_kt_by_phase
      : manualTasByPhase,
    // New updates never write the scalar compatibility field.
    manual_tas_kt: isVisualArrival ? section.manual_tas_kt : null,
    manual_wind_by_phase: isVisualArrival
      ? section.manual_wind_by_phase
      : manualWindByPhase,
    manual_wind_direction_deg: isVisualArrival
      ? section.manual_wind_direction_deg
      : finiteNumber(draft.windDirectionByPhase[section.phase] ?? ""),
    manual_wind_speed_kt: isVisualArrival
      ? section.manual_wind_speed_kt
      : finiteNumber(draft.windSpeedByPhase[section.phase] ?? ""),
  };
}

export function hasNavLogEditErrors(errors: NavLogEditErrors): boolean {
  return Object.keys(errors).length > 0;
}
