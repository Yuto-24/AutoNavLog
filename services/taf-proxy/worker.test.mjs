import { test } from "node:test";
import assert from "node:assert/strict";
import { createTafProxy } from "./worker.mjs";

const MAX_BYTES = 64 * 1024;
const origin = "https://static.example";
const permit = { limit: async () => ({ success: true }) };
const env = { ALLOWED_ORIGINS: origin, CLIENT_RATE: permit, UPSTREAM_RATE: permit, STATION_RATE: permit };
function harness(fetchUpstream = async () => Response.json([{ icaoId: "RJFM" }]), options = {}) {
  const entries = new Map();
  const pending = [];
  let calls = 0;
  const proxy = createTafProxy({ fetchUpstream: (...args) => { calls++; return fetchUpstream(...args); },
    cache: () => ({ match: async key => entries.get(key.url)?.clone(),
      put: async (key, value) => { entries.set(key.url, value); } }), ...options });
  return { calls: () => calls, entries,
    run: (path = "/taf?icao=RJFM", init = {}, bindings = env) => proxy.fetch(new Request(`https://proxy.example${path}`, {
      headers: { Origin: origin, "CF-Connecting-IP": "192.0.2.1" }, ...init,
    }), bindings, { waitUntil: promise => pending.push(promise) }) };
}

test("fixed upstream, no credentials forwarded, CORS and bounded cache reuse", async () => {
  const h = harness(async (url, init) => {
    assert.equal(url, "https://aviationweather.gov/api/data/taf?ids=RJFM&format=json");
    assert.equal(init.redirect, "manual");
    assert.deepEqual(Object.keys(init.headers).sort(), ["Accept", "User-Agent"]);
    return Response.json([{ icaoId: "RJFM" }]);
  });
  const first = await h.run();
  assert.equal(first.status, 200);
  assert.equal(first.headers.get("Access-Control-Allow-Origin"), origin);
  assert.equal(first.headers.get("Access-Control-Allow-Credentials"), null);
  assert.equal(first.headers.get("Vary"), "Origin");
  assert.deepEqual(await first.json(), [{ icaoId: "RJFM" }]);
  assert.equal((await h.run()).status, 200);
  assert.equal(h.calls(), 1);
  const other = "https://other.example";
  const crossOriginHit = await h.run(undefined, {
    headers: { Origin: other, "CF-Connecting-IP": "192.0.2.2" },
  }, { ...env, ALLOWED_ORIGINS: `${origin},${other}` });
  assert.equal(crossOriginHit.status, 200);
  assert.equal(crossOriginHit.headers.get("Access-Control-Allow-Origin"), other);
  assert.equal(h.calls(), 1);
  const key = [...h.entries.keys()][0];
  h.entries.set(key, Response.json([{ icaoId: "RJFM" }], { headers: { "X-TAF-Expires": "1" } }));
  await h.run();
  assert.equal(h.calls(), 2);
});

test("reject unauthorized origins, methods, routes and queries without upstream", async () => {
  const h = harness();
  for (const headers of [{}, { Origin: "null" }, { Origin: "https://evil.example" }]) {
    assert.equal((await h.run(undefined, { headers })).status, 403);
  }
  for (const method of ["POST", "OPTIONS", "HEAD"]) assert.equal((await h.run(undefined, { method })).status, 405);
  assert.equal((await h.run("/metar?icao=RJFM")).status, 404);
  for (const query of ["", "icao=rjfm", "icao=RJFM,RJFK", "icao=RJFM&icao=RJFK", "icao=RJFM&url=https://evil.example", "icao=RJFM&format=xml", "icao=RJFM%0a", "ids=RJFM"]) {
    assert.equal((await h.run(`/taf?${query}`)).status, 400, query);
  }
  assert.equal(h.calls(), 0);
});

test("client, station and aggregate rate limits plus unavailable binding fail closed", async () => {
  for (const name of ["CLIENT_RATE", "UPSTREAM_RATE", "STATION_RATE"]) {
    const h = harness();
    assert.equal((await h.run(undefined, {}, { ...env, [name]: { limit: async () => ({ success: false }) } })).status, 429);
    assert.equal(h.calls(), 0);
    assert.equal((await h.run(undefined, {}, { ...env, [name]: undefined })).status, 503);
  }
});

test("upstream HTTP/redirect/JSON/schema/size failures are not cached", async () => {
  const cases = [
    () => new Response("bad", { status: 429 }), () => new Response("bad", { status: 500 }),
    () => new Response(null, { status: 302, headers: { Location: "https://evil.example" } }),
    () => new Response("<html>"), () => Response.json({ error: "bad" }),
    () => Response.json([{ icaoId: "RJFK" }]),
    () => new Response("oops", { headers: { "Content-Type": "application/json" } }),
    () => new Response("[]", { headers: { "Content-Type": "application/json", "Content-Length": String(MAX_BYTES + 1) } }),
    () => new Response(new ReadableStream({ start(c) { c.enqueue(new Uint8Array(MAX_BYTES)); c.enqueue(new Uint8Array(1)); c.close(); } }), { headers: { "Content-Type": "application/json" } }),
    () => { throw new Error("network"); },
  ];
  for (const response of cases) {
    const h = harness(response);
    const result = await h.run();
    assert.equal(result.status, 502);
    assert.equal(result.headers.get("Access-Control-Allow-Origin"), origin);
    assert.equal(result.headers.get("Cache-Control"), "no-store");
    assert.equal(h.entries.size, 0);
  }
});

test("204 is a short cached empty dataset", async () => {
  const h = harness(async () => new Response(null, { status: 204 }));
  const result = await h.run();
  assert.deepEqual(await result.json(), []);
  assert.equal(result.headers.get("Cache-Control"), "public, max-age=60");
});

test("deadline covers headers and streamed body and releases concurrency", async () => {
  for (const bodyTimeout of [false, true]) {
    const h = harness(async (_, { signal }) => {
      if (bodyTimeout) return new Response(new ReadableStream({ start(c) {
        signal.addEventListener("abort", () => c.error(new Error("aborted")));
      } }), { headers: { "Content-Type": "application/json" } });
      return new Promise((_, reject) => signal.addEventListener("abort", () => reject(new Error("aborted"))));
    }, { timeoutMs: 20 });
    const inflight = Array.from({ length: 4 }, () => h.run());
    await new Promise(resolve => setTimeout(resolve, 1));
    assert.equal((await h.run()).status, 503);
    for (const response of await Promise.all(inflight)) assert.equal(response.status, 504);
    assert.equal((await h.run()).status, 504);
    assert.equal(h.calls(), 5);
  }
});
