import type {
  AirportOption,
  FtdWeatherSettings,
  Project,
  RouteCandidate,
} from "./types";

export interface PlanningForm {
  flightDate: string;
  departureTimeJst: string;
  departureAirportId: string;
  destinationAirportId: string;
  destinationPatternAltitudeFtMsl: string;
  totalUsableFuelGal: string;
  variationDegEast: number;
  runUpIncluded: boolean;
  noseFairingEnabled: boolean;
  airConditioningEnabled: boolean;
  tglCount: number;
  allLegAltitudeFtMsl: number;
  candidateKey: string;
  routeUseConfirmed: boolean;
  polygonRouteConfirmed: boolean;
  usePenultimateAsVrep: boolean;
  weatherMode: "FORECAST" | "FTD";
  ftdSurfaceWindDirection: string;
  ftdSurfaceWindSpeed: string;
  ftdWind5000Direction: string;
  ftdWind5000Speed: string;
}

function airportForCandidateEndpoint(
  candidate: RouteCandidate | null,
  airports: AirportOption[],
  endpoint: "departure" | "destination",
): AirportOption | null {
  const airportId = endpoint === "departure"
    ? candidate?.departureAirportId
    : candidate?.destinationAirportId;
  return airports.find((airport) => airport.id === airportId) ?? null;
}

export function departureAirportForCandidate(
  candidate: RouteCandidate | null,
  airports: AirportOption[],
): AirportOption | null {
  return airportForCandidateEndpoint(candidate, airports, "departure");
}

export function destinationAirportForCandidate(
  candidate: RouteCandidate | null,
  airports: AirportOption[],
): AirportOption | null {
  return airportForCandidateEndpoint(candidate, airports, "destination");
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

export function usableFuelGal(form: PlanningForm): number | null {
  if (!form.totalUsableFuelGal.trim()) return null;
  const entered = Number(form.totalUsableFuelGal);
  if (!Number.isFinite(entered) || entered <= 0 || entered > 200) return null;
  return entered;
}

export function ftdWeatherSettings(form: PlanningForm): FtdWeatherSettings | null {
  if (
    !form.ftdSurfaceWindDirection.trim() ||
    !form.ftdSurfaceWindSpeed.trim() ||
    !form.ftdWind5000Direction.trim() ||
    !form.ftdWind5000Speed.trim()
  ) {
    return null;
  }
  const surfaceDirection = Number(form.ftdSurfaceWindDirection);
  const surfaceSpeed = Number(form.ftdSurfaceWindSpeed);
  const upperDirection = Number(form.ftdWind5000Direction);
  const upperSpeed = Number(form.ftdWind5000Speed);
  if (
    !Number.isFinite(surfaceDirection) ||
    !Number.isInteger(surfaceDirection) ||
    surfaceDirection < 1 ||
    surfaceDirection > 360 ||
    !Number.isFinite(upperDirection) ||
    !Number.isInteger(upperDirection) ||
    upperDirection < 1 ||
    upperDirection > 360 ||
    !Number.isFinite(surfaceSpeed) ||
    surfaceSpeed < 0 ||
    surfaceSpeed > 200 ||
    !Number.isFinite(upperSpeed) ||
    upperSpeed < 0 ||
    upperSpeed > 200
  ) {
    return null;
  }
  return {
    surface_wind: {
      direction_deg_from: surfaceDirection,
      speed_kt: surfaceSpeed,
    },
    wind_at_5000_ft: {
      direction_deg_from: upperDirection,
      speed_kt: upperSpeed,
    },
  };
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

export function initialPlanningForm(airports: AirportOption[] = []): PlanningForm {
  const departure = airports.find((airport) => airport.id === "RJFM") ?? airports[0];
  return {
    flightDate: tomorrowIso(),
    departureTimeJst: "09:00",
    departureAirportId: departure?.id ?? "",
    destinationAirportId: "",
    destinationPatternAltitudeFtMsl: "",
    totalUsableFuelGal: "90",
    variationDegEast: variationForDeparture(departure),
    runUpIncluded: true,
    noseFairingEnabled: false,
    airConditioningEnabled: true,
    tglCount: 0,
    allLegAltitudeFtMsl: 3000,
    candidateKey: "",
    routeUseConfirmed: false,
    polygonRouteConfirmed: false,
    usePenultimateAsVrep: true,
    weatherMode: "FORECAST",
    ftdSurfaceWindDirection: "360",
    ftdSurfaceWindSpeed: "15",
    ftdWind5000Direction: "270",
    ftdWind5000Speed: "30",
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
    totalUsableFuelGal: String(project.total_usable_fuel_gal),
    variationDegEast: project.default_variation_deg_east,
    runUpIncluded: project.run_up_included,
    noseFairingEnabled: project.nose_fairing_enabled,
    airConditioningEnabled: project.air_conditioning_enabled,
    tglCount: project.tgl_count,
    weatherMode: project.weather_mode,
    ftdSurfaceWindDirection: String(
      (project.ftd_weather?.surface_wind.direction_deg_from === 0
        ? 360
        : project.ftd_weather?.surface_wind.direction_deg_from) ??
        previous.ftdSurfaceWindDirection,
    ),
    ftdSurfaceWindSpeed: String(
      project.ftd_weather?.surface_wind.speed_kt ?? previous.ftdSurfaceWindSpeed,
    ),
    ftdWind5000Direction: String(
      (project.ftd_weather?.wind_at_5000_ft.direction_deg_from === 0
        ? 360
        : project.ftd_weather?.wind_at_5000_ft.direction_deg_from) ??
        previous.ftdWind5000Direction,
    ),
    ftdWind5000Speed: String(
      project.ftd_weather?.wind_at_5000_ft.speed_kt ?? previous.ftdWind5000Speed,
    ),
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
