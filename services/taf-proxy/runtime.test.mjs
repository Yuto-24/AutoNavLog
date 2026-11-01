import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

// Run the production module and bindings in workerd, not just Node's Fetch implementation.
test("workerd: real module, CORS/cache/rate bindings and no redirect forwarding", async () => {
  const config = JSON.parse(readFileSync(new URL("wrangler.jsonc", import.meta.url)));
  const calls = [];
  const mf = new Miniflare(convertV4MiniflareOptions({
    modules: true, scriptPath: new URL("worker.mjs", import.meta.url).pathname,
    compatibilityDate: config.compatibility_date,
    bindings: { ALLOWED_ORIGINS: "https://static.example" },
    ratelimits: Object.fromEntries(config.ratelimits.map(({ name, ...binding }) => [name, binding])),
    outboundService: request => {
      const url = new URL(request.url);
      calls.push(request.url);
      assert.equal(url.origin + url.pathname, "https://aviationweather.gov/api/data/taf");
      if (url.searchParams.get("ids") === "RJFK") return new Response(null, {
        status: 302, headers: { Location: "https://evil.example/" },
      });
      return Response.json([{ icaoId: url.searchParams.get("ids") }]);
    },
  }));
  try {
    const get = async (icao, origin = "https://static.example") => {
      const response = await mf.dispatchFetch(`https://proxy.example/taf?icao=${icao}`, {
        headers: { Origin: origin, "CF-Connecting-IP": "192.0.2.1" },
      });
      // Consume once, immediately: Miniflare wraps an Undici response's stream,
      // whose original response can be GC'd while a batch waits for other readers.
      const body = await response.json();
      return { status: response.status, headers: response.headers, body };
    };
    const response = await get("RJFM");
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), "https://static.example");
    assert.deepEqual(response.body, [{ icaoId: "RJFM" }]);
    // Drain waitUntil via workerd's cache write before testing the cache hit.
    const cache = await mf.getCaches();
    for (let i = 0; i < 50 && !await cache.default.match("https://proxy.example/taf?icao=RJFM"); i++) {
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert.equal((await get("RJFM")).status, 200);
    assert.equal(calls.length, 1);
    const warm = await Promise.all(Array.from({ length: 100 }, () => get("RJFM")));
    assert.ok(warm.every(response => response.status === 200));
    for (const response of warm) assert.deepEqual(response.body, [{ icaoId: "RJFM" }]);
    assert.equal(calls.length, 1); // Warm readers do not consume the school-IP miss limit.
    const cold = await Promise.all(Array.from({ length: 100 }, () => get("RJFO")));
    assert.ok(cold.every(response => response.status === 200));
    for (const response of cold) assert.deepEqual(response.body, [{ icaoId: "RJFO" }]);
    assert.equal(calls.length, 2); // Native workerd bindings still allow only one station miss.
    assert.equal((await get("RJFM", "https://evil.example")).status, 403);
    assert.equal((await get("RJFK")).status, 502);
    assert.equal(calls.length, 3);
    // The failed fetch consumed the station's native rate token, and was not cached.
    assert.equal((await get("RJFK")).status, 429);
    assert.equal(calls.length, 3);
  } finally { await mf.dispose(); }
});
