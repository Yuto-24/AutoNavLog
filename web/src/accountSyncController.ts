import type { AccountProjectRepository } from "./accountProjectRepository";
import type { LocalAccountContext, LocalProjectRecord } from "./localProjectRepository";
import type { AccountSyncControl, AccountSyncRepository, ConflictChoice, SyncStatus } from "./accountSync";

export class AccountSyncController implements AccountSyncControl {
  private state: SyncStatus = { conflicts: [], imports: [], undo: [], generation: 0 };
  private listeners = new Set<() => void>();
  private remote?: AccountSyncRepository;
  private unsubscribe?: () => void;
  private running = false;
  private active = true;
  private initialized = false;
  private remoteInitialized = false;
  private remoteChanged = true;
  private subscriptionFailed = false;
  private imported: LocalProjectRecord[] = [];
  private failures = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private statusTimer: ReturnType<typeof setInterval>;
  private again = false;
  constructor(private readonly local: AccountProjectRepository,
    private readonly context: LocalAccountContext,
    private readonly createRemote: () => Promise<AccountSyncRepository>,
    private readonly importAnonymous: () => Promise<LocalProjectRecord[]>) {
    local.changed = () => { void this.publish(); this.wake(); };
    this.statusTimer = setInterval(() => { void this.publish(); }, 1000);
    globalThis.addEventListener?.("online", this.wake);
    globalThis.addEventListener?.("focus", this.wake);
    this.wake();
  }
  getState = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private async publish(notice = this.state.notice, force = false) {
    try {
      const status = await this.local.status();
      this.context.assertActive();
      if (!this.active) return;
      const imports = (await this.local.missingImports(this.imported)).map(row => ({ id: row.id, name: row.draft.name || "Latest" }));
      if (!force && JSON.stringify({ ...status, imports, notice }) === JSON.stringify({ conflicts: this.state.conflicts, undo: this.state.undo, imports: this.state.imports, notice: this.state.notice })) return;
      this.state = { ...status, imports, notice, generation: this.state.generation + 1 };
      for (const listener of this.listeners) listener();
    } catch { /* context closure cannot republish account data */ }
  }
  private failed = (error: unknown) => {
    if (!this.active) return;
    this.failures++;
    const code = (error as { code?: string })?.code ?? "";
    const terminal = /permission-denied|unauthenticated|resource-exhausted|SYNC_DATA_INVALID|invalid-argument/.test(code);
    const offline = globalThis.navigator?.onLine === false;
    if (terminal || (this.failures >= 5 && !offline)) {
      void this.publish(/unauthenticated/.test(code)
        ? "他の端末に変更を同期できません。Googleアカウントで再ログインしてください。"
        : "他の端末に変更を同期できていません。この端末の保存データは保持されています。時間をおいても続く場合は接続と保存容量を確認してください。");
    }
  };
  wake = () => {
    if (!this.active) return;
    if (this.running) { this.again = true; return; }
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.run(); }, 250);
  };
  async run() {
    if (!this.active || this.running) return;
    this.running = true; this.again = false;
    try {
      if (!this.initialized) {
        this.imported = await this.importAnonymous();
        await this.local.initialize(this.imported, true);
        await this.publish();
        this.initialized = true;
      }
      this.context.assertActive();
      if (this.subscriptionFailed) {
        this.unsubscribe?.(); this.remote?.dispose(); this.remote = undefined;
        this.subscriptionFailed = false;
      }
      if (!this.remote) {
        this.remote = await this.createRemote();
        this.context.assertActive();
        this.unsubscribe = this.remote.subscribe(() => { this.remoteChanged = true; this.wake(); }, error => { this.remoteChanged = true; this.subscriptionFailed = true; this.failed(error); this.wake(); });
      }
      // Publish local outbox first: a previous remote success with a lost ACK is
      // safely replayed before interpreting newer snapshots as conflicts.
      for (const mutations of await this.local.pending()) {
        if (!await this.local.markSending(mutations)) { this.again = true; continue; }
        const result = await this.remote.commit(mutations);
        this.context.assertActive();
        if (result.committed) await this.local.acknowledge(mutations);
        else { await this.local.receive(result.conflicts); this.remoteChanged = true; }
      }
      if (this.remoteChanged) {
        this.remoteChanged = false;
        try { await this.local.receive(await this.remote.list()); }
        catch (error) { this.remoteChanged = true; throw error; }
      }
      if (!this.remoteInitialized) {
        // A returning device may have an existing Latest only in the cloud.
        // Keep the claimed anonymous originals durable until this union is known.
        await this.local.confirmImports();
        this.remoteInitialized = true;
        this.again = true;
      }
      this.failures = 0;
      await this.publish("", true);
    } catch (error) { this.failed(error); }
    finally {
      this.running = false;
      if (!this.active) this.remote?.dispose();
      if (this.active) this.timer = setTimeout(() => { void this.run(); }, this.again ? 250 : Math.min(60_000, 15_000 * Math.max(1, this.failures)));
    }
  }
  async resolve(id: string, choice: ConflictChoice) {
    const selected = await this.local.resolve(id, choice);
    await this.publish(); this.wake(); return selected;
  }
  async importLatest(id: string, name: string | null) {
    const record = this.imported.find(row => row.id === id);
    if (!record) return;
    await this.local.importLatest(record, name);
    await this.publish(); this.wake();
  }
  async undo(id: string) { await this.local.undo(id); await this.publish(); this.wake(); }
  dispose() {
    this.active = false; clearTimeout(this.timer); clearInterval(this.statusTimer);
    globalThis.removeEventListener?.("online", this.wake);
    globalThis.removeEventListener?.("focus", this.wake);
    this.unsubscribe?.(); this.remote?.dispose(); this.listeners.clear();
  }
}
