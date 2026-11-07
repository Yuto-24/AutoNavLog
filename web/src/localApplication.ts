import { ForecastBackupTransport, permitsForecastBackup, type BackupInput, type BackupTransport } from "./forecastBackup";
import { backupMessages } from "./forecastBackupMessages";
import { accountBoundaryClosed, type AuthProvider } from "./auth";
import { ApplicationError } from "./application";
import type { AutoNavLogApplication, CalculationOptions, ImportRouteInput, ConfirmRouteInput, UpdateProjectInput, ProgressListener, LoadProjectOptions } from "./application";
import { sameProjectContent, sameProjectDraft } from "./projectLoad";
import type { CheckPointInput, WebState, WorkingRecovery } from "./types";
import { LocalClient } from "./localClient";
import type { LocalProjectRepository, LocalProjectRecord, LocalProjectRepositoryFactory } from "./localProjectRepository";

export class LocalApplication implements AutoNavLogApplication {
  readonly checkpointLoads = true;
  sync?: import("./accountSync").AccountSyncControl;
  private active = true;
  private generation = 0;
  private pendingBackup?: AbortController;
  private client: Pick<LocalClient, "request" | "dispose"> | undefined;
  private readonly repository: LocalProjectRepository;
  private tokens = new Map<string, string>();
  private listedTokens = new Map<string, string>();
  private queue: Promise<unknown> = Promise.resolve();
  constructor(createRepository: LocalProjectRepositoryFactory,
    private readonly createClient: () => Pick<LocalClient, "request" | "dispose"> = () => new LocalClient(),
    readonly auth?: AuthProvider,
    private readonly backup: BackupTransport = new ForecastBackupTransport()) {
    this.repository = createRepository(record => this.request<LocalProjectRecord>("validateRecord", record), undefined,
      (record, copyId) => this.request<LocalProjectRecord>("prepareSyncResolution", { record, copy_id: copyId ?? null }));
  }

  dispose() {
    this.invalidatePending();
    this.active = false;
    this.client?.dispose();
    this.client = undefined;
  }
  private invalidatePending() {
    this.generation++;
    this.pendingBackup?.abort();
    this.pendingBackup = undefined;
  }
  private assertActive() { if (!this.active) throw accountBoundaryClosed(); }
  private async request<T>(operation: string, input?: unknown): Promise<T> {
    this.assertActive();
    try {
      const result = await (this.client ??= this.createClient()).request<T>(operation, input);
      this.assertActive();
      return result;
    }
    catch (error) {
      if (error instanceof ApplicationError) throw error;
      throw new ApplicationError("処理を実行できませんでした。画面を再読み込みしてください。", "APPLICATION_UNAVAILABLE");
    }
  }
  private serial<T>(operation: () => Promise<T>): Promise<T> {
    const next = this.queue.then(() => { this.assertActive(); return operation(); }).then(value => { this.assertActive(); return value; });
    this.queue = next.catch(() => undefined);
    return next;
  }
  private async present(state: WebState): Promise<WebState> {
    if (state.workingRecovery && state.project) {
      const token = this.tokens.get(state.project.id) ?? null;
      state.workingRecovery.durableToken = token;
      if (token && this.repository.captureWorking) {
        state.workingRecovery.repositoryRecovery = await this.repository.captureWorking(state.project.id, token);
      }
    }

    const listing = await this.repository.list().catch(() => null);
    if (!listing) {
      state.savedProjects = [];
      state.storageWarning = "端末の保存データにアクセスできません。ブラウザの保存設定を確認してください。";
      return state;
    }
    this.listedTokens = new Map(Object.entries(listing.tokens ?? {}));
    state.savedProjects = listing.projects;
    state.storageWarning = listing.unavailable.length
      ? "一部のプロジェクトを読み込めません。元データは保持され、他のプロジェクトは利用できます。" : undefined;
    return state;
  }
  private async persist(state: WebState, name?: string): Promise<WebState> {
    const working = state.workingRecovery;
    if (!working?.project) {
      if (name !== undefined) throw new ApplicationError("保存するプロジェクトがありません。", "PROJECT_REQUIRED");
      return this.present(state);
    }
    const draft = structuredClone(working.project);
    const expected = this.tokens.get(draft.id) ?? null;
    const existing = expected ? await (this.repository.readWorking?.(draft.id, expected) ?? this.repository.read(draft.id)) : undefined;
    if (existing && existing.draft.revision !== draft.revision) throw new ApplicationError(
      "別のタブでプロジェクトが保存されています。開き直してください。", "PROJECT_REVISION_CONFLICT");
    const updatedAt = new Date().toISOString();
    if (name !== undefined) {
      draft.updated_at = updatedAt;
      draft.name = name.trim().slice(0, 60) || "route";
      draft.revision += 1;
      draft.metadata.project_name_auto = false;
    }
    const record: LocalProjectRecord = {
      schemaVersion: 2, id: draft.id, token: crypto.randomUUID(),
      draft, checkpoint: name !== undefined ? structuredClone(draft) : existing?.checkpoint ?? null,
      lastCalculation: working.last_calculation ?? existing?.lastCalculation ?? null,
      updatedAt,
    };
    await this.repository.write(record, expected, !record.checkpoint);
    this.tokens.set(record.id, record.token);
    if (name !== undefined) {
      const { durableToken: _token, repositoryRecovery: _repository, ...recovery } = working;
      state = await this.request<WebState>("adoptSavedProject", {
        working: { ...recovery, project: draft }, rounding_mode: state.roundingMode ?? false,
      });
    }
    return this.present(state);
  }
  refreshProjects() { return this.serial(async () => this.present(await this.request<WebState>("state"))); }
  private assertResolved() {
    const status = this.sync?.getState();
    if (status?.conflicts.length || status?.imports.length) throw new ApplicationError("表示中のプロジェクトの選択を完了してください。", "PROJECT_REVISION_CONFLICT");
  }
  private execute(operation: string, input?: unknown, progress?: ProgressListener): Promise<WebState> {
    // Invalidate immediately, before a queued edit waits for network work.
    this.invalidatePending();
    const generation = this.generation;
    return this.serial(async () => {
      this.assertResolved();
      let state: WebState;
      try {
        try { state = await this.request<WebState>(operation, input); }
        catch (error) {
          if (!["calculate", "updateAndRecalculate"].includes(operation) || !permitsForecastBackup(error)) throw error;
          if (generation !== this.generation) throw new ApplicationError(backupMessages.stale_result, "FORECAST_BACKUP_STALE");
          const controller = new AbortController();
          this.pendingBackup = controller;
          try {
            const prepared = await this.request<BackupInput>("prepareForecastBackup", {
              reason: error.code, ...(operation === "calculate" && input ? input as object : {}),
            });
            progress?.({ percent: 30, message: backupMessages.backup_progress });
            const result = await this.backup.calculate(prepared, controller.signal,
              () => progress?.({ percent: 30, message: backupMessages.pending_unknown }));
            this.assertActive();
            if (generation !== this.generation || controller.signal.aborted)
              throw new ApplicationError(backupMessages.stale_result, "FORECAST_BACKUP_STALE");
            const previous = await this.request<WebState>("state");
            if (generation !== this.generation || controller.signal.aborted)
              throw new ApplicationError(backupMessages.stale_result, "FORECAST_BACKUP_STALE");
            state = await this.request<WebState>("adoptForecastBackup", result);
            if (generation !== this.generation || controller.signal.aborted) {
              // The worker may have committed while an edit queued in this adapter.
              // Restore before releasing the serial queue; no new edit ran yet.
              if (previous.workingRecovery) {
                const { durableToken: _token, repositoryRecovery: _repository, ...working } = previous.workingRecovery;
                await this.request("refreshSavedWork", { working, rounding_mode: previous.roundingMode ?? false });
              }
              throw new ApplicationError(backupMessages.stale_result, "FORECAST_BACKUP_STALE");
            }
          } catch (backupError) {
            if (controller.signal.aborted) throw new ApplicationError(backupMessages.stale_result, "FORECAST_BACKUP_STALE");
            if (backupError instanceof ApplicationError) throw backupError;
            throw new ApplicationError(backupMessages.backup_unavailable, "FORECAST_BACKUP_FAILED");
          } finally { if (this.pendingBackup === controller) this.pendingBackup = undefined; }
        }
      }
      catch (error) {
        // update/recalculate may have committed its validated draft before calculation failed.
        if (operation === "updateAndRecalculate") {
          const committed = await this.request<WebState>("state");
          let presented: WebState;
          try { presented = await this.persist(committed); }
          catch (storageError) {
            if (storageError instanceof ApplicationError) throw new ApplicationError(
              storageError.message, storageError.code, storageError.details, await this.present(committed));
            throw storageError;
          }
          if (error instanceof ApplicationError) throw new ApplicationError(
            error.message, error.code, error.details, presented);
        }
        throw error;
      }
      if (operation === "importRoute") return this.present(state);
      try { return await this.persist(state); }
      catch (error) {
        if ((operation === "confirmRoute" || operation === "calculate") && error instanceof ApplicationError) {
          // Python has committed the result; retain it for recovery/save retry.
          const committed = await this.present(state).catch(() => state);
          throw new ApplicationError(error.message, error.code,
            { ...error.details, operation }, committed);
        }
        throw error;
      }
    });
  }
  bootstrap(recovery?: WorkingRecovery) {
    this.invalidatePending();
    return this.serial(async () => {
      const { durableToken, repositoryRecovery: _repository, ...working } = recovery ?? {};
      if (recovery) await this.repository.restoreWorking?.(recovery);
      if (recovery?.project && durableToken) this.tokens.set(recovery.project.id, durableToken);
      const state = await this.request<WebState>("bootstrap", recovery ? working : undefined);
      return this.present(state); // never select or write durable work during session hydration
    });
  }
  importRoute(input: ImportRouteInput) { return this.execute("importRoute", input); }
  confirmRoute(input: ConfirmRouteInput) { return this.execute("confirmRoute", input); }
  updateProject(input: UpdateProjectInput) { return this.execute("updateProject", input); }
  updateAndRecalculate(input: UpdateProjectInput) { return this.execute("updateAndRecalculate", input); }
  renameRouteNode(nodeId: string, name: string) { return this.execute("renameRouteNode", { node_id: nodeId, name }); }
  replaceCheckPoints(checkPoints: CheckPointInput[]) { return this.execute("replaceCheckPoints", { check_points: checkPoints }); }
  acknowledge(key: string, checked: boolean) { return this.execute("acknowledge", { key, checked }); }
  async calculate(onProgress?: ProgressListener, options?: CalculationOptions) {
    // Report only known milestones; finer progress is deferred to #159.
    onProgress?.({ percent: 0, message: "計算を開始しています。" });
    const state = await this.execute("calculate", options && { rounding_mode: options.roundingMode }, onProgress);
    onProgress?.({ percent: 100, message: "計算が完了しました。" });
    return state;
  }
  saveProject(name: string) {
    this.invalidatePending();
    return this.serial(async () => { this.assertResolved(); return this.persist(await this.request<WebState>("state"), name); });
  }
  loadProject(projectId: string, options: LoadProjectOptions = {}) {
    this.invalidatePending();
    return this.serial(async () => {
      this.assertResolved();
      const record = await this.repository.read(projectId, false);
      const previous = await this.request<WebState>("state");
      const project = options.restoreDraft ? record.draft : record.checkpoint ?? record.draft;
      const storedChanges = !sameProjectDraft(record.draft, project);
      if (options.confirmDiscard && !options.confirmDiscard(project, storedChanges)) return null;
      const opened = !sameProjectContent(record.draft, project) ? { ...record, draft: structuredClone(project),
        token: crypto.randomUUID(), updatedAt: new Date().toISOString() } : record;
      const last = record.lastCalculation as { outcome: WorkingRecovery["outcome"]; destination_wind: WorkingRecovery["destination_wind"] } | null;
      const recovery: WorkingRecovery = { version: 1, project,
        outcome: last?.outcome ?? null, destination_wind: last?.destination_wind ?? null,
        last_calculation: record.lastCalculation, import_result: null, import_filename: null };
      let state: WebState;
      try {
        state = await this.request<WebState>("bootstrap", recovery);
        await this.repository.open(opened, record.token);
      }
      catch (error) {
        if (previous.workingRecovery) {
          const { durableToken: _token, repositoryRecovery: _repository, ...working } = previous.workingRecovery;
          await this.request("refreshSavedWork", {
            working, rounding_mode: previous.roundingMode ?? false,
          });
        }
        throw error;
      }
      this.tokens.set(record.id, opened.token);
      return this.present(state);
    });
  }
  deleteProject(projectId: string) {
    this.invalidatePending();
    return this.serial(async () => {
      const state = await this.request<WebState>("state");
      const expected = state.project?.id === projectId
        ? this.tokens.get(projectId) : this.listedTokens.get(projectId);
      await this.repository.delete(projectId, expected ?? null);
      this.tokens.delete(projectId);
      if (state.project?.id === projectId) {
        return this.present(await this.request<WebState>("bootstrap", { version: 1, project: null,
          outcome: null, destination_wind: null, import_result: null, import_filename: null }));
      }
      return this.present(state);
    });
  }
  async newWork() {
    this.invalidatePending();
    await this.serial(async () => {
      // The queue drains previous writes before replacing Python work. Account disposal
      // still terminates the worker; only this same-account boundary retains Pyodide.
      if (this.client) await this.request("newWork");
      this.tokens.clear();
      this.listedTokens.clear();
    });
  }
}
