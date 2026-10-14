import { test } from "node:test";
import assert from "node:assert/strict";
import { createTafProxy } from "./worker.mjs";

const origins = ["https://school.example", "https://second.example"];
const stations = ["RJFM", "RJFK", "RJFO", "RJFF", "RJFT", "RJFR", "RJFC", "RJFE"];
const deferred = () => {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
};
function rates(clock) {
  const counts = { CLIENT_RATE: 0, UPSTREAM_RATE: 0, STATION_RATE: 0 };
  const bindings = { ALLOWED_ORIGINS: origins.join(",") };
  for (const [name, limit] of [["CLIENT_RATE", 20], ["UPSTREAM_RATE", 60], ["STATION_RATE", 1]]) {
    const windows = new Map();
    bindings[name] = { limit: async ({ key }) => {
      counts[name]++;
      const window = `${Math.floor(clock.time / 60000)}:${key}`;
      const used = (windows.get(window) ?? 0) + 1;
      windows.set(window, used);
      return { success: used <= limit };
    } };
  }
  return { bindings, counts };
}
function isolate({ clock = { time: 1800000000000 }, rate = rates(clock), upstream,
  entries = new Map(), write = async () => {}, timeoutMs = 5000 } = {}) {
  let calls = 0;
  const pending = [];
  const proxy = createTafProxy({ now: () => clock.time, timeoutMs,
    fetchUpstream: async (url, init) => {
      calls++;
      return upstream ? upstream(url, init) : Response.json([{ icaoId: new URL(url).searchParams.get("ids"), rawTAF: "original" }]);
    },
    cache: () => ({ match: async key => entries.get(key.url)?.clone(), put: async (key, value) => {
      await write();
      entries.set(key.url, value);
    } }),
  });
  return { clock, rate, entries, calls: () => calls,
    run: (icao = "RJFM", origin = origins[0], init = {}) => proxy.fetch(
      new Request(`https://proxy.example/taf?icao=${icao}`, {
        ...init, headers: { Origin: origin, "CF-Connecting-IP": "192.0.2.100", ...init.headers },
      }), rate.bindings, { waitUntil: promise => pending.push(promise) }),
    drain: () => Promise.all(pending),
  };
}

test("100 school-IP warm-cache readers succeed even when all miss bindings are unavailable", async () => {
  const h = isolate();
  assert.equal((await h.run()).status, 200);
  await h.drain();
  const before = { ...h.rate.counts };
  for (const name of Object.keys(before)) h.rate.bindings[name] = { limit: async () => { throw new Error("unavailable"); } };
  h.clock.time += 10000;
  const responses = await Promise.all(Array.from({ length: 100 }, (_, i) => h.run("RJFM", origins[i % 2])));
  for (const [i, response] of responses.entries()) {
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), origins[i % 2]);
    assert.equal(response.headers.get("Cache-Control"), "public, max-age=290");
    assert.equal((await response.json())[0].icaoId, "RJFM");
  }
  assert.equal(h.calls(), 1);
  assert.deepEqual(h.rate.counts, before);
  assert.equal((await h.run("RJFM", "https://evil.example")).status, 403);
  assert.equal((await h.run("rjfm")).status, 400);
  assert.equal((await h.run("RJFM", origins[0], { method: "POST" })).status, 405);
  assert.equal((await h.run("RJFM", origins[0], { headers: { "CF-Connecting-IP": "" } })).status, 403);
});

test("100 cold-cache readers share one fetch and independently readable origin-specific responses", async () => {
  const ready = deferred();
  const h = isolate({ upstream: async () => { await ready.promise; return Response.json([{ icaoId: "RJFM" }]); } });
  const readers = Array.from({ length: 100 }, (_, i) => h.run("RJFM", origins[i % 2]));
  ready.resolve();
  const responses = await Promise.all(readers);
  assert.equal(h.calls(), 1);
  assert.deepEqual(h.rate.counts, { CLIENT_RATE: 1, UPSTREAM_RATE: 1, STATION_RATE: 1 });
  for (const [i, response] of responses.entries()) {
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), [{ icaoId: "RJFM" }]);
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), origins[i % 2]);
  }
  await h.drain();
});

test("coalescing survives asynchronous cache publication without extending expiry", async () => {
  const writing = deferred();
  const h = isolate({ write: () => writing.promise });
  const first = await h.run();
  assert.equal(first.status, 200);
  assert.equal(h.entries.size, 0);
  h.clock.time += 20000;
  const readers = await Promise.all(Array.from({ length: 100 }, () => h.run()));
  assert.ok(readers.every(r => r.status === 200 && r.headers.get("Cache-Control") === "public, max-age=280"));
  h.clock.time += 280000;
  assert.equal((await h.run()).status, 503); // The in-flight cache write cannot serve stale success.
  assert.equal(h.calls(), 1);
  writing.resolve();
  await h.drain();
  assert.equal((await h.run()).status, 200);
  assert.equal(h.calls(), 2);
});

test("8 airports, 100 readers each: bounded four-station waves consume 8 upstream tokens", async () => {
  const h = isolate();
  for (const wave of [stations.slice(0, 4), stations.slice(4)]) {
    const responses = await Promise.all(wave.flatMap(icao => Array.from({ length: 100 }, () => h.run(icao))));
    assert.ok(responses.every(response => response.status === 200));
    await h.drain();
  }
  assert.equal(h.calls(), 8);
  assert.deepEqual(h.rate.counts, { CLIENT_RATE: 8, UPSTREAM_RATE: 8, STATION_RATE: 8 });
});

test("8 simultaneously cold airports keep the four-fetch cap without a retry queue", async () => {
  const ready = deferred();
  const h = isolate({ upstream: async url => { await ready.promise; return Response.json([{ icaoId: new URL(url).searchParams.get("ids") }]); } });
  const readers = stations.flatMap(icao => Array.from({ length: 100 }, () => h.run(icao)));
  ready.resolve();
  const responses = await Promise.all(readers);
  assert.equal(responses.filter(r => r.status === 200).length, 400);
  assert.equal(responses.filter(r => r.status === 503).length, 400);
  assert.equal(h.calls(), 4);
  await h.drain();
});

for (const sharedLocationLimiter of [true, false]) test(`100 readers in 4 isolates; shared-location limiter=${sharedLocationLimiter}`, async () => {
  const clock = { time: 1800000000000 };
  const shared = rates(clock);
  const isolates = Array.from({ length: 4 }, () => isolate({ clock, rate: sharedLocationLimiter ? shared : rates(clock) }));
  const responses = await Promise.all(Array.from({ length: 100 }, (_, i) => isolates[i % 4].run()));
  assert.equal(isolates.reduce((sum, h) => sum + h.calls(), 0), sharedLocationLimiter ? 1 : 4);
  assert.equal(responses.filter(r => r.status === 200).length, sharedLocationLimiter ? 25 : 100);
  assert.ok(responses.every(r => [200, 429].includes(r.status)));
  await Promise.all(isolates.map(h => h.drain()));
});

for (const emptyStatus of [200, 204]) test(`normal 5-minute / empty 1-minute TTL switching, AMD refresh, empty HTTP ${emptyStatus}`, async () => {
  const values = [() => Response.json([{ icaoId: "RJFM", rawTAF: "original" }]),
    () => emptyStatus === 204 ? new Response(null, { status: 204 }) : Response.json([]),
    () => Response.json([{ icaoId: "RJFM", rawTAF: "TAF AMD" }])];
  const h = isolate({ upstream: async () => values.shift()() });
  assert.equal((await h.run()).headers.get("Cache-Control"), "public, max-age=300");
  await h.drain();
  h.clock.time += 299000;
  assert.equal((await h.run()).headers.get("Cache-Control"), "public, max-age=1");
  h.clock.time += 1000;
  const empty = await h.run();
  assert.deepEqual(await empty.json(), []);
  assert.equal(empty.headers.get("Cache-Control"), "public, max-age=60");
  await h.drain();
  h.clock.time += 59000;
  assert.equal((await h.run()).headers.get("Cache-Control"), "public, max-age=1");
  h.clock.time += 1000;
  const amended = await h.run();
  assert.equal((await amended.json())[0].rawTAF, "TAF AMD");
  assert.equal(amended.headers.get("Cache-Control"), "public, max-age=300");
  assert.equal(h.calls(), 3);
  await h.drain();
});

for (const [name, upstream, status] of [
  ["429", async () => new Response(null, { status: 429 }), 502],
  ["timeout", async (_, { signal }) => new Promise((_, reject) => signal.addEventListener("abort", () => reject(new Error("abort")))), 504],
  ["invalid JSON", async () => new Response("invalid", { headers: { "Content-Type": "application/json" } }), 502],
  ["wrong ICAO", async () => Response.json([{ icaoId: "RJFK" }]), 502],
]) test(`100 expired-cache readers share ${name} failure without stale success or automatic retry`, async () => {
  let fail = false;
  const h = isolate({ timeoutMs: 20, upstream: async (...args) => fail ? upstream(...args) : Response.json([{ icaoId: "RJFM" }]) });
  assert.equal((await h.run()).status, 200);
  await h.drain();
  h.clock.time += 300000;
  fail = true;
  const responses = await Promise.all(Array.from({ length: 100 }, () => h.run()));
  for (const response of responses) {
    assert.equal(response.status, status);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
  }
  assert.equal(h.calls(), 2);
  await h.drain();
  assert.equal((await h.run()).status, 429); // A failed miss consumed the station token.
  assert.equal(h.calls(), 2);
});

test("miss abuse limits remain effective across distinct ICAOs", async () => {
  const h = isolate();
  for (let i = 0; i < 21; i++) {
    assert.equal((await h.run(`R${String(i).padStart(3, "0")}`)).status, i < 20 ? 200 : 429);
    await h.drain();
  }
  assert.equal(h.calls(), 20);
});
