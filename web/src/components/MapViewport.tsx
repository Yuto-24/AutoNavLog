import { useEffect, useRef } from "react";
import { useMap, useMapEvents } from "react-leaflet";
import type { KeyValueStorage } from "../platform";

interface Viewport { latitude: number; longitude: number; zoom: number }
function readViewport(storage: KeyValueStorage, key: string): Viewport | null {
  try {
    const value = JSON.parse(storage.getItem(key) ?? "null") as Viewport | null;
    return value && Number.isFinite(value.latitude) && Math.abs(value.latitude) <= 90 &&
      Number.isFinite(value.longitude) && Math.abs(value.longitude) <= 180 &&
      Number.isFinite(value.zoom) && value.zoom >= 0 && value.zoom <= 19 ? value : null;
  } catch { return null; }
}
export function MapViewport({ storage, scope, projectId }: {
  storage: KeyValueStorage; scope: string; projectId: string | null;
}) {
  const map = useMap();
  const initialized = useRef(false);
  const previousProject = useRef<string | null>(null);
  const prefix = `autonavlog.map-viewport.v1.${scope}`;
  const lastKey = `${prefix}.last`;
  const projectKey = projectId ? `${prefix}.project.${projectId}` : lastKey;
  const save = () => {
    const center = map.getCenter().wrap();
    const value = JSON.stringify({ latitude: center.lat, longitude: center.lng, zoom: map.getZoom() });
    try { storage.setItem(projectKey, value); storage.setItem(lastKey, value); } catch { /* Best-effort view preference, never Project data. */ }
  };
  useMapEvents({ moveend: save });
  useEffect(() => {
    const projectView = projectId ? readViewport(storage, projectKey) : null;
    const saved = projectView ?? readViewport(storage, lastKey);
    // Confirmation retains the draft view; opening a saved Project restores its own view.
    if (projectView || !initialized.current || previousProject.current !== null) {
      if (saved) map.setView([saved.latitude, saved.longitude], saved.zoom, { animate: false });
    }
    initialized.current = true;
    previousProject.current = projectId;
    save();
  }, [map, storage, projectKey]);
  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize({ animate: false }));
    observer.observe(map.getContainer());
    return () => observer.disconnect();
  }, [map]);
  return null;
}
