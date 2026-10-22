import type { Project, AirportOption } from "./types";
export interface MapRoutePoint {
  id: string;
  original_node_id?: string;
  name: string;
  latitude_deg: number;
  longitude_deg: number;
  airport_id: string | null;
}
export function validMapDraft(value: unknown): value is MapRoutePoint[] {
  if (!Array.isArray(value) || value.length > 500) return false;
  const ids = new Set<string>();
  return value.every(point => {
    if (!point || typeof point !== "object" || typeof point.id !== "string" || ids.has(point.id) ||
        (point.original_node_id !== undefined && typeof point.original_node_id !== "string") ||
        typeof point.name !== "string" || !point.name.trim() || (point.name.length > 100 && !point.original_node_id) ||
        !Number.isFinite(point.latitude_deg) || Math.abs(point.latitude_deg) > 90 ||
        !Number.isFinite(point.longitude_deg) || Math.abs(point.longitude_deg) > 180 ||
        !(point.airport_id === null || typeof point.airport_id === "string" && point.airport_id.length > 0)) return false;
    ids.add(point.id);
    return true;
  }) && (value.length === 0 || Boolean(value[0].airport_id));
}
export function canConfirmMapDraft(points: MapRoutePoint[]): boolean {
  return points.length >= 2 && Boolean(points[0]?.airport_id && points.at(-1)?.airport_id);
}
export function nextWaypointName(points: MapRoutePoint[]): string {
  return `WP${1 + points.reduce((max, point) => Math.max(max, Number(/^WP(\d+)$/.exec(point.name)?.[1] ?? 0)), 0)}`;
}

export function routeDraftFromProject(project: Project, airports: AirportOption[]): MapRoutePoint[] {
  return [...project.route_nodes].sort((a, b) => a.sequence - b.sequence).map((node, index, nodes) => ({
    id: node.id, original_node_id: node.id, name: node.name,
    latitude_deg: node.latitude_deg, longitude_deg: node.longitude_deg,
    airport_id: index === 0 ? project.departure_airport_id : index === nodes.length - 1
      ? project.destination_airport_id : airports.find(airport =>
        airport.latitudeDeg === node.latitude_deg && airport.longitudeDeg === node.longitude_deg)?.id ?? null,
  }));
}
