import type { ArrivalPlan, CheckPointInput, NavSection, Project, WebState, WorkingRecovery } from "./types";

// Transitional snapshot only. Application Session/lifecycle is designed in #119.
export type ApplicationSnapshot = WebState;
export type PlanningInput = Pick<Project,
  "flight_date" | "total_usable_fuel_gal" | "default_variation_deg_east"
> & Partial<Pick<Project,
  "pilot_name" | "ship_identifier" | "weather_mode" | "ftd_weather" |
  "run_up_included" | "nose_fairing_enabled" | "air_conditioning_enabled" |
  "descent_rate_fpm" | "tgl_count"
>> & { departure_time_jst: string; defaults_confirmed?: boolean };
export type ConfirmRouteInput = PlanningInput & {
  candidate_kind: "line" | "connected_lines" | "polygon" | "points";
  candidate_index?: number;
  point_indices?: number[];
  route_use_confirmed: boolean;
  polygon_route_confirmed?: boolean;
  all_leg_altitude_ft_msl?: number;
  use_penultimate_as_vrep?: boolean;
};
export type UpdateProjectInput = PlanningInput & {
  sections?: (Omit<NavSection, "id" | "sequence" | "from_node_id" | "to_node_id"> & { section_id: string })[];
  visual_reporting_point_node_id?: string | null;
  selected_pattern_altitude_ft_msl?: number | null;
  arrival_altitude_mode?: ArrivalPlan["altitude_mode"];
  manual_vrep_altitude_ft_msl?: number | null;
  manual_vrep_reason?: string | null;
};
export type ImportRouteInput = { filename: string; kmz_kml_filename?: string | null } & (
  { kml_text: string; content_base64?: never } | { content_base64: string; kml_text?: never }
);
export interface ApplicationErrorDetails {
  candidates?: string[];
  issues?: { location: (string | number)[]; message: string; type: string }[];
  [key: string]: unknown;
}
export class ApplicationError extends Error {
  constructor(message: string, readonly code: string, readonly details: ApplicationErrorDetails = {}) {
    super(message);
    this.name = "ApplicationError";
  }
}
export interface CalculationProgress { percent: number; message: string }
export type ProgressListener = (progress: CalculationProgress) => void;

// Operation boundaries intentionally preserve draft commits and atomic update/recalculate.
export interface AutoNavLogApplication {
  bootstrap(recovery?: WorkingRecovery): Promise<ApplicationSnapshot>;
  importRoute(input: ImportRouteInput): Promise<ApplicationSnapshot>;
  confirmRoute(input: ConfirmRouteInput): Promise<ApplicationSnapshot>;
  updateProject(input: UpdateProjectInput): Promise<ApplicationSnapshot>;
  updateAndRecalculate(input: UpdateProjectInput): Promise<ApplicationSnapshot>;
  renameRouteNode(nodeId: string, name: string): Promise<ApplicationSnapshot>;
  replaceCheckPoints(checkPoints: CheckPointInput[]): Promise<ApplicationSnapshot>;
  acknowledge(key: string, checked: boolean): Promise<ApplicationSnapshot>;
  calculate(onProgress?: ProgressListener): Promise<ApplicationSnapshot>;
  saveProject(name: string): Promise<ApplicationSnapshot>;
  loadProject(projectId: string): Promise<ApplicationSnapshot>;
  deleteProject(projectId: string): Promise<ApplicationSnapshot>;
  newWork(): Promise<void>;
}
