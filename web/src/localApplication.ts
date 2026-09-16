import { accountBoundaryClosed, type AuthProvider } from "./auth";
import { ApplicationError } from "./application";
import type { AutoNavLogApplication, ImportRouteInput, ConfirmRouteInput, UpdateProjectInput, ProgressListener } from "./application";
import type { CheckPointInput, WebState, WorkingRecovery } from "./types";
import { LocalClient } from "./localClient";
import type { LocalProjectRepository, LocalProjectRecord, LocalProjectRepositoryFactory } from "./localProjectRepository";

export class LocalApplication implements AutoNavLogApplication {
  private active = true;
  private client: Pick<LocalClient, "request" | "dispose"> | undefined;
  private readonly repository: LocalProjectRepository;
  private tokens = new Map<string, string>();
  private listedTokens = new Map<string, string>();
  private queue: Promise<unknown> = Promise.resolve();
  constructor(createRepository: LocalProjectRepositoryFactory,
    private readonly createClient: () => Pick<LocalClient, "request" | "dispose"> = () => new LocalClient(),
    readonly auth?: AuthProvider) {
    this.repository = createRepository(record => this.request<LocalProjectRecord>("validateRecord", record));
  }

  dispose() {
    this.active = false;
    this.client?.dispose();
    this.client = undefined;
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
    if (state.workingRecovery && state.project) state.workingRecovery.durableToken = this.tokens.get(state.project.id) ?? null;

    const listing = await this.repository.list().catch(() => null);
    if (!listing) {
      state.savedProjects = [];
      state.storageWarning = "端末の保存データにアクセスできません。ブラウザの保存設定を確認してください。";
      return state;
    }
    this.listedTokens = new Map(Object.entries(listing.tokens ?? {}));
    state.savedProjects = listing.projects;
    state.storageWarning = listing.unavailable.length
      ? "一部のProjectを読み込めません。元データは保持され、他のProjectは利用できます。" : undefined;
    return state;
  }
  private async persist(state: WebState, name?: string): Promise<WebState> {
    const working = state.workingRecovery;
    if (!working?.project) {
      if (name !== undefined) throw new ApplicationError("保存するProjectがありません。", "PROJECT_REQUIRED");
      return this.present(state);
    }
    const draft = structuredClone(working.project);
    const expected = this.tokens.get(draft.id) ?? null;
    const existing = expected ? await this.repository.read(draft.id) : undefined;
    if (existing && existing.draft.revision !== draft.revision) throw new ApplicationError(
      "別のタブでProjectが保存されています。開き直してください。", "PROJECT_REVISION_CONFLICT");
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
      const { durableToken: _token, ...recovery } = working;
      state = await this.request<WebState>("bootstrap", { ...recovery, project: draft });
    }
    return this.present(state);
  }
  private execute(operation: string, input?: unknown): Promise<WebState> {
    return this.serial(async () => {
      let state: WebState;
      try { state = await this.request<WebState>(operation, input); }
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
      return operation === "importRoute" ? this.present(state) : this.persist(state);
    });
  }
  bootstrap(recovery?: WorkingRecovery) {
    return this.serial(async () => {
      const { durableToken, ...working } = recovery ?? {};
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
  async calculate(onProgress?: ProgressListener) {
    // Report only known milestones; finer progress is deferred to #159.
    onProgress?.({ percent: 0, message: "計算を開始しています。" });
    const state = await this.execute("calculate");
    onProgress?.({ percent: 100, message: "計算が完了しました。" });
    return state;
  }
  saveProject(name: string) {
    return this.serial(async () => this.persist(await this.request<WebState>("state"), name));
  }
  loadProject(projectId: string) {
    return this.serial(async () => {
      const record = await this.repository.read(projectId);
      const previous = await this.request<WebState>("state");
      const last = record.lastCalculation as { outcome: WorkingRecovery["outcome"]; destination_wind: WorkingRecovery["destination_wind"] } | null;
      const recovery: WorkingRecovery = { version: 1, project: record.draft,
        outcome: last?.outcome ?? null, destination_wind: last?.destination_wind ?? null,
        last_calculation: record.lastCalculation, import_result: null, import_filename: null };
      let state: WebState;
      try {
        state = await this.request<WebState>("bootstrap", recovery);
        await this.repository.open(record);
      }
      catch (error) {
        if (previous.workingRecovery) {
          const { durableToken: _token, ...working } = previous.workingRecovery;
          await this.request("bootstrap", working);
        }
        throw error;
      }
      this.tokens.set(record.id, record.token);
      return this.present(state);
    });
  }
  deleteProject(projectId: string) {
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
    await this.serial(async () => {
      this.client?.dispose();
      this.client = undefined;
      this.tokens.clear();
      this.listedTokens.clear();
    });
  }
}
