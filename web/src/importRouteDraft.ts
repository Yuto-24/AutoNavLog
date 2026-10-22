import type { MapRoutePoint } from "./mapRouteDraft";
import type { AirportOption, RouteCandidate } from "./types";

export type ImportDraftEndpoint = "departure" | "destination";
export type ImportRouteDraftResult =
  | { status: "ready"; points: MapRoutePoint[]; missingEndpoints: [] }
  | { status: "unmatched-endpoints"; points: MapRoutePoint[]; missingEndpoints: ImportDraftEndpoint[] }
  | { status: "invalid"; reason: "point-count" | "coordinates" };

function validCoordinate(latitude: number, longitude: number): boolean {
  return Number.isFinite(latitude) && Math.abs(latitude) <= 90 &&
    Number.isFinite(longitude) && Math.abs(longitude) <= 180;
}

/** Adapt the common KML/KMZ parser's candidate without confirming or mutating a Project.
 * Airport IDs are the facade's existing 5 NM match; do not introduce another distance
 * model here (the accompanying display distances are rounded). Unmatched endpoints
 * remain visible in the preview, but must not be persisted as a valid Map Draft.
 */
export function importCandidateToMapDraft(
  candidate: RouteCandidate,
  airports: readonly AirportOption[],
): ImportRouteDraftResult {
  const coordinates = candidate.coordinates;
  if (coordinates.length < 2 || coordinates.length > 500) {
    return { status: "invalid", reason: "point-count" };
  }
  if (coordinates.some(([latitude, longitude]) => !validCoordinate(latitude, longitude))) {
    return { status: "invalid", reason: "coordinates" };
  }
  const departure = airports.find(airport => airport.id === candidate.departureAirportId);
  const destination = airports.find(airport => airport.id === candidate.destinationAirportId);
  if ([departure, destination].some(airport => airport &&
    !validCoordinate(airport.latitudeDeg, airport.longitudeDeg))) {
    return { status: "invalid", reason: "coordinates" };
  }
  const missingEndpoints: ImportDraftEndpoint[] = [];
  if (!departure) missingEndpoints.push("departure");
  if (!destination) missingEndpoints.push("destination");
  let waypoint = 0;
  const points = coordinates.map(([latitude, longitude], occurrence): MapRoutePoint => {
    const airport = occurrence === 0 ? departure : occurrence === coordinates.length - 1 ? destination : undefined;
    return {
      // Occurrences, not coordinates or airport identities, distinguish repeated visits.
      id: `import:${candidate.kind}:${candidate.index}:${occurrence}`,
      name: airport ? airport.icao : `WP${++waypoint}`,
      latitude_deg: airport?.latitudeDeg ?? latitude,
      longitude_deg: airport?.longitudeDeg ?? longitude,
      airport_id: airport?.id ?? null,
    };
  });
  return missingEndpoints.length
    ? { status: "unmatched-endpoints", points, missingEndpoints }
    : { status: "ready", points, missingEndpoints: [] };
}
