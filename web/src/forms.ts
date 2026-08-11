import type { AirportOption, Project, RouteCandidate } from "./types";

export type QnhUnit = "hPa" | "inHg";

export interface PlanningForm {
  flightDate: string;
  departureTimeJst: string;
  departureAirportId: string;
  destinationAirportId: string;
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
  return {
    flightDate: tomorrowIso(),
    departureTimeJst: "09:00",
    departureAirportId: airports[0]?.id ?? "",
    destinationAirportId: airports[1]?.id ?? airports[0]?.id ?? "",
    totalUsableFuelGal: 90,
    variationDegEast: 8,
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
): PlanningForm {
  return {
    ...previous,
    flightDate: project.flight_date,
    departureTimeJst: project.planned_departure_time_jst.slice(11, 16),
    departureAirportId: project.departure_airport_id,
    destinationAirportId: project.destination_airport_id,
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
