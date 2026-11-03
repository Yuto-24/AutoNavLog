import { ApplicationError } from "./application";
import { backupMessages as messages } from "./forecastBackupMessages";

const failures = new Set(["WEATHER_CATALOG_EXPIRED", "WEATHER_COMMUNICATION_FAILED",
  "WEATHER_DISCOVERY_FAILED", "WEATHER_SOURCE_UNAVAILABLE", "WEATHER_TRANSPORT_FAILED",
  "WEATHER_PREPARED_UNAVAILABLE", "WEATHER_RUN_UNAVAILABLE", "WEATHER_OUT_OF_COVERAGE",
  "FORECAST_RUN_OUT_OF_COVERAGE"]);
export function permitsForecastBackup(error: unknown): error is ApplicationError {
  return error instanceof ApplicationError && failures.has(error.code ?? "");
}
export type BackupInput = Record<string, unknown> & { input_identity: string };
export interface BackupTransport {
  calculate(input: BackupInput, signal: AbortSignal, progress?: () => void): Promise<{ request: unknown; result: unknown }>;
}
const errorMessages: Record<string, string> = {
  FORECAST_BACKUP_INCOMPATIBLE: messages.incompatible, FORECAST_BACKUP_INVALID: messages.invalid_result,
  FORECAST_BACKUP_BUSY: messages.busy, FORECAST_BACKUP_EXPIRED: messages.request_expired,
  FORECAST_BACKUP_CONFLICT: messages.request_conflict, ORIGIN_DENIED: messages.origin_denied,
  FORECAST_BACKUP_INTERRUPTED: messages.job_interrupted,
};
function invalid(): never { throw new ApplicationError(messages.invalid_result, "FORECAST_BACKUP_INVALID"); }

export class ForecastBackupTransport implements BackupTransport {
  constructor(private readonly base = import.meta.env?.VITE_FORECAST_BACKUP_URL ?? "") {}
  async calculate(input: BackupInput, signal: AbortSignal, progress?: () => void) {
    if (!this.base) throw new ApplicationError(messages.not_configured, "FORECAST_BACKUP_NOT_CONFIGURED");
    const base = new URL(this.base);
    if (base.username || base.password || base.search || base.hash || base.pathname !== "/api/forecast-backup/jobs/"
      || (base.protocol !== "https:" && !(base.protocol === "http:" && ["localhost", "127.0.0.1"].includes(base.hostname)))) invalid();
    const request = { ...input, request_id: crypto.randomUUID(), issued_at: new Date().toISOString() };
    const capability = Array.from(crypto.getRandomValues(new Uint8Array(32)), value => value.toString(16).padStart(2, "0")).join("");
    const url = new URL(request.request_id, base);
    const headers = { "Content-Type": "application/json", "X-AutoNavLog-Request": capability };
    const body = JSON.stringify(request);
    if (new TextEncoder().encode(body).length > 256 * 1024) invalid();
    const deadline = Date.now() + 610_000;
    let method = "PUT", puts = 0;
    while (Date.now() < deadline) {
      signal.throwIfAborted();
      let response: Response;
      try {
        response = await fetch(url, { method, headers, body: method === "PUT" ? body : undefined,
          credentials: "omit", redirect: "error", cache: "no-store",
          signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]) });
      } catch {
        signal.throwIfAborted();
        // An unknown PUT outcome is reconciled by GET. Only an explicit 404
        // allows reissuing the SAME id/body/capability, never a new computation ID.
        method = "GET";
        progress?.();
        await this.pause(signal);
        continue;
      }
      if (method === "PUT") puts++;
      if (response.status >= 500) {
        await response.body?.cancel(); method = "GET"; progress?.(); await this.pause(signal); continue;
      }
      if (response.status === 404 && method === "GET" && puts < 3) {
        await response.body?.cancel(); method = "PUT"; puts++; continue;
      }
      const reader = response.body?.getReader();
      if (!reader) invalid();
      const chunks: Uint8Array[] = []; let length = 0;
      try {
        while (true) {
          const item = await reader.read();
          if (item.done) break;
          length += item.value.length;
          if (length > 2 * 1024 * 1024 + 4096) { await reader.cancel(); invalid(); }
          chunks.push(item.value);
        }
      } catch (error) {
        if (error instanceof ApplicationError) throw error;
        signal.throwIfAborted();
        method = "GET"; progress?.(); await this.pause(signal); continue;
      } finally { reader.releaseLock(); }
      const bytes = new Uint8Array(length); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      let value;
      try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
      catch { invalid(); }
      if (!response.ok) {
        const code = typeof value?.error?.code === "string" ? value.error.code : "FORECAST_BACKUP_FAILED";
        throw new ApplicationError(errorMessages[code] ?? messages.backup_unavailable,
          code in errorMessages ? code : "FORECAST_BACKUP_FAILED");
      }
      if (!value || value.request_id !== request.request_id) invalid();
      if (value.status === "succeeded" && value.result) return { request, result: value.result };
      if (value.status === "failed") throw new ApplicationError(errorMessages[value.error] ?? messages.backup_unavailable,
        value.error in errorMessages ? value.error : "FORECAST_BACKUP_FAILED");
      if (value.status !== "running") invalid();
      method = "GET";
      await this.pause(signal);
    }
    throw new ApplicationError(messages.job_interrupted, "FORECAST_BACKUP_INTERRUPTED");
  }
  private pause(signal: AbortSignal) {
    return new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(signal.reason); };
      const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 2000);
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
  }
}
