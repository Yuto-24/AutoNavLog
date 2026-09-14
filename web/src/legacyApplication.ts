import { ApplicationError } from "./application";
import type { ApplicationErrorDetails, AutoNavLogApplication, ImportRouteInput, ConfirmRouteInput, UpdateProjectInput, ProgressListener } from "./application";
import type { CheckPointInput, WebState, WorkingRecovery } from "./types";

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

  private token: string | undefined;
  private recovery: WorkingRecovery | undefined;

  private async createSession(recovery?: WorkingRecovery): Promise<WebState> {
    const response = await this.fetch("/api/application-session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(recovery ?? null),
      credentials: "same-origin",
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok) {
      throw await parseError(response);
    }
    const payload = await this.readJson<{ state: WebState; token: string }>(response);
    if (typeof payload.token !== "string" || !payload.token || !payload.state) {
      throw new ApplicationError("処理結果を読み取れませんでした。", "APPLICATION_RESPONSE_INVALID");
    }
    this.token = payload.token;
    this.recovery = payload.state.workingRecovery;
    return payload.state;
  }

  private async fetchJson<T>(
    path: string,
    options: { method?: string; body?: unknown },
    retryOnUnauthorized: boolean,
  ): Promise<T> {
    const headers: Record<string, string> = this.token ? { "X-AutoNavLog-Session": this.token } : {};
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
      await this.createSession(this.recovery);
      return this.fetchJson<T>(path, options, false);
    }
    if (!response.ok) {
      throw await parseError(response);
    }
    return await this.readJson<T>(response);
  }

  async bootstrap(recovery?: WorkingRecovery): Promise<WebState> {
    if (this.bootstrapInFlight) return this.bootstrapInFlight;
    const pending = this.createSession(recovery);
    this.bootstrapInFlight = pending;
    try { return await pending; }
    finally { if (this.bootstrapInFlight === pending) this.bootstrapInFlight = null; }
  }

  private async request<T>(
    path: string,
    options: { method?: string; body?: unknown } = {},
  ): Promise<T> {
    const result = await this.fetchJson<T>(path, options, true);
    if (result && typeof result === "object" && "workingRecovery" in result) {
      this.recovery = (result as unknown as WebState).workingRecovery;
    }
    return result;
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
    this.recovery = job.state.workingRecovery;
    return job.state;
  }

  async newWork(): Promise<void> {
    const response = await this.fetch("/api/application-session", {
      method: "DELETE",
      headers: this.token ? { "X-AutoNavLog-Session": this.token } : {},
      credentials: "same-origin",
      signal: AbortSignal.timeout(30_000),
    });
    if (!response.ok && response.status !== 401) {
      throw await parseError(response);
    }
    this.token = undefined;
    this.recovery = undefined;
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
