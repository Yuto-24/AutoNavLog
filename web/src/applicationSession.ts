import type { PlanningForm } from "./forms";
import { initialPlanningForm } from "./forms";
import type { NavLogEditDrafts } from "./navLogEditing";
import type { Project, WorkingRecovery, NavSection, ArrivalPlan } from "./types";

export const SESSION_KEY = "autonavlog.working-session.v1";
export const RECOVERY_FAILED = "前回の作業を復元できなかったため、新規作業を開始しました。";
export interface PendingKmz { filename: string; contentBase64: string; candidates: string[] }
export interface CheckPointDraft {
  id: string | null; name: string; latitude: string; longitude: string; linkedSectionId: string;
}
export const emptyCheckPointDraft = (): CheckPointDraft => ({
  id: null, name: "", latitude: "", longitude: "", linkedSectionId: "",
});
export interface NodeNameDraft { id: string | null; name: string }
interface ProjectDraft {
  id: string;
  sections: Pick<NavSection, "id" | "phase" | "planned_altitude_ft_msl">[];
  arrival: Pick<ArrivalPlan, "altitude_mode" | "manual_vrep_altitude_ft_msl" | "manual_override_reason"> | null;
}
export function projectDraft(project: Project | null): ProjectDraft | null {
  if (!project) return null;
  const arrival = project.metadata.ui_state?.arrival_plan;
  return {
    id: project.id,
    sections: project.sections.map(({ id, phase, planned_altitude_ft_msl }) => ({ id, phase, planned_altitude_ft_msl })),
    arrival: arrival ? {
      altitude_mode: arrival.altitude_mode,
      manual_vrep_altitude_ft_msl: arrival.manual_vrep_altitude_ft_msl,
      manual_override_reason: arrival.manual_override_reason,
    } : null,
  };
}
export function restoreProjectDraft(project: Project, draft: ProjectDraft): Project {
  const sections = new Map(draft.sections.map(section => [section.id, section]));
  const arrival = project.metadata.ui_state?.arrival_plan;
  return { ...project,
    sections: project.sections.map(section => ({ ...section, ...sections.get(section.id) })),
    metadata: { ...project.metadata, ui_state: {
      ...project.metadata.ui_state,
      ...(arrival && draft.arrival ? { arrival_plan: { ...arrival, ...draft.arrival } } : {}),
    } },
  };
}
export interface VorColumn { id: number; stationIdentifier: string | null }
export interface ApplicationSession {
  vorColumns: VorColumn[];
  checkPointDraft: CheckPointDraft;
  nodeNameDraft: NodeNameDraft;
  version: 1;
  working: WorkingRecovery;
  form: PlanningForm;
  altitudeInputs: Record<string, string>;
  navLogDrafts: NavLogEditDrafts;
  projectDraft: ProjectDraft | null;
  calculationInputsAreLocallyCurrent: boolean;
  pastedKml: string;
  projectName: string;
  selectedProjectId: string;
  pendingKmz: PendingKmz | null;
  selectedKmzDocument: string;
}

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const strings = (value: unknown) => record(value) && Object.values(value).every(v => typeof v === "string");

// Validate representation, not input validity: empty, incomplete and out-of-range text is work.
export function decodeSession(raw: string): ApplicationSession {
  const value: unknown = JSON.parse(raw);
  if (!record(value) || value.version !== 1 || !record(value.working) ||
      value.working.version !== 1 || !record(value.form) ||
      !Object.entries(initialPlanningForm()).every(([key, seed]) => typeof (value.form as Record<string, unknown>)[key] === typeof seed) ||
      !strings(value.altitudeInputs) || !record(value.navLogDrafts) ||
      !Object.values(value.navLogDrafts).every(draft => record(draft) &&
        typeof draft.plannedAltitude === "string" &&
        ["temperatureByPhase", "tasByPhase", "windDirectionByPhase", "windSpeedByPhase"].every(key => strings(draft[key]))) ||
      !Array.isArray(value.vorColumns) || !value.vorColumns.every(column => record(column) &&
        typeof column.id === "number" && (column.stationIdentifier === null || typeof column.stationIdentifier === "string")) ||
      !record(value.checkPointDraft) ||
      !(value.checkPointDraft.id === null || typeof value.checkPointDraft.id === "string") ||
      !["name", "latitude", "longitude", "linkedSectionId"].every(key => typeof (value.checkPointDraft as Record<string, unknown>)[key] === "string") ||
      !record(value.nodeNameDraft) || typeof value.nodeNameDraft.name !== "string" ||
      !(value.nodeNameDraft.id === null || typeof value.nodeNameDraft.id === "string") ||
      typeof value.calculationInputsAreLocallyCurrent !== "boolean" ||
      !["pastedKml", "projectName", "selectedProjectId", "selectedKmzDocument"].every(key => typeof value[key] === "string") ||
      !(value.pendingKmz === null || (record(value.pendingKmz) &&
        typeof value.pendingKmz.filename === "string" && typeof value.pendingKmz.contentBase64 === "string" &&
        Array.isArray(value.pendingKmz.candidates) && value.pendingKmz.candidates.every(v => typeof v === "string"))) ||
      !(value.projectDraft === null || (record(value.projectDraft) &&
        record(value.working.project) && value.projectDraft.id === value.working.project.id &&
        (value.projectDraft.arrival === null || (record(value.projectDraft.arrival) &&
          ["STANDARD_DISTANCE_RULE", "MANUAL_NON_STANDARD_ENTRY"].includes(String(value.projectDraft.arrival.altitude_mode)) &&
          (value.projectDraft.arrival.manual_vrep_altitude_ft_msl === null || typeof value.projectDraft.arrival.manual_vrep_altitude_ft_msl === "number") &&
          (value.projectDraft.arrival.manual_override_reason === null || typeof value.projectDraft.arrival.manual_override_reason === "string"))) &&
        Array.isArray(value.projectDraft.sections) && value.projectDraft.sections.every(section =>
          record(section) && typeof section.id === "string" && typeof section.planned_altitude_ft_msl === "number" &&
          ["CLIMB", "CRUISE", "DESCENT", "VISUAL_ARRIVAL"].includes(String(section.phase)))))) {
    throw new Error("Incompatible working session");
  }
  return value as unknown as ApplicationSession;
}

export function readSession(): { session?: ApplicationSession; failed?: boolean } {
  try {
    // sessionStorage may be cloned by window.open/duplicate. Only reload may adopt it.
    const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    if (navigation?.type !== "reload") {
      sessionStorage.removeItem(SESSION_KEY);
      return {};
    }
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw === null ? {} : { session: decodeSession(raw) };
  } catch {
    try { sessionStorage.removeItem(SESSION_KEY); } catch { /* storage unavailable */ }
    return { failed: true };
  }
}
export function writeSession(session: ApplicationSession): boolean {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(session)); return true; }
  catch { return false; }
}
export function clearSession(): void {
  sessionStorage.removeItem(SESSION_KEY);
}
