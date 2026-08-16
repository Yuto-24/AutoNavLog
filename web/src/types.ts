export type Severity = "BLOCKER" | "WARNING";
export type FlightPhase =
  | "CLIMB"
  | "CRUISE"
  | "DESCENT"
  | "VISUAL_ARRIVAL";

export interface RuntimeState {
  appVersion: string;
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
  kind: "line" | "polygon" | "points" | "connected_lines";
  index: number;
  name: string;
  vertexCount: number;
  distanceNm: number | null;
  coordinates: [number, number][];
  containerPath?: string[];
  segmentNames?: string[];
  segmentCount?: number;
  legCount?: number;
  maxJoinGapNm?: number;
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

export interface ManualWind {
  direction_deg_from: number;
  speed_kt: number;
}

export interface FtdWeatherSettings {
  surface_wind: ManualWind;
  wind_at_5000_ft: ManualWind;
}

export interface VisualReference {
  id: string;
  project_id: string | null;
  name: string;
  latitude_deg: number;
  longitude_deg: number;
  role: string;
  linked_section_id: string | null;
  along_track_fraction: number | null;
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
  manual_wind_by_phase?: Partial<Record<FlightPhase, ManualWind>>;
  manual_temperature_c: number | null;
  manual_temperature_c_by_phase?: Partial<Record<FlightPhase, number>>;
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
  weather_mode: "FORECAST" | "FTD";
  ftd_weather: FtdWeatherSettings | null;
  tgl_count: number;
  route_nodes: RouteNode[];
  visual_references: VisualReference[];
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
  automatic_status?: string;
  manual_override: T | null;
  adopted_source: "AUTOMATIC" | "MANUAL" | null;
  warnings?: string[];
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

export type DisplayCellState =
  | "DISPLAY_VALUE"
  | "INHERIT"
  | "BLANK"
  | "UNAVAILABLE"
  | "STATE_SYMBOL";

export interface NavLogDisplayCell {
  state: DisplayCellState;
  text: string | null;
  effective_value: number | string | null;
  reason_code: string | null;
  manual: boolean;
}

export interface NavLogDisplayRow {
  section_id: string | null;
  sequence: number;
  source_result_sequence: number | null;
  phase: FlightPhase | null;
  row_type:
    | "PHYSICAL_LEG_SUMMARY"
    | "CALCULATION_ZONE"
    | "DESTINATION_INFO"
    | "LEG_SEPARATOR";
  counts_toward_totals: boolean;
  from_name: string;
  to_name: string;
  pa_display_kind:
    | "NUMERIC"
    | "CLIMB"
    | "DESCENT"
    | "ESTIMATED"
    | "BLANK"
    | "UNAVAILABLE";
  pa: NavLogDisplayCell;
  toat: NavLogDisplayCell;
  cas: NavLogDisplayCell;
  tas: NavLogDisplayCell;
  tc: NavLogDisplayCell;
  variation: NavLogDisplayCell;
  mc: NavLogDisplayCell;
  wind: NavLogDisplayCell;
  wca: NavLogDisplayCell;
  mh: NavLogDisplayCell;
  distance: NavLogDisplayCell;
  gs: NavLogDisplayCell;
  ete: NavLogDisplayCell;
  eto: NavLogDisplayCell;
  ato: NavLogDisplayCell;
  ate: NavLogDisplayCell;
  fuel: NavLogDisplayCell;
  zone_distance_nm_exact: number | null;
  cumulative_distance_nm_exact: number | null;
  zone_ete_seconds_exact: number | null;
  cumulative_ete_seconds_exact: number | null;
}

export interface DerivedPoint {
  type: "RCA" | "EOC";
  latitude_deg: number;
  longitude_deg: number;
  along_route_distance_nm: number;
}

export interface CheckPointProjection {
  checkpoint_id: string;
  section_id: string;
  abeam_latitude_deg: number;
  abeam_longitude_deg: number;
  along_track_fraction: number;
  along_section_distance_nm: number;
  cumulative_distance_nm: number;
  cross_track_distance_nm: number;
  policy_version: string;
}

export interface CheckPointPlanningIssue {
  code: string;
  message: string;
  sectionId: string | null;
  checkPointId: string | null;
}

export interface CheckPointPlanning {
  projections: CheckPointProjection[];
  issues: CheckPointPlanningIssue[];
}

export interface CheckPointInput {
  id?: string | null;
  name: string;
  latitude_deg: number;
  longitude_deg: number;
  linked_section_id: string;
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
  qnh_hpa: AdoptedValue<number>;
  sections: SectionResult[];
  display_rows: NavLogDisplayRow[];
  derived_points: DerivedPoint[];
  check_point_projections: CheckPointProjection[];
  fuel_plan: FuelPlan;
  status: string;
}

export interface DestinationWindForecast {
  airport_icao: string;
  valid_time_utc: string | null;
  availability: "AVAILABLE" | "UNAVAILABLE";
  wind_direction_deg_from: number | null;
  wind_speed_kt: number | null;
  wind_gust_kt: number | null;
  variable_direction: boolean;
  source_label: string;
  issue_time_utc: string | null;
  taf_valid_from_utc: string | null;
  taf_valid_to_utc: string | null;
  forecast_change: string | null;
  raw_taf: string | null;
  reason_code: string | null;
}

export interface SectionAltitudeGuidance {
  sectionId: string;
  magneticCourseDeg: number;
  variationDegEast: number;
  candidateAltitudesFtMsl: number[];
  appliesToCruise: boolean;
  appliesToCruisingAltitudeInput: boolean;
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
  checkPointPlanning: CheckPointPlanning;
  project: Project | null;
  outcome: CalculationOutcome | null;
  destinationWind: DestinationWindForecast | null;
  readiness: ReadinessState;
}

export interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    candidates?: string[];
  };
}
