import type { AirportOption, Project, RouteCandidate } from "./types";

export interface PlanningForm {
  flightDate: string;
  departureTimeJst: string;
  departureAirportId: string;
  destinationAirportId: string;
  pilotName: string;
  shipIdentifier: string;
  totalUsableFuelGal: number;
  variationDegEast: number;
  manualQnhHpa: string;
  tglCount: number;
  allLegAltitudeFtMsl: number;
  candidateKey: string;
  routeUseConfirmed: boolean;
  polygonRouteConfirmed: boolean;
  defaultsConfirmed: boolean;
  manualQnhConfirmed: boolean;
  usePenultimateAsVrep: boolean;
}

function tomorrowIso(): string {
  const date = new Date();
  date.setDate(date.getDate() + 1);
  return [
    date.getFullYear().toString().padStart(4, "0"),
    (date.getMonth() + 1).toString().padStart(2, "0"),
    date.getDate().toString().padStart(2, "0"),
  ].join("-");
}

export function initialPlanningForm(airports: AirportOption[] = []): PlanningForm {
  return {
    flightDate: tomorrowIso(),
    departureTimeJst: "09:00",
    departureAirportId: airports[0]?.id ?? "",
    destinationAirportId: airports[1]?.id ?? airports[0]?.id ?? "",
    pilotName: "",
    shipIdentifier: "",
    totalUsableFuelGal: 81,
    variationDegEast: 8,
    manualQnhHpa: "",
    tglCount: 0,
    allLegAltitudeFtMsl: 3000,
    candidateKey: "",
    routeUseConfirmed: false,
    polygonRouteConfirmed: false,
    defaultsConfirmed: false,
    manualQnhConfirmed: false,
    usePenultimateAsVrep: true,
  };
}

export function formFromProject(
  project: Project,
  previous: PlanningForm,
): PlanningForm {
  return {
    ...previous,
    flightDate: project.flight_date,
    departureTimeJst: project.planned_departure_time_jst.slice(11, 16),
    departureAirportId: project.departure_airport_id,
    destinationAirportId: project.destination_airport_id,
    pilotName: project.pilot_name,
    shipIdentifier: project.ship_identifier,
    totalUsableFuelGal: project.total_usable_fuel_gal,
    variationDegEast: project.default_variation_deg_east,
    manualQnhHpa: project.manual_qnh_hpa?.toString() ?? "",
    tglCount: project.tgl_count,
  };
}

export function candidateFromKey(
  candidates: RouteCandidate[],
  key: string,
): RouteCandidate | null {
  const [kind, rawIndex] = key.split(":", 2);
  const index = Number(rawIndex);
  return (
    candidates.find((candidate) => candidate.kind === kind && candidate.index === index) ?? null
  );
}
