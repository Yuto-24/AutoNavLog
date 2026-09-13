import { ApplicationError } from "./application";
import type { ApplicationErrorDetails, AutoNavLogApplication, ImportRouteInput, ConfirmRouteInput, UpdateProjectInput, ProgressListener } from "./application";
import type { CheckPointInput, WebState } from "./types";

async function parseError(response: Response): Promise<ApplicationError> {
  let payload: {
    error?: { message?: string; code?: string; candidates?: string[]; details?: ApplicationErrorDetails };
    detail?: { loc: (string | number)[]; msg: string; type: string }[];
  } = {};
  try {
    payload = await response.json() ?? {};
  } catch {
    // Non-JSON failures expose a neutral message, never the HTTP status text.
  }
  if (response.status === 422 && Array.isArray(payload.detail)) {
    return new ApplicationError("入力内容を確認してください。", "VALIDATION_FAILED", {
      issues: payload.detail.map(issue => ({
        location: ["body", "path", "query"].includes(String(issue.loc[0])) ? issue.loc.slice(1) : issue.loc,
        message: issue.msg, type: issue.type,
      })),
    });
  }
  return new ApplicationError(
    payload.error?.message?.trim() || "処理に失敗しました。",
    payload.error?.code ?? "REQUEST_FAILED",
    { ...payload.error?.details, ...(payload.error?.candidates ? { candidates: payload.error.candidates } : {}) },
  );
}

export class LegacyApplication implements AutoNavLogApplication {
  private async fetch(path: string, options: RequestInit): Promise<Response> {
    try { return await fetch(path, options); }
    catch { throw new ApplicationError("処理に接続できませんでした。再試行してください。", "APPLICATION_UNAVAILABLE"); }
  }

  private async readJson<T>(response: Response): Promise<T> {
    try { return await response.json() as T; }
    catch { throw new ApplicationError("処理結果を読み取れませんでした。", "APPLICATION_RESPONSE_INVALID"); }
  }

  private bootstrapInFlight: Promise<WebState> | null = null;

  private async createSession(): Promise<WebState> {
    const response = await this.fetch("/api/session", {
      method: "POST",
      credentials: "same-origin",
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) {
      throw await parseError(response);
    }
    const payload = await this.readJson<{ state: WebState }>(response);
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
    const response = await this.fetch(path, {
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
    return await this.readJson<T>(response);
  }

  async bootstrap(): Promise<WebState> {
    if (this.bootstrapInFlight) return this.bootstrapInFlight;
    const pending = (async () => {
      const response = await this.fetch("/api/state", {
        credentials: "same-origin", signal: AbortSignal.timeout(30_000),
      });
      if (response.status === 401) return this.createSession();
      if (!response.ok) throw await parseError(response);
      return await this.readJson<WebState>(response);
    })();
    this.bootstrapInFlight = pending;
    try { return await pending; }
    finally { if (this.bootstrapInFlight === pending) this.bootstrapInFlight = null; }
  }

  private async request<T>(
    path: string,
    options: { method?: string; body?: unknown } = {},
  ): Promise<T> {
    return this.fetchJson<T>(path, options, true);
  }

  async calculate(
    onProgress?: ProgressListener,
  ): Promise<WebState> {
    type Job = {
      job_id: string;
      status: "queued" | "preparing_weather" | "calculating" | "succeeded" | "failed";
      state?: WebState;
      error?: { code?: string; message?: string; status?: number };
      progress_percent: number;
      progress_message: string;
    };
    const created = await this.request<Job>("/api/calculation-jobs", { method: "POST" });
    const deadline = Date.now() + 10 * 60_000;
    let job = created;
    onProgress?.({ percent: job.progress_percent, message: job.progress_message });
    while (job.status !== "succeeded" && job.status !== "failed") {
      if (Date.now() >= deadline) {
        throw new ApplicationError("気象準備と計算がタイムアウトしました。", "CALCULATION_TIMEOUT");
      }
      await new Promise((resolve) => setTimeout(resolve, 1_000));
      job = await this.request<Job>(`/api/calculation-jobs/${encodeURIComponent(job.job_id)}`);
      onProgress?.({ percent: job.progress_percent, message: job.progress_message });
    }
    if (job.status === "failed") {
      throw new ApplicationError(
        job.error?.message ?? "計算に失敗しました。",
        job.error?.code ?? "CALCULATION_JOB_FAILED",
      );
    }
    if (!job.state) {
      throw new ApplicationError("計算結果がありません。", "CALCULATION_RESULT_MISSING");
    }
    return job.state;
  }

  async newWork(): Promise<void> {
    const response = await this.fetch("/api/session", {
      method: "DELETE",
      credentials: "same-origin",
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok && response.status !== 401) {
      throw await parseError(response);
    }
  }

  importRoute(input: ImportRouteInput) { return this.request<WebState>("/api/import", { method: "POST", body: input }); }
  confirmRoute(input: ConfirmRouteInput) { return this.request<WebState>("/api/route/confirm", { method: "POST", body: input }); }
  updateProject(input: UpdateProjectInput) { return this.request<WebState>("/api/project", { method: "PUT", body: input }); }
  updateAndRecalculate(input: UpdateProjectInput) { return this.request<WebState>("/api/project/recalculate", { method: "POST", body: input }); }
  renameRouteNode(nodeId: string, name: string) { return this.request<WebState>(`/api/project/route-nodes/${encodeURIComponent(nodeId)}/name`, { method: "PUT", body: { name } }); }
  replaceCheckPoints(checkPoints: CheckPointInput[]) { return this.request<WebState>("/api/project/check-points", { method: "PUT", body: { check_points: checkPoints } }); }
  acknowledge(key: string, checked: boolean) { return this.request<WebState>(`/api/acknowledgements/${encodeURIComponent(key)}`, { method: "PUT", body: { checked } }); }
  saveProject(name: string) { return this.request<WebState>("/api/projects/save", { method: "POST", body: { name } }); }
  loadProject(projectId: string) { return this.request<WebState>("/api/projects/load", { method: "POST", body: { project_id: projectId } }); }
  deleteProject(projectId: string) { return this.request<WebState>(`/api/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" }); }

}
