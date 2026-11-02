import { expect, test } from "@playwright/test";
import { LocalWeatherTransport, WeatherError, type PreparedAsset } from "../src/localWeather";

const digest = "a".repeat(64);
const asset: PreparedAsset = {
  file: `${digest}.npz`,
  sha256: digest,
  bytes: 4,
  run: "20260915210000",
};
const base = new URL("https://weather.example/msm/");
const assetUrl = new URL(asset.file, base);

function key(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : input.toString();
}

class MemoryCache {
  private readonly entries = new Map<string, Response>();

  async match(input: RequestInfo | URL) {
    return this.entries.get(key(input))?.clone();
  }

  async put(input: RequestInfo | URL, response: Response) {
    this.entries.set(key(input), response.clone());
  }

  async delete(input: RequestInfo | URL) {
    return this.entries.delete(key(input));
  }

  async keys() {
    return [...this.entries.keys()].map(url => new Request(url));
  }
}

const cachedResponse = (bytes: Uint8Array) => new Response(bytes, { headers: {
  "Content-Length": String(bytes.byteLength),
  "X-AutoNavLog-Cached-At": String(Date.now()),
} });

const cachesDescriptor = Object.getOwnPropertyDescriptor(globalThis, "caches");
const fetchDescriptor = Object.getOwnPropertyDescriptor(globalThis, "fetch");

test.afterEach(() => {
  if (cachesDescriptor) Object.defineProperty(globalThis, "caches", cachesDescriptor);
  else Reflect.deleteProperty(globalThis, "caches");
  if (fetchDescriptor) Object.defineProperty(globalThis, "fetch", fetchDescriptor);
  else Reflect.deleteProperty(globalThis, "fetch");
});

function install(cache: MemoryCache, fetcher: typeof fetch) {
  Object.defineProperty(globalThis, "caches", {
    configurable: true,
    value: { open: async () => cache },
  });
  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: fetcher,
  });
}

test("corrupt prepared cache is reacquired once and reports cache corruption if reacquisition fails", async () => {
  const cache = new MemoryCache();
  await cache.put(assetUrl, cachedResponse(Uint8Array.of(9)));
  let fetches = 0;
  install(cache, async () => {
    fetches++;
    return new Response(Uint8Array.of(1, 2, 3, 4), {
      status: 200,
      headers: { "Content-Length": "4" },
    });
  });

  const transport = new LocalWeatherTransport(base);
  let accepted = 0;
  await transport.prepared(asset, bytes => {
    accepted++;
    expect([...bytes]).toEqual([1, 2, 3, 4]);
  });
  expect(fetches).toBe(1);
  expect(accepted).toBe(1);

  await cache.put(assetUrl, cachedResponse(Uint8Array.of(9)));
  install(cache, async () => {
    fetches++;
    return new Response("", { status: 503 });
  });

  await expect(transport.prepared(asset, () => {})).rejects.toMatchObject({
    code: "WEATHER_CACHE_CORRUPT",
  });
  expect(fetches).toBe(2);
});


test("valid cached payload outside prepared coverage stays cached and is not reacquired", async () => {
  const cache = new MemoryCache();
  const bytes = Uint8Array.of(1, 2, 3, 4);
  await cache.put(assetUrl, cachedResponse(bytes));
  let fetches = 0;
  install(cache, async () => {
    fetches++;
    throw new Error("offline");
  });
  const transport = new LocalWeatherTransport(base);
  await expect(transport.prepared(asset, actual => {
    expect([...actual]).toEqual([...bytes]);
    throw new WeatherError("WEATHER_PREPARED_UNAVAILABLE", "unprepared test time");
  })).rejects.toMatchObject({ code: "WEATHER_PREPARED_UNAVAILABLE" });
  expect(fetches).toBe(0);
  expect(await cache.match(assetUrl)).toBeDefined();
  // A subsequent covered request still uses the same valid cached snapshot.
  let accepted = false;
  await transport.prepared(asset, () => { accepted = true; });
  expect(accepted).toBe(true);
  expect(fetches).toBe(0);
});


const catalogUrl = new URL("catalog.json", base);
const retainedUrl = new URL("last-good.json", base);
const catalogText = () => JSON.stringify({
  schema_version: 1, generated_at: new Date(Date.now() - 1000).toISOString(),
  expires_at: new Date(Date.now() + 3600000).toISOString(), assets: [asset], listings: {},
});
const plan = (text: string) => {
  const value = JSON.parse(text);
  if (value.schema_version !== 1) throw new WeatherError("WEATHER_CATALOG_INVALID", "invalid");
  return value.assets[0] as PreparedAsset;
};
const accept = (bytes: Uint8Array) => {
  if (bytes.join() !== "1,2,3,4") throw new WeatherError("WEATHER_PAYLOAD_INTEGRITY_FAILED", "invalid");
};
const online: typeof fetch = async input => key(input).endsWith("catalog.json")
  ? new Response(catalogText()) : new Response(Uint8Array.of(1, 2, 3, 4));

async function retain(cache: MemoryCache) {
  install(cache, online);
  await new LocalWeatherTransport(base).acquire(plan, accept);
  expect(await cache.match(retainedUrl)).toBeDefined();
  // Force catalog revalidation without removing the distinct retained pair.
  await cache.delete(catalogUrl);
}

test("offline reload revalidates the complete retained pair and does not renew retention", async () => {
  const cache = new MemoryCache();
  await retain(cache);
  const before = (await cache.match(retainedUrl))!.headers.get("X-AutoNavLog-Cached-At");
  let fetches = 0;
  install(cache, async () => { fetches++; throw new Error("private network diagnostics"); });
  let checked = 0;
  await new LocalWeatherTransport(base).acquire((text, reason, run) => {
    checked++;
    expect(reason).toBe("WEATHER_COMMUNICATION_FAILED");
    expect(run).toBe(asset.run);
    return plan(text);
  }, accept);
  expect(checked).toBe(1);
  expect(fetches).toBe(1);
  expect((await cache.match(retainedUrl))!.headers.get("X-AutoNavLog-Cached-At")).toBe(before);
});

for (const absent of ["empty", "payload", "index", "expired-payload", "expired-index", "denied"] as const) {
  test(`offline with ${absent} storage never claims successful recalculation`, async () => {
    const cache = new MemoryCache();
    if (absent !== "empty") await retain(cache);
    if (absent === "payload") await cache.delete(assetUrl);
    if (absent === "index") await cache.delete(retainedUrl);
    if (absent.startsWith("expired-")) {
      const url = absent === "expired-payload" ? assetUrl : retainedUrl;
      const previous = (await cache.match(url))!;
      const headers = new Headers(previous.headers);
      headers.set("X-AutoNavLog-Cached-At", String(Date.now() - 8 * 86400000));
      await cache.put(url, new Response(await previous.arrayBuffer(), { headers }));
    }
    install(cache, async () => { throw new Error("offline"); });
    if (absent === "denied") Object.defineProperty(globalThis, "caches", {
      configurable: true, value: { open: async () => { throw new Error("storage denied"); } },
    });
    let accepted = false;
    await expect(new LocalWeatherTransport(base).acquire(plan, () => { accepted = true; }))
      .rejects.toMatchObject({ code: "WEATHER_COMMUNICATION_FAILED" });
    expect(accepted).toBe(false);
  });
}

test("storage write rejection allows online calculation but cannot invent offline availability", async () => {
  const cache = new MemoryCache();
  cache.put = async () => { throw new DOMException("quota", "QuotaExceededError"); };
  install(cache, online);
  await new LocalWeatherTransport(base).acquire(plan, accept);
  install(cache, async () => { throw new Error("offline"); });
  await expect(new LocalWeatherTransport(base).acquire(plan, accept))
    .rejects.toMatchObject({ code: "WEATHER_COMMUNICATION_FAILED" });
});

for (const failure of ["schema", "hash", "future", "coverage", "discovery", "oversized", "auth"] as const) {
  test(`public ${failure} failure is not hidden by an older retained pair`, async () => {
    const cache = new MemoryCache();
    await retain(cache);
    await cache.delete(assetUrl);
    // Recreate valid retained payload, while the new public catalog selects another hash.
    await cache.put(assetUrl, cachedResponse(Uint8Array.of(1, 2, 3, 4)));
    let fallback = false;
    const other = { ...asset, sha256: "b".repeat(64), file: `${"b".repeat(64)}.npz` };
    install(cache, async input => {
      if (failure === "auth") return new Response("", { status: 403 });
      if (failure === "oversized") return new Response("", { headers: { "Content-Length": "999999999" } });
      if (key(input).endsWith("catalog.json")) return new Response(JSON.stringify({
        ...JSON.parse(catalogText()), schema_version: failure === "schema" ? 2 : 1, assets: [other],
      }));
      return new Response(Uint8Array.of(9, 9, 9, 9));
    });
    await expect(new LocalWeatherTransport(base).acquire((text, reason) => {
      if (reason) fallback = true;
      if (failure === "discovery") throw new WeatherError("WEATHER_DISCOVERY_FAILED", "invalid listing");
      if (failure === "future") throw new WeatherError("WEATHER_CATALOG_EXPIRED", "future");
      if (failure === "coverage") throw new WeatherError("WEATHER_PREPARED_UNAVAILABLE", "coverage");
      return plan(text);
    }, accept)).rejects.toBeInstanceOf(WeatherError);
    expect(fallback).toBe(false);
  });
}

test("corrupt retained identity and payload remain failures without network payload fetch", async () => {
  for (const damage of ["identity", "payload"]) {
    const cache = new MemoryCache();
    await retain(cache);
    if (damage === "identity") {
      const pair = JSON.parse(await (await cache.match(retainedUrl))!.text());
      pair.asset.bytes = 9;
      await cache.put(retainedUrl, cachedResponse(new TextEncoder().encode(JSON.stringify(pair))));
    } else await cache.put(assetUrl, cachedResponse(Uint8Array.of(9, 9, 9, 9)));
    let requests = 0;
    install(cache, async () => { requests++; throw new Error("offline"); });
    await expect(new LocalWeatherTransport(base).acquire(plan, accept)).rejects.toMatchObject({
      code: damage === "identity" ? "WEATHER_CACHE_CORRUPT" : "WEATHER_PAYLOAD_INTEGRITY_FAILED",
    });
    expect(requests).toBe(1);
  }
});

test("fresh acquisition after offline fallback returns to normal plan and replaces retained pair", async () => {
  const cache = new MemoryCache();
  await retain(cache);
  install(cache, async () => { throw new Error("offline"); });
  await new LocalWeatherTransport(base).acquire(plan, accept);
  install(cache, online);
  const reasons: (string | undefined)[] = [];
  await new LocalWeatherTransport(base).acquire((text, reason) => {
    reasons.push(reason); return plan(text);
  }, accept);
  expect(reasons).toEqual([undefined]);
});


for (const source of ["catalog", "payload"] as const) {
  for (const failure of ["stream", 404, 408, 429, 500, 502, 503, 504] as const) {
    test(`${source} ${failure} acquisition failure permits only the validated retained pair`, async () => {
      const cache = new MemoryCache();
      await retain(cache);
      const other = { ...asset, sha256: "b".repeat(64), file: `${"b".repeat(64)}.npz` };
      install(cache, async input => {
        if (source === "payload" && key(input).endsWith("catalog.json")) {
          return new Response(JSON.stringify({ ...JSON.parse(catalogText()), assets: [other] }));
        }
        return failure === "stream" ? new Response(new ReadableStream({
          start(controller) { controller.error(new TypeError("connection lost")); },
        })) : new Response("", { status: failure });
      });
      const reasons: (string | undefined)[] = [];
      await new LocalWeatherTransport(base).acquire((text, reason, retainedRun) => {
        reasons.push(reason);
        if (reason) expect(retainedRun).toBe(asset.run);
        return plan(text);
      }, accept);
      expect(reasons.at(-1)).toBe(failure === "stream" ? "WEATHER_DOWNLOAD_FAILED"
        : source === "catalog" ? "WEATHER_DISCOVERY_FAILED" : "WEATHER_SOURCE_UNAVAILABLE");
    });
  }
}
