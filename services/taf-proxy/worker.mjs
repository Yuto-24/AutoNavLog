const ENDPOINT = "https://aviationweather.gov/api/data/taf";
const MAX_BYTES = 64 * 1024;
const TTL_SECONDS = 300;

// Slots are per isolate, not a global concurrency/quota accounting system.
export function createTafProxy({ fetchUpstream = fetch, cache = () => caches.default, timeoutMs = 5000, now = Date.now } = {}) {
  let active = 0;
  const inflight = new Map();
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
        const key = new Request(`${url.origin}/taf?icao=${icao}`);
        const cached = await cache().match(key);
        if (cached) {
          const remaining = Math.floor((Number(cached.headers.get("X-TAF-Expires")) - now()) / 1000);
          if (remaining > 0) return new Response(cached.body, {
            headers: { ...headers, "Cache-Control": `public, max-age=${remaining}` },
          });
        }
        const respond = result => {
          if (result.status !== 200) return error(result.status);
          const remaining = Math.floor((result.expires - now()) / 1000);
          if (remaining <= 0) return error(503);
          return new Response(result.payload, { headers: {
            ...headers, "Cache-Control": `public, max-age=${remaining}`,
          } });
        };
        // Join before consuming miss tokens: one school IP may have many readers.
        // The promise is installed synchronously before any rate-binding await.
        if (inflight.has(icao)) return respond(await inflight.get(icao));
        if (active >= 4) return error(503);
        active++;
        let writing;
        const acquire = async () => {
          if (!(await env.CLIENT_RATE.limit({ key: ip })).success ||
              !(await env.UPSTREAM_RATE.limit({ key: "taf" })).success ||
              !(await env.STATION_RATE.limit({ key: icao })).success) return { status: 429 };
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), timeoutMs);
          try {
            const response = await fetchUpstream(`${ENDPOINT}?ids=${icao}&format=json`, {
              headers: { Accept: "application/json", "User-Agent": "AutoNavLog destination-taf-proxy" },
              redirect: "manual", signal: controller.signal,
            });
            if (response.status !== 200 && response.status !== 204) {
              await response.body?.cancel();
              return { status: 502 };
            }
            let payload = "[]";
            if (response.status === 200) {
              if (!/^application\/json(?:;|$)/i.test(response.headers.get("Content-Type") ?? "") ||
                  Number(response.headers.get("Content-Length")) > MAX_BYTES || !response.body) {
                await response.body?.cancel();
                return { status: 502 };
              }
              const reader = response.body.getReader();
              const chunks = [];
              let size = 0;
              try {
                while (true) {
                  const { done, value } = await reader.read();
                  if (done) break;
                  size += value.byteLength;
                  if (size > MAX_BYTES) { await reader.cancel(); return { status: 502 }; }
                  chunks.push(value);
                }
              } finally { reader.releaseLock(); }
              const bytes = new Uint8Array(size);
              let offset = 0;
              for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
              payload = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
              const records = JSON.parse(payload);
              if (!Array.isArray(records) || records.some(record => !record || typeof record !== "object" ||
                  Array.isArray(record) || record.icaoId !== icao)) return { status: 502 };
            }
            const ttl = JSON.parse(payload).length === 0 ? 60 : TTL_SECONDS;
            const expires = now() + ttl * 1000;
            const stored = new Response(payload, { headers: {
              "Content-Type": "application/json", "Cache-Control": `public, max-age=${ttl}`,
              "X-TAF-Expires": String(expires),
            } });
            writing = cache().put(key, stored).catch(() => undefined);
            return { status: 200, payload, expires };
          } catch { return { status: controller.signal.aborted ? 504 : 502 }; }
          finally { clearTimeout(timer); }
        };
        const pending = acquire().catch(() => ({ status: 503 }));
        inflight.set(icao, pending);
        const cleanup = pending.then(async () => {
          // Keep the result available while Cache API publication completes.
          await writing;
          inflight.delete(icao);
          active--;
        });
        ctx.waitUntil(cleanup);
        return respond(await pending);
      } catch { return error(503); }
    },
  };
}
export default createTafProxy();
