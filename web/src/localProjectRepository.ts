import { evictWeatherCache } from "./browserStorage";
import { ApplicationError } from "./application";
import type { Project, SavedProject, WorkingRecovery } from "./types";

export interface LocalProjectRecord {
  schemaVersion: 2;
  id: string;
  token: string;
  checkpoint: Project | null;
  draft: Project;
  lastCalculation: WorkingRecovery["last_calculation"];
  updatedAt: string;
}
export interface LocalProjectRepository {
  list(): Promise<{ projects: SavedProject[]; unavailable: string[]; tokens: Record<string, string> }>;
  read(id: string): Promise<LocalProjectRecord>;
  write(record: LocalProjectRecord, expectedToken: string | null, replaceLatest: boolean): Promise<void>;
  open(record: LocalProjectRecord): Promise<void>;
  delete(id: string, expectedToken: string | null): Promise<void>;
}
export interface LocalAccountContext { account_id: string | null; assertActive(): void; onClose(listener: () => void): () => void }
export type LocalProjectRepositoryFactory = (
  validate: (record: unknown) => Promise<LocalProjectRecord>, context?: LocalAccountContext,
) => LocalProjectRepository;
export const LOCAL_DATABASE = "autonavlog.projects";
export const storageFailure = () => new ApplicationError(
  "端末への保存に失敗しました。空き容量やブラウザの保存設定を確認して再試行してください。", "LOCAL_STORAGE_FAILED");
// Compare only transaction identity/version markers. Invalid structured-clone payloads
// (for example cyclic objects or BigInt) must not poison other Projects' writes.
const rowVersions = (rows: unknown[]) => JSON.stringify(rows.map(raw => {
  const row = raw as Partial<LocalProjectRecord>;
  return [String(row.id), typeof row.token === "string" ? row.token : null, row.checkpoint === null];
}));
const conflict = () => new ApplicationError(
  "別のタブでProjectが更新または削除されています。編集内容を確認してから開き直してください。", "PROJECT_REVISION_CONFLICT");
const unavailable = (id: string) => new ApplicationError(
  "このProjectの保存データを読み込めません。元データは保持されています。", "LOCAL_PROJECT_UNAVAILABLE", { projectId: id });

export class IndexedDbProjectRepository implements LocalProjectRepository {
  constructor(
    private readonly validate: (record: unknown) => Promise<LocalProjectRecord>,
    private readonly evict: () => Promise<void> = evictWeatherCache,
    private readonly factory: () => IDBFactory = () => indexedDB,
    private readonly context?: LocalAccountContext,
  ) {}

  private database(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      const request = this.factory().open(this.context?.account_id ? `${LOCAL_DATABASE}.${this.context.account_id}` : LOCAL_DATABASE, 1);
      let blocked = false;
      request.onupgradeneeded = () => request.result.createObjectStore("projects", { keyPath: "id" });
      request.onerror = () => reject(request.error);
      request.onblocked = () => { blocked = true; reject(storageFailure()); };
      request.onsuccess = () => {
        if (blocked) { request.result.close(); return; }
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
    });
  }

  private async transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore, rows: unknown[]) => T): Promise<T> {
    this.context?.assertActive();
    const db = await this.database();
    try {
      this.context?.assertActive();
      return await new Promise<T>((resolve, reject) => {
        const tx = db.transaction("projects", mode);
        let value: T, error: unknown;
        const unsubscribe = this.context?.onClose(() => {
          try { tx.abort(); } catch { /* already committed/aborted */ }
        });
        tx.oncomplete = () => {
          unsubscribe?.();
          try { this.context?.assertActive(); resolve(value); } catch (error) { reject(error); }
        };
        tx.onabort = () => {
          unsubscribe?.();
          try { this.context?.assertActive(); reject(error ?? tx.error ?? storageFailure()); }
          catch (cause) { reject(cause); }
        };
        tx.onerror = () => { /* abort reports the transaction failure, never request success */ };
        const store = tx.objectStore("projects");
        const read = store.getAll();
        read.onsuccess = () => {
          try { this.context?.assertActive(); value = action(store, read.result); }
          catch (cause) { error = cause; tx.abort(); }
        };
      });
    } finally { db.close(); }
  }
  private async safe<T>(operation: () => Promise<T>, retryQuota = false): Promise<T> {
    try { return await operation(); }
    catch (error) {
      if (retryQuota && error instanceof DOMException && error.name === "QuotaExceededError") {
        try { await this.evict(); return await operation(); }
        catch (retryError) { if (retryError instanceof ApplicationError) throw retryError; }
      }
      if (error instanceof ApplicationError) throw error;
      throw storageFailure();
    }
  }
  async list() {
    const rows = await this.safe(() => this.transaction("readonly", (_store, rows) => rows));
    const projects: SavedProject[] = [], failed: string[] = [];
    const tokens: Record<string, string> = {};
    for (const raw of rows) {
      const id = String((raw as { id?: unknown })?.id ?? "unknown");
      try {
        const record = await this.validate(raw);
        this.context?.assertActive();
        tokens[record.id] = record.token;
        projects.push({ id: record.id, kind: record.checkpoint ? "SAVED" : "LATEST",
          name: record.checkpoint?.name ?? "Latest", status: record.draft.status,
          revision: record.draft.revision, updatedAt: record.updatedAt });
      } catch { this.context?.assertActive(); failed.push(id); }
    }
    this.context?.assertActive();
    projects.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    return { projects, unavailable: failed, tokens };
  }
  async read(id: string) {
    const raw = await this.safe(() => this.transaction("readonly", (_store, rows) => rows.find(row => (row as { id: string }).id === id)));
    if (!raw) throw unavailable(id);
    try {
      const record = await this.validate(raw);
      this.context?.assertActive();
      return record;
    } catch { this.context?.assertActive(); throw unavailable(id); }
  }
  private async latestTokens() {
    const rows = await this.safe(() => this.transaction("readonly", (_store, rows) => rows));
    const latest = new Map<string, string>();
    for (const row of rows) {
      try {
        const record = await this.validate(row);
        if (!record.checkpoint) latest.set(record.id, record.token);
      } catch { /* keep unavailable records intact */ }
    }
    return { latest, snapshot: rowVersions(rows) };
  }
  async write(record: LocalProjectRecord, expectedToken: string | null, replaceLatest: boolean) {
    const valid = await this.validate(record);
    const candidates = replaceLatest ? await this.latestTokens() : undefined;
    await this.safe(() => this.transaction("readwrite", (store, rows) => {
      if (candidates && rowVersions(rows) !== candidates.snapshot) throw conflict();
      const existing = rows.find(row => (row as LocalProjectRecord).id === valid.id) as LocalProjectRecord | undefined;
      if (expectedToken === null ? existing !== undefined : existing?.token !== expectedToken) throw conflict();
      store.put(valid);
      if (replaceLatest) for (const row of rows as LocalProjectRecord[]) {
        // Never infer deletion targets from malformed records.
        if (row.id !== valid.id && candidates?.latest.has(row.id) && candidates.latest.get(row.id) === row.token) store.delete(row.id);
      }
    }), true);
  }
  async open(record: LocalProjectRecord) {
    const candidates = await this.latestTokens();
    await this.safe(() => this.transaction("readwrite", (store, rows) => {
      if (rowVersions(rows) !== candidates.snapshot) throw conflict();
      const current = rows.find(row => (row as LocalProjectRecord).id === record.id) as LocalProjectRecord | undefined;
      if (!current || current.token !== record.token) throw conflict();
      store.put(record); // migration is committed only after domain validation succeeded
      for (const row of rows as LocalProjectRecord[]) {
        if (row.id !== record.id && candidates?.latest.has(row.id) && candidates.latest.get(row.id) === row.token) store.delete(row.id);
      }
    }), true);
  }
  async delete(id: string, expectedToken: string | null) {
    await this.safe(() => this.transaction("readwrite", (store, rows) => {
      const current = rows.find(row => (row as LocalProjectRecord).id === id) as LocalProjectRecord | undefined;
      if ((current?.token ?? null) !== expectedToken) throw conflict();
      store.delete(id);
    }));
  }
}
