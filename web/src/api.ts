import type { ApiErrorPayload, WebState } from "./types";


export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly candidates: string[];

  constructor(message: string, code: string, status: number, candidates: string[] = []) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.candidates = candidates;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let payload: ApiErrorPayload = {};
  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    // The status text is the only safe fallback for a non-JSON failure.
  }
  return new ApiError(
    payload.error?.message?.trim() || response.statusText.trim() || "処理に失敗しました。",
    payload.error?.code ?? "REQUEST_FAILED",
    response.status,
    payload.error?.candidates ?? [],
  );
}

export class ApiClient {
  private bootstrapInFlight: Promise<WebState> | null = null;

  private async createSession(): Promise<WebState> {
    const response = await fetch("/api/session", {
      method: "POST",
      credentials: "same-origin",
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) {
      throw await parseError(response);
    }
    const payload = (await response.json()) as { state: WebState };
    return payload.state;
  }

  private async fetchJson<T>(
    path: string,
    options: { method?: string; body?: unknown },
    retryOnUnauthorized: boolean,
  ): Promise<T> {
    const headers: Record<string, string> = {};
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, {
      method: options.method ?? "GET",
      credentials: "same-origin",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: AbortSignal.timeout(30_000),
    });
    if (response.status === 401 && retryOnUnauthorized) {
      await this.createSession();
      return this.fetchJson<T>(path, options, false);
    }
    if (!response.ok) {
      throw await parseError(response);
    }
    return (await response.json()) as T;
  }

  async bootstrap(): Promise<WebState> {
    if (this.bootstrapInFlight) return this.bootstrapInFlight;
    const pending = (async () => {
      try {
        return await this.fetchJson<WebState>("/api/state", {}, false);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) {
          throw error;
        }
        return this.createSession();
      }
    })();
    this.bootstrapInFlight = pending;
    try {
      return await pending;
    } finally {
      if (this.bootstrapInFlight === pending) this.bootstrapInFlight = null;
    }
  }

  async request<T>(
    path: string,
    options: { method?: string; body?: unknown } = {},
  ): Promise<T> {
    return this.fetchJson<T>(path, options, true);
  }

  async calculate(): Promise<WebState> {
    type Job = {
      job_id: string;
      status: "queued" | "preparing_weather" | "calculating" | "succeeded" | "failed";
      state?: WebState;
      error?: { code?: string; message?: string; status?: number };
    };
    const created = await this.request<Job>("/api/calculation-jobs", { method: "POST" });
    const deadline = Date.now() + 10 * 60_000;
    let job = created;
    while (job.status !== "succeeded" && job.status !== "failed") {
      if (Date.now() >= deadline) {
        throw new ApiError("気象準備と計算がタイムアウトしました。", "CALCULATION_TIMEOUT", 504);
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1_000));
      job = await this.request<Job>(`/api/calculation-jobs/${encodeURIComponent(job.job_id)}`);
    }
    if (job.status === "failed") {
      throw new ApiError(
        job.error?.message ?? "計算に失敗しました。",
        job.error?.code ?? "CALCULATION_JOB_FAILED",
        job.error?.status ?? 500,
      );
    }
    if (!job.state) {
      throw new ApiError("計算結果がありません。", "CALCULATION_RESULT_MISSING", 500);
    }
    return job.state;
  }

  async resetSession(): Promise<void> {
    const response = await fetch("/api/session", {
      method: "DELETE",
      credentials: "same-origin",
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok && response.status !== 401) {
      throw await parseError(response);
    }
  }

  private async fetchTransferAid(retryOnUnauthorized: boolean): Promise<Response> {
    const response = await fetch("/api/transfer-aid", {
      credentials: "same-origin",
      signal: AbortSignal.timeout(30_000),
    });
    if (response.status === 401 && retryOnUnauthorized) {
      await this.createSession();
      return this.fetchTransferAid(false);
    }
    return response;
  }

  async downloadTransferAid(): Promise<void> {
    const response = await this.fetchTransferAid(true);
    if (!response.ok) {
      throw await parseError(response);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const match = /filename="([^"]+)"/.exec(disposition);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = match?.[1] ?? "AutoNavLog_transfer_aid.html";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }
}

export async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}
