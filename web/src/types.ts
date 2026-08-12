export type Severity = "BLOCKER" | "WARNING";
export type FlightPhase =
  | "CLIMB"
  | "CRUISE"
  | "DESCENT"
  | "VISUAL_ARRIVAL";

export interface RuntimeState {
  weatherLabel: string;
  developmentWeather: boolean;
  referenceDatasetId: string;
  referenceRevision: string;
  performanceRevision: string | null;
  performanceValidationStatus: string;
}

export interface AirportOption {
  id: string;
  icao: string;
  name: string;
  latitudeDeg: number;
  longitudeDeg: number;
  elevationFtMsl: number;
  patternAltitudeFtMsl: number;
  patternAltitudeValidationStatus: string;
  patternAltitudeSource: string;
  patternAltitudeSourceRevision: string;
}

export interface RouteCandidate {
  kind: "line" | "polygon" | "points";
  index: number;
  name: string;
  vertexCount: number;
  distanceNm: number | null;
  coordinates: [number, number][];
}

export interface ImportState {
  filename: string | null;
  warnings: string[];
  sourceFiles: string[];
  candidates: RouteCandidate[];
}

export interface RouteNode {
  id: string;
  sequence: number;
  name: string;
  latitude_deg: number;
  longitude_deg: number;
  role: string;
  source: string;
}

export interface NavSection {
  id: string;
  sequence: number;
  from_node_id: string;
  to_node_id: string;
  phase: FlightPhase;
  planned_altitude_ft_msl: number;
  manual_wind_direction_deg: number | null;
  manual_wind_speed_kt: number | null;
  manual_temperature_c: number | null;
  manual_tas_kt: number | null;
}

export interface ArrivalPlan {
  visual_reporting_point_node_id: string;
  selected_pattern_altitude_ft_msl: number | null;
  selected_pattern_altitude_source: "AUTOMATIC" | "MANUAL" | null;
  altitude_mode: "STANDARD_DISTANCE_RULE" | "MANUAL_NON_STANDARD_ENTRY";
  manual_vrep_altitude_ft_msl: number | null;
  manual_override_reason: string | null;
}

// Domain API payloads intentionally retain Python snake_case at this boundary.
// Components consume these validated fields directly; UI-only state uses camelCase.
export interface Project {
  id: string;
  name: string;
  revision: number;
  status: string;
  pilot_name: string;
  ship_identifier: string;
  flight_date: string;
  planned_departure_time_jst: string;
  departure_airport_id: string;
  destination_airport_id: string;
  total_usable_fuel_gal: number;
  default_variation_deg_east: number;
  manual_qnh_hpa: number | null;
  tgl_count: number;
  route_nodes: RouteNode[];
  sections: NavSection[];
  acknowledged_warning_codes: string[];
  metadata: {
    ui_state?: {
      arrival_plan?: ArrivalPlan | null;
    };
    [key: string]: unknown;
  };
}

export interface AdoptedValue<T> {
  automatic_value: T | null;
  automatic_metadata: Record<string, unknown>;
  manual_override: T | null;
  adopted_source: "AUTOMATIC" | "MANUAL" | null;
}

export interface SectionResult {
  section_id: string;
  sequence: number;
  phase: FlightPhase;
  segment_label: string | null;
  from_name: string;
  to_name: string;
  planned_altitude_ft_msl: AdoptedValue<number>;
  pressure_altitude_planning_ft: AdoptedValue<number>;
  true_course_deg: AdoptedValue<number>;
  variation_deg_east: AdoptedValue<number>;
  magnetic_course_deg: AdoptedValue<number>;
  wind_direction_deg_from: AdoptedValue<number>;
  wind_speed_kt: AdoptedValue<number>;
  wca_deg: AdoptedValue<number>;
  magnetic_heading_deg: AdoptedValue<number>;
  temperature_c: AdoptedValue<number>;
  cas_kt: AdoptedValue<number>;
  tas_kt: AdoptedValue<number>;
  ground_speed_kt: AdoptedValue<number>;
  zone_distance_nm: AdoptedValue<number>;
  cumulative_distance_nm: AdoptedValue<number>;
  zone_ete_seconds: AdoptedValue<number>;
  cumulative_ete_seconds: AdoptedValue<number>;
  section_fuel_gal: AdoptedValue<number>;
  remaining_fuel_gal: AdoptedValue<number>;
}

export interface DerivedPoint {
  type: "RCA" | "EOC";
  latitude_deg: number;
  longitude_deg: number;
  along_route_distance_nm: number;
}

export interface FuelPlan {
  total_usable_gal: number;
  taxi_runup_gal: number;
  climb_gal: number | null;
  cruise_gal: number | null;
  descent_gal: number | null;
  additional_gal: number;
  tgl_gal: number;
  reserve_gal: number;
  min_required_gal: number | null;
  extra_gal: number | null;
  extra_endurance_seconds: number | null;
}

export interface CalculationOutcome {
  selected_forecast_run_id: string | null;
  sections: SectionResult[];
  derived_points: DerivedPoint[];
  fuel_plan: FuelPlan;
  status: string;
}

export interface SectionAltitudeGuidance {
  sectionId: string;
  magneticCourseDeg: number;
  variationDegEast: number;
  candidateAltitudesFtMsl: number[];
  appliesToCruise: boolean;
  requiresReview: boolean;
}

export interface AltitudeGuidance {
  legalThresholdNote: string;
  terrainLimitationNote: string;
  sections: SectionAltitudeGuidance[];
}

export interface EffectiveIssue {
  code: string;
  severity: Severity;
  message: string;
  sectionId: string | null;
  segmentSequence: number | null;
  acknowledgementRequired: boolean;
  ackKey: string;
  acknowledged: boolean;
  action: string;
}

export interface ReadinessState {
  status: string | null;
  calculationIsCurrent: boolean;
  transferAidAllowed: boolean;
  workflowStep: number;
  nextAction: string;
  issues: EffectiveIssue[];
}

export interface SavedProject {
  id: string;
  name: string;
  status: string;
  revision: number;
  updatedAt: string;
}

export interface WebState {
  runtime: RuntimeState;
  airports: AirportOption[];
  savedProjects: SavedProject[];
  import: ImportState;
  altitudeGuidance: AltitudeGuidance;
  project: Project | null;
  outcome: CalculationOutcome | null;
  readiness: ReadinessState;
}

export interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    candidates?: string[];
  };
}
