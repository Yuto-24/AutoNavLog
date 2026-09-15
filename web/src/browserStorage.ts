import { PlatformError, type KeyValueStorage } from "./platform";

export const WEATHER_CACHE_PREFIX = "autonavlog.weather.";
// #144 owns the cache format, acquisition, TTL and invalidation.
export async function evictWeatherCache(): Promise<void> {
  if (typeof caches === "undefined") return;
  for (const key of await caches.keys()) if (key.startsWith(WEATHER_CACHE_PREFIX)) await caches.delete(key);
}
export function requestPersistentStorage(): void {
  try { void globalThis.navigator?.storage?.persist?.().catch(() => undefined); } catch { /* best effort */ }
}
export function browserKeyValueStorage(kind: "sessionStorage" | "localStorage"): KeyValueStorage {
  const access = (): Storage => {
    const storage = globalThis[kind];
    if (!storage) throw new PlatformError(kind, "UNSUPPORTED", "この環境では保存領域を利用できません。");
    return storage;
  };
  return {
    getItem: key => access().getItem(key),
    setItem: (key, value) => access().setItem(key, value),
    removeItem: key => access().removeItem(key),
  };
}
