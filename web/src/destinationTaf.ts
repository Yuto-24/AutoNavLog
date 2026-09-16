// Network acquisition only. Python retains the shared ETA/prevailing-wind semantics.
export interface TafAcquisition { records: Record<string, unknown>[]; reason_code: string | null }
const MAX_BYTES = 64 * 1024;

export async function fetchDestinationTaf(
  endpoint: string | undefined, icao: string,
  fetcher: typeof fetch = fetch, timeoutMs = 7000,
): Promise<TafAcquisition> {
  const unavailable = (reason_code: string): TafAcquisition => ({ records: [], reason_code });
  if (!endpoint) return unavailable("TAF_PROXY_NOT_CONFIGURED");
  let url: URL;
  try { url = new URL(endpoint); }
  catch { return unavailable("TAF_PROXY_NOT_CONFIGURED"); }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    if ((url.protocol !== "https:" && !(url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname))) ||
        url.username || url.password || url.search || url.hash || url.pathname !== "/taf" || !/^[A-Z0-9]{4}$/.test(icao)) {
      return unavailable("TAF_PROXY_NOT_CONFIGURED");
    }
    url.searchParams.set("icao", icao);
    const response = await fetcher(url, { credentials: "omit", redirect: "error", signal: controller.signal });
    if (!response.ok || !/^application\/json(?:;|$)/i.test(response.headers.get("Content-Type") ?? "") ||
        Number(response.headers.get("Content-Length")) > MAX_BYTES || !response.body) {
      await response.body?.cancel();
      return unavailable("TAF_FETCH_FAILED");
    }
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_BYTES) { await reader.cancel(); return unavailable("TAF_FETCH_FAILED"); }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const records: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!Array.isArray(records) || records.some(record => !record || typeof record !== "object" ||
        Array.isArray(record) || record.icaoId !== icao)) return unavailable("TAF_FETCH_FAILED");
    return { records, reason_code: null };
  } catch { return unavailable(controller.signal.aborted ? "TAF_FETCH_TIMEOUT" : "TAF_FETCH_FAILED"); }
  finally { clearTimeout(timer); }
}
