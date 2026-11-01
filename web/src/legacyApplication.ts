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
    { ...payload.error?.details, ...([502, 503, 504].includes(response.status) ? { retryable: true } : {}), ...(payload.error?.candidates ? { candidates: payload.error.candidates } : {}) },
  );
}

type Job = {
  job_id: string;
  status: "queued" | "preparing_weather" | "calculating" | "succeeded" | "failed";
  state?: WebState;
  error?: { code?: string; message?: string };
  progress_percent: number;
  progress_message: string;
};

export class LegacyApplication implements AutoNavLogApplication {
  private async fetch(path: string, options: RequestInit): Promise<Response> {
    try { return await fetch(path, options); }
    catch { throw new ApplicationError("サーバーに接続できませんでした。再試行してください。", "APPLICATION_UNAVAILABLE"); }
  }

  private async readJson<T>(response: Response): Promise<T> {
    try { return await response.json() as T; }
    catch { throw new ApplicationError("処理結果を読み取れませんでした。", "APPLICATION_RESPONSE_INVALID"); }
  }

  private calculationJob: Job | null = null;
  private calculationSession: string | undefined;
  private sessionEpoch = 0;
  private calculationInFlight: Promise<WebState> | null = null;

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
    this.sessionEpoch += 1;
    this.calculationJob = null;
    this.calculationInFlight = null;
    this.token = payload.token;
    this.recovery = payload.state.workingRecovery;
    return payload.state;
  }

  private async fetchJson<T>(
    path: string,
    options: { method?: string; body?: unknown },
    retryOnUnauthorized: boolean,
    timeoutMs = 30_000,
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
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (response.status === 401 && retryOnUnauthorized) {
      await this.createSession(this.recovery);
      return this.fetchJson<T>(path, options, false);
    }
    if (!response.ok) {
      throw await parseError(response);
    }
    if (response.status === 204) return null as T;
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

  private checkCalculationSession(epoch: number): void {
    if (epoch !== this.sessionEpoch) {
      throw new ApplicationError("画面を再読み込みしてセッションを作成してください。", "SESSION_REQUIRED");
    }
  }

  private async jobRequest(path: string, method = "GET", timeoutMs = 30_000, epoch = this.sessionEpoch): Promise<Job | null> {
    // Never replace a session while following its job: that would lose ownership.
    this.checkCalculationSession(epoch);
    const result = await this.fetchJson<Job | null>(path, { method }, false, timeoutMs);
    this.checkCalculationSession(epoch);
    return result;
  }

  private async followJob(job: Job, onProgress?: ProgressListener): Promise<WebState> {
    const epoch = this.sessionEpoch;
    this.calculationSession = this.token;
    this.calculationJob = job;
    const deadline = Date.now() + 10 * 60_000;
    while (job.status !== "succeeded" && job.status !== "failed") {
      this.checkCalculationSession(epoch);
      onProgress?.({ percent: job.progress_percent, message: job.progress_message });
      if (Date.now() >= deadline) {
        throw new ApplicationError("状況確認がタイムアウトした", "CALCULATION_TIMEOUT");
      }
      await new Promise(resolve => setTimeout(resolve, 1_000));
      const remaining = deadline - Date.now();
      if (remaining <= 0) continue;
      try {
        const next = await this.jobRequest(`/api/calculation-jobs/${encodeURIComponent(job.job_id)}`, "GET", Math.min(30_000, remaining), epoch);
        if (!next) throw new ApplicationError("計算結果がありません。", "CALCULATION_RESULT_MISSING");
        job = next;
        this.calculationJob = job;
      } catch (reason) {
        if (!(reason instanceof ApplicationError) || reason.code !== "APPLICATION_UNAVAILABLE" && !reason.details.retryable) throw reason;
        // GET is safe to retry; retain both the job and its original session.
      }
    }
    this.checkCalculationSession(epoch);
    onProgress?.({ percent: job.progress_percent, message: job.progress_message });
    this.calculationJob = null;
    if (job.status === "failed") {
      throw new ApplicationError(job.error?.message ?? "計算に失敗しました。", job.error?.code ?? "CALCULATION_JOB_FAILED");
    }
    if (!job.state) throw new ApplicationError("計算結果がありません。", "CALCULATION_RESULT_MISSING");
    this.recovery = job.state.workingRecovery;
    return job.state;
  }

  private trackJob(job: Job, onProgress?: ProgressListener): Promise<WebState> {
    if (this.calculationInFlight) return this.calculationInFlight;
    const pending = this.followJob(job, onProgress);
    this.calculationInFlight = pending;
    void pending.finally(() => {
      if (this.calculationInFlight === pending) this.calculationInFlight = null;
    }).catch(() => {});
    return pending;
  }

  async resumeCalculation(onProgress?: ProgressListener): Promise<WebState | null> {
    if (this.calculationInFlight) return this.calculationInFlight;
    if (this.calculationJob && this.calculationSession !== this.token) this.calculationJob = null;
    const job = this.calculationJob ?? await this.jobRequest("/api/calculation-jobs/active");
    return job ? this.trackJob(job, onProgress) : null;
  }

  async calculate(onProgress?: ProgressListener): Promise<WebState> {
    if (this.calculationInFlight) return this.calculationInFlight;
    // Reserve the in-flight promise before the first await to prevent double submission.
    const epoch = this.sessionEpoch;
    const pending = (async () => {
      let job: Job | null;
      try {
        job = await this.jobRequest("/api/calculation-jobs", "POST", 30_000, epoch);
      } catch (reason) {
        if (!(reason instanceof ApplicationError) || !["APPLICATION_UNAVAILABLE", "CALCULATION_JOB_ALREADY_ACTIVE"].includes(reason.code)) throw reason;
        job = await this.jobRequest("/api/calculation-jobs/active", "GET", 30_000, epoch);
        if (!job) throw reason; // An unknown POST outcome is never automatically replayed.
      }
      if (!job) throw new ApplicationError("計算結果がありません。", "CALCULATION_RESULT_MISSING");
      return this.followJob(job, onProgress);
    })();
    this.calculationInFlight = pending;
    try { return await pending; }
    finally { if (this.calculationInFlight === pending) this.calculationInFlight = null; }
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
    this.sessionEpoch += 1;
    this.calculationJob = null;
    this.calculationInFlight = null;
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
