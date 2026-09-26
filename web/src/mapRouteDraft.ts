export interface MapRoutePoint {
  id: string;
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
        typeof point.name !== "string" || !point.name.trim() || point.name.length > 100 ||
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
