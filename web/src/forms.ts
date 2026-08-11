import type { AirportOption, Project, RouteCandidate } from "./types";

export type QnhUnit = "hPa" | "inHg";

export interface PlanningForm {
  flightDate: string;
  departureTimeJst: string;
  departureAirportId: string;
  destinationAirportId: string;
  destinationPatternAltitudeFtMsl: string;
  totalUsableFuelGal: number;
  variationDegEast: number;
  manualQnhValue: string;
  qnhUnit: QnhUnit;
  tglCount: number;
  allLegAltitudeFtMsl: number;
  candidateKey: string;
  routeUseConfirmed: boolean;
  polygonRouteConfirmed: boolean;
  manualQnhConfirmed: boolean;
  usePenultimateAsVrep: boolean;
}

const HPA_PER_INHG = 33.8638866667;
const EARTH_RADIUS_NM = 3440.065;
const DESTINATION_MATCH_LIMIT_NM = 5;

function distanceNm(
  first: [number, number],
  second: [number, number],
): number {
  const toRadians = (degrees: number) => degrees * Math.PI / 180;
  const latitudeDelta = toRadians(second[0] - first[0]);
  const longitudeDelta = toRadians(second[1] - first[1]);
  const firstLatitude = toRadians(first[0]);
  const secondLatitude = toRadians(second[0]);
  const haversine =
    Math.sin(latitudeDelta / 2) ** 2 +
    Math.cos(firstLatitude) * Math.cos(secondLatitude) *
      Math.sin(longitudeDelta / 2) ** 2;
  return 2 * EARTH_RADIUS_NM * Math.asin(Math.min(1, Math.sqrt(haversine)));
}

export function destinationAirportForCandidate(
  candidate: RouteCandidate | null,
  airports: AirportOption[],
): AirportOption | null {
  const endpoint = candidate?.coordinates.at(-1);
  if (!endpoint) return null;
  const nearest = airports.reduce<{ airport: AirportOption; distance: number } | null>(
    (current, airport) => {
      const distance = distanceNm(endpoint, [airport.latitudeDeg, airport.longitudeDeg]);
      return current === null || distance < current.distance
        ? { airport, distance }
        : current;
    },
    null,
  );
  return nearest && nearest.distance <= DESTINATION_MATCH_LIMIT_NM
    ? nearest.airport
    : null;
}

export function variationForDeparture(airport: AirportOption | undefined): number {
  return airport && airport.latitudeDeg < 32 ? 7 : 8;
}

function tomorrowIso(): string {
  const tomorrow = new Date(Date.now() + 86_400_000);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(tomorrow);
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return [byType.year, byType.month, byType.day].join("-");
}

export function qnhHpa(form: PlanningForm): number | null {
  if (!form.manualQnhValue.trim()) return null;
  const entered = Number(form.manualQnhValue);
  if (!Number.isFinite(entered)) return null;
  return form.qnhUnit === "hPa" ? entered : entered * HPA_PER_INHG;
}

export function patternAltitudeFtMsl(value: string): number | null {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const altitude = Number(trimmed);
  if (
    !Number.isInteger(altitude) ||
    altitude < 100 ||
    altitude > 25000 ||
    altitude % 100 !== 0
  ) {
    return null;
  }
  return altitude;
}

export function convertQnhValue(
  value: string,
  from: QnhUnit,
  to: QnhUnit,
): string {
  if (!value.trim() || from === to) return value;
  const entered = Number(value);
  if (!Number.isFinite(entered)) return "";
  const hpa = from === "hPa" ? entered : entered * HPA_PER_INHG;
  return to === "hPa"
    ? hpa.toFixed(1).replace(/\.0$/, "")
    : (hpa / HPA_PER_INHG).toFixed(2);
}

export function initialPlanningForm(airports: AirportOption[] = []): PlanningForm {
  const departure = airports.find((airport) => airport.id === "RJFM") ?? airports[0];
  return {
    flightDate: tomorrowIso(),
    departureTimeJst: "09:00",
    departureAirportId: departure?.id ?? "",
    destinationAirportId: "",
    destinationPatternAltitudeFtMsl: "",
    totalUsableFuelGal: 90,
    variationDegEast: variationForDeparture(departure),
    manualQnhValue: "",
    qnhUnit: "hPa",
    tglCount: 0,
    allLegAltitudeFtMsl: 3000,
    candidateKey: "",
    routeUseConfirmed: false,
    polygonRouteConfirmed: false,
    manualQnhConfirmed: false,
    usePenultimateAsVrep: true,
  };
}

export function formFromProject(
  project: Project,
  previous: PlanningForm,
  airports: AirportOption[],
): PlanningForm {
  const arrival = project.metadata.ui_state?.arrival_plan ?? null;
  const destinationMaster = airports.find(
    (airport) => airport.id === project.destination_airport_id,
  );
  return {
    ...previous,
    flightDate: project.flight_date,
    departureTimeJst: project.planned_departure_time_jst.slice(11, 16),
    departureAirportId: project.departure_airport_id,
    destinationAirportId: project.destination_airport_id,
    destinationPatternAltitudeFtMsl: String(
      arrival?.selected_pattern_altitude_ft_msl ??
      destinationMaster?.patternAltitudeFtMsl ??
      previous.destinationPatternAltitudeFtMsl,
    ),
    totalUsableFuelGal: project.total_usable_fuel_gal,
    variationDegEast: project.default_variation_deg_east,
    manualQnhValue:
      project.manual_qnh_hpa === null
        ? ""
        : convertQnhValue(project.manual_qnh_hpa.toString(), "hPa", previous.qnhUnit),
    qnhUnit: previous.qnhUnit,
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
