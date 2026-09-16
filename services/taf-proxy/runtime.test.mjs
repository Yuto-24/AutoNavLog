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
    const get = (icao, origin = "https://static.example") => mf.dispatchFetch(`https://proxy.example/taf?icao=${icao}`, {
      headers: { Origin: origin, "CF-Connecting-IP": "192.0.2.1" },
    });
    const response = await get("RJFM");
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), "https://static.example");
    assert.deepEqual(await response.json(), [{ icaoId: "RJFM" }]);
    // Drain waitUntil via workerd's cache write before testing the cache hit.
    const cache = await mf.getCaches();
    for (let i = 0; i < 50 && !await cache.default.match("https://proxy.example/taf?icao=RJFM"); i++) {
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert.equal((await get("RJFM")).status, 200);
    assert.equal(calls.length, 1);
    assert.equal((await get("RJFM", "https://evil.example")).status, 403);
    assert.equal((await get("RJFK")).status, 502);
    assert.equal(calls.length, 2);
    // The failed fetch consumed the station's native rate token, and was not cached.
    assert.equal((await get("RJFK")).status, 429);
    assert.equal(calls.length, 2);
  } finally { await mf.dispose(); }
});
