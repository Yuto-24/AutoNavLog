/** Static prepared-data transport. No model, Run-selection or interpolation rules. */
export class WeatherError extends Error {
  constructor(readonly code: string, message: string) { super(message); }
}

export interface PreparedAsset { file: string; sha256: string; bytes: number; run: string }
const CACHE = "autonavlog.weather.msm.v1";
const CATALOG_TTL = 5 * 60_000;
const PAYLOAD_TTL = 7 * 24 * 60 * 60_000;
const MAX_PAYLOAD = 32 * 1024 * 1024;
const MAX_CACHE = 128 * 1024 * 1024;

export class LocalWeatherTransport {
  constructor(
    private readonly base = new URL(`${import.meta.env.BASE_URL}weather/msm/`, self.location.origin),
  ) {}

  private async cache(): Promise<Cache | undefined> {
    try { return await caches.open(CACHE); } catch { return undefined; }
  }

  private async cached(url: URL, ttl: number): Promise<Response | undefined> {
    try {
      const cache = await this.cache();
      const response = await cache?.match(url);
      if (!response) return undefined;
      const age = Date.now() - Number(response.headers.get("X-AutoNavLog-Cached-At"));
      if (!Number.isFinite(age) || age < 0 || age >= ttl) {
        await cache?.delete(url);
        return undefined;
      }
      return response;
    } catch { return undefined; }
  }

  private async remove(url: URL) {
    try { await (await this.cache())?.delete(url); } catch { /* disposable */ }
  }

  private async store(url: URL, bytes: Uint8Array) {
    try {
      const cache = await this.cache();
      if (!cache) return;
      let total = bytes.byteLength;
      const entries = [];
      for (const key of await cache.keys()) {
        const entry = await cache.match(key);
        const size = Number(entry?.headers.get("Content-Length"));
        if (!Number.isFinite(size) || size < 0) { await cache.delete(key); continue; }
        total += size;
        entries.push({ key, size });
      }
      // Cache API key order is insertion order; discard oldest disposable data first.
      for (const entry of entries) {
        if (total <= MAX_CACHE) break;
        await cache.delete(entry.key);
        total -= entry.size;
      }
      await cache.put(url, new Response(bytes.slice().buffer, { headers: {
        "Content-Length": String(bytes.byteLength), "X-AutoNavLog-Cached-At": String(Date.now()),
      } }));
    } catch { /* Quota/denial affects warm reuse, never Project data or a valid result. */ }
  }

  private async read(response: Response, limit: number): Promise<Uint8Array> {
    if (Number(response.headers.get("Content-Length")) > limit) {
      await response.body?.cancel();
      throw new WeatherError("WEATHER_DOWNLOAD_FAILED", "MSM配信データがサイズ上限を超えています。");
    }
    const reader = response.body?.getReader();
    if (!reader) throw new WeatherError("WEATHER_DOWNLOAD_FAILED", "MSM応答本文がありません。");
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        size += next.value.byteLength;
        if (size > limit) {
          await reader.cancel();
          throw new WeatherError("WEATHER_DOWNLOAD_FAILED", "MSM配信データがサイズ上限を超えています。");
        }
        chunks.push(next.value);
      }
    } catch (error) {
      if (error instanceof WeatherError) throw error;
      throw new WeatherError("WEATHER_DOWNLOAD_FAILED", "MSMデータのdownloadを完了できません。");
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return bytes;
  }

  private async download(url: URL, limit: number, catalog: boolean): Promise<Uint8Array> {
    let response: Response;
    try {
      response = await fetch(url, { cache: "no-cache", credentials: "omit", redirect: "error",
        signal: AbortSignal.timeout(60_000) });
    } catch {
      throw new WeatherError("WEATHER_COMMUNICATION_FAILED", "MSM配信元へ接続できません。");
    }
    if (!response.ok) {
      await response.body?.cancel();
      throw new WeatherError(catalog ? "WEATHER_DISCOVERY_FAILED"
        : response.status === 404 || response.status === 503 ? "WEATHER_SOURCE_UNAVAILABLE"
        : "WEATHER_DOWNLOAD_FAILED", `MSM取得に失敗しました（HTTP ${response.status}）。`);
    }
    return this.read(response, limit);
  }

  async catalog<T>(accept: (text: string) => T): Promise<T> {
    const url = new URL("catalog.json", this.base);
    const cached = await this.cached(url, CATALOG_TTL);
    if (cached) {
      let text: string | undefined;
      try {
        text = new TextDecoder("utf-8", { fatal: true }).decode(await this.read(cached, 4 * 1024 * 1024));
        const expiry = Date.parse(JSON.parse(text).expires_at);
        if (!(expiry > Date.now())) text = undefined;
      } catch { text = undefined; }
      if (text !== undefined) {
        try { return accept(text); }
        catch (error) {
          if (!(error instanceof WeatherError) || error.code !== "WEATHER_CATALOG_INVALID") throw error;
        }
      }
      await this.remove(url);
    }
    const bytes = await this.download(url, 4 * 1024 * 1024, true);
    let text: string;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); JSON.parse(text); }
    catch { throw new WeatherError("WEATHER_CATALOG_INVALID", "MSM配信情報を読み取れません。"); }
    const result = accept(text); // Python validates the index before it enters the cache.
    await this.store(url, bytes);
    return result;
  }

  async prepared(asset: PreparedAsset, accept: (bytes: Uint8Array) => void): Promise<void> {
    if (!/^[a-f0-9]{64}$/.test(asset.sha256) || asset.file !== `${asset.sha256}.npz`
      || !Number.isSafeInteger(asset.bytes) || asset.bytes <= 0 || asset.bytes > MAX_PAYLOAD) {
      throw new WeatherError("WEATHER_CATALOG_INVALID", "MSM配信file情報が不正です。");
    }
    const url = new URL(asset.file, this.base);
    const validate = (bytes: Uint8Array) => {
      if (bytes.byteLength !== asset.bytes) throw new WeatherError(
        "WEATHER_PAYLOAD_INTEGRITY_FAILED", "MSMデータの長さが配信情報と一致しません。");
      accept(bytes); // MsmPreparedData.from_bytes validates SHA-256, format and budgets.
    };
    const cached = await this.cached(url, PAYLOAD_TTL);
    let corrupt = false;
    if (cached) {
      try { validate(await this.read(cached, MAX_PAYLOAD)); return; }
      catch { corrupt = true; await this.remove(url); }
    }
    let bytes: Uint8Array;
    try { bytes = await this.download(url, MAX_PAYLOAD, false); }
    catch (error) {
      if (corrupt) throw new WeatherError("WEATHER_CACHE_CORRUPT",
        `MSM cacheが破損し、再取得できません。${error instanceof Error ? error.message : ""}`);
      throw error;
    }
    validate(bytes);
    await this.store(url, bytes);
  }
}
