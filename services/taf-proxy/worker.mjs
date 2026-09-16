const ENDPOINT = "https://aviationweather.gov/api/data/taf";
const MAX_BYTES = 64 * 1024;
const TTL_SECONDS = 300;

// Slots are per isolate, not a global concurrency/quota accounting system.
export function createTafProxy({ fetchUpstream = fetch, cache = () => caches.default, timeoutMs = 5000 } = {}) {
  let active = 0;
  return {
    async fetch(request, env, ctx) {
      const origin = request.headers.get("Origin");
      const allowed = (env.ALLOWED_ORIGINS ?? "").split(",").map(value => value.trim()).filter(Boolean);
      const headers = { "Content-Type": "application/json", "Cache-Control": "no-store", "Vary": "Origin", "X-Content-Type-Options": "nosniff" };
      const error = (status) => new Response('{"availability":"UNAVAILABLE"}', { status, headers });
      if (!origin || origin === "null" || !allowed.includes(origin)) return error(403);
      headers["Access-Control-Allow-Origin"] = origin;
      if (request.method !== "GET") return error(405);
      const url = new URL(request.url);
      if (url.pathname !== "/taf") return error(404);
      const params = [...url.searchParams];
      if (params.length !== 1 || params[0][0] !== "icao" || !/^[A-Z0-9]{4}$/.test(params[0][1])) return error(400);
      const icao = params[0][1];
      const ip = request.headers.get("CF-Connecting-IP");
      if (!ip) return error(403);
      try {
        if (!(await env.CLIENT_RATE.limit({ key: ip })).success) return error(429);
        const key = new Request(`${url.origin}/taf?icao=${icao}`);
        const cached = await cache().match(key);
        if (cached) {
          const remaining = Math.floor((Number(cached.headers.get("X-TAF-Expires")) - Date.now()) / 1000);
          if (remaining > 0) return new Response(cached.body, {
            headers: { ...headers, "Cache-Control": `public, max-age=${remaining}` },
          });
        }
        if (active >= 4) return error(503);
        active++;
        try {
          if (!(await env.UPSTREAM_RATE.limit({ key: "taf" })).success ||
              !(await env.STATION_RATE.limit({ key: icao })).success) return error(429);
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), timeoutMs);
          try {
            const response = await fetchUpstream(`${ENDPOINT}?ids=${icao}&format=json`, {
              headers: { Accept: "application/json", "User-Agent": "AutoNavLog destination-taf-proxy" },
              redirect: "manual", signal: controller.signal,
            });
            if (response.status !== 200 && response.status !== 204) {
              await response.body?.cancel();
              return error(502);
            }
            let payload = "[]";
            if (response.status === 200) {
              if (!/^application\/json(?:;|$)/i.test(response.headers.get("Content-Type") ?? "") ||
                  Number(response.headers.get("Content-Length")) > MAX_BYTES || !response.body) {
                await response.body?.cancel();
                return error(502);
              }
              const reader = response.body.getReader();
              const chunks = [];
              let size = 0;
              try {
                while (true) {
                  const { done, value } = await reader.read();
                  if (done) break;
                  size += value.byteLength;
                  if (size > MAX_BYTES) { await reader.cancel(); return error(502); }
                  chunks.push(value);
                }
              } finally { reader.releaseLock(); }
              const bytes = new Uint8Array(size);
              let offset = 0;
              for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
              payload = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
              const records = JSON.parse(payload);
              if (!Array.isArray(records) || records.some(record => !record || typeof record !== "object" ||
                  Array.isArray(record) || record.icaoId !== icao)) return error(502);
            }
            const ttl = response.status === 204 ? 60 : TTL_SECONDS;
            const stored = new Response(payload, { headers: {
              "Content-Type": "application/json", "Cache-Control": `public, max-age=${ttl}`,
              "X-TAF-Expires": String(Date.now() + ttl * 1000),
            } });
            ctx.waitUntil(cache().put(key, stored).catch(() => undefined));
            return new Response(payload, { headers: { ...headers, "Cache-Control": `public, max-age=${ttl}` } });
          } catch { return error(controller.signal.aborted ? 504 : 502); }
          finally { clearTimeout(timer); }
        } finally { active--; }
      } catch { return error(503); }
    },
  };
}
export default createTafProxy();
