import { expect, test } from "@playwright/test";
import { LocalWeatherTransport, type PreparedAsset } from "../src/localWeather";

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
