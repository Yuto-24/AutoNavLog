import { ApplicationError } from "./application";
import { IndexedDbProjectRepository, type LocalProjectRecord, type LocalAccountContext } from "./localProjectRepository";
import { deletionState, type SyncProject, type SyncMutation, type ConflictChoice } from "./accountSync";
import type { SavedProject, WorkingRecovery } from "./types";

interface SyncMetadata {
  baseVersion: string | null;
  baseRevision: number;
  value: SyncProject;
  dirty: boolean;
  conflict?: SyncProject;
  group: string;
  sent?: SyncMutation;
  resolutionCopyId?: string;
  importPending?: boolean;
}
type Row = LocalProjectRecord & { sync?: SyncMetadata };
const conflict = () => new ApplicationError("他の端末の変更と競合しています。内容を選択してください。", "PROJECT_REVISION_CONFLICT");
const unavailable = () => new ApplicationError("このProjectは利用できません。", "LOCAL_PROJECT_UNAVAILABLE");
const signature = (rows: Row[]) => JSON.stringify(rows.map(row => [
  row.id, row.token, row.sync?.baseVersion, row.sync?.value?.version,
  row.sync?.conflict?.version, row.sync?.dirty, row.sync?.group, row.sync?.sent?.value.version,
]));

// Account namespace keeps the existing #124 record and transaction store. Sync
// metadata is outside the domain record and is never passed to Python or UI.
export class AccountProjectRepository extends IndexedDbProjectRepository {
  changed: () => void = () => {};
  private loaded = new Map<string, Row>();
  constructor(validate: (raw: unknown) => Promise<LocalProjectRecord>, context: LocalAccountContext,
    readonly deviceId: string, private readonly cleanCalculation: (record: LocalProjectRecord, copyId?: string) => Promise<LocalProjectRecord>,
    factory?: () => IDBFactory) {
    super(validate, undefined, factory, context);
  }
  private row(record: LocalProjectRecord, previous?: Row): Row {
    return { ...record, sync: { baseVersion: previous?.sync?.baseVersion ?? null, baseRevision: previous?.sync?.baseRevision ?? 0,
      value: { id: record.id, version: crypto.randomUUID(), revision: (previous?.sync?.baseRevision ?? 0) + 1, record,
        latestDeviceId: record.checkpoint ? null : this.deviceId, deletion: "ACTIVE", undoUntil: null },
      dirty: true, group: crypto.randomUUID(), sent: previous?.sync?.sent, importPending: previous?.sync?.importPending } };
  }
  private async rows(): Promise<Row[]> {
    const raw = await this.safe(() => this.transaction("readonly", (_store, rows) => rows as Row[]));
    return raw;
  }
  private async decode(row: Row): Promise<LocalProjectRecord> {
    const { sync: _sync, ...record } = row;
    const valid = await this.validate(record);
    this.context?.assertActive();
    return valid;
  }
  private async change<T>(action: (store: IDBObjectStore, rows: Row[]) => T) {
    return this.safe(() => this.transaction("readwrite", (store, rows) => action(store, rows as Row[])), true);
  }
  async initialize(imported: LocalProjectRecord[], stageLatest = false) {
    // Existing #184 account copies enter the outbox without changing their tokens.
    const originals = await this.rows();
    const valid = new Map<string, LocalProjectRecord>();
    for (const row of originals) {
      if (!row.sync) try { valid.set(row.id, await this.decode(row)); } catch { /* preserve corruption */ }
    }
    await this.change((store, rows) => {
      if (signature(originals) !== signature(rows)) throw conflict();
      const ids = new Set(rows.map(row => row.id));
      for (const row of rows) if (!row.sync && valid.has(row.id)) store.put(this.row(valid.get(row.id)!));
      for (const record of imported) if (!ids.has(record.id)) {
        if (!stageLatest && !record.checkpoint && rows.some(row => !row.checkpoint && (!row.sync || (row.sync.value.latestDeviceId === this.deviceId && deletionState(row.sync.value) === "ACTIVE")))) continue;
        const next = this.row(record);
        next.sync!.importPending = stageLatest && !record.checkpoint;
        store.put(next); ids.add(record.id);
      }
    });
  }
  async list() {
    const projects: SavedProject[] = [], unavailable: string[] = [], tokens: Record<string, string> = {};
    const rows = await this.rows();
    for (const row of rows) {
      if (row.sync?.importPending && this.importCollision(row, rows)) continue;
      if (row.sync && deletionState(row.sync.value) !== "ACTIVE") continue;
      try {
        const record = await this.decode(row);
        tokens[record.id] = record.token;
        projects.push({ id: record.id, kind: record.checkpoint ? "SAVED" : "LATEST",
          name: record.checkpoint?.name ?? "Latest", revision: record.draft.revision,
          status: record.draft.status, updatedAt: record.updatedAt });
      } catch { this.context?.assertActive(); unavailable.push(row.id); }
    }
    projects.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    return { projects, unavailable, tokens };
  }
  async read(id: string) {
    const row = (await this.rows()).find(row => row.id === id);
    if (!row || (row.sync && deletionState(row.sync.value) !== "ACTIVE")) throw unavailable();
    const record = await this.decode(row);
    this.loaded.set(id, structuredClone(row));
    return record;
  }
  async readWorking(id: string, token: string) {
    const loaded = this.loaded.get(id);
    return loaded?.token === token ? this.decode(loaded) : this.read(id);
  }
  async captureWorking(id: string, token: string) {
    const current = (await this.rows()).find(row => row.id === id && row.token === token &&
      (!row.sync || deletionState(row.sync.value) === "ACTIVE"));
    const row = current ?? this.loaded.get(id);
    if (!row || row.token !== token || !row.sync) return undefined;
    return { accountId: this.context!.account_id, token, checkpoint: row.checkpoint,
      baseVersion: row.sync.baseVersion, baseRevision: row.sync.baseRevision };
  }
  async restoreWorking(recovery: WorkingRecovery) {
    const { project, durableToken, repositoryRecovery } = recovery;
    if (!project || !durableToken) return;
    const current = (await this.rows()).find(row => row.id === project.id);
    if (current?.token === durableToken && current.sync && deletionState(current.sync.value) === "ACTIVE" &&
      (!repositoryRecovery || current.sync.baseVersion === (repositoryRecovery as { baseVersion?: unknown }).baseVersion)) {
      this.loaded.set(project.id, current); return;
    }
    const base = repositoryRecovery as { accountId?: string; token?: string; checkpoint?: LocalProjectRecord["checkpoint"]; baseVersion?: string | null; baseRevision?: number } | undefined;
    if (!base || base.accountId !== this.context!.account_id || base.token !== durableToken ||
      !(base.baseVersion === null || typeof base.baseVersion === "string") || base.checkpoint === undefined || !Number.isSafeInteger(base.baseRevision)) return;
    const record = await this.validate({ schemaVersion: 2, id: project.id, token: durableToken,
      draft: project, checkpoint: base.checkpoint, lastCalculation: recovery.last_calculation ?? null,
      updatedAt: project.updated_at });
    this.context!.assertActive();
    const row = this.row(record);
    row.sync!.baseVersion = base.baseVersion; row.sync!.baseRevision = base.baseRevision!;
    this.loaded.set(project.id, row);
  }
  private discardLatest(store: IDBObjectStore, rows: Row[], except: string, group: string) {
    for (const row of rows) if (row.id !== except && !row.checkpoint && row.sync &&
      row.sync.value.latestDeviceId === this.deviceId && deletionState(row.sync.value) === "ACTIVE") {
      if (row.sync.conflict) throw conflict();
      const next = structuredClone(row);
      next.sync!.value.deletion = "DELETED";
      next.sync!.value.version = crypto.randomUUID(); next.sync!.value.revision = next.sync!.baseRevision + 1;
      next.sync!.dirty = true; next.sync!.group = group;
      store.put(next);
    }
  }
  async write(record: LocalProjectRecord, expectedToken: string | null, replaceLatest: boolean) {
    const valid = await this.validate(record);
    await this.change((store, rows) => {
      if (rows.some(row => row.sync?.conflict)) throw conflict();
      const old = rows.find(row => row.id === record.id);
      const loaded = this.loaded.get(record.id);
      const remoteChanged = loaded?.token === expectedToken && (old?.token !== expectedToken || (old?.sync && deletionState(old.sync.value) !== "ACTIVE")) && old?.sync && !old.sync.dirty && loaded.sync?.baseVersion !== old.sync.baseVersion;
      if (!remoteChanged && ((old?.token ?? null) !== expectedToken || (old?.sync && deletionState(old.sync.value) !== "ACTIVE"))) throw conflict();
      const next = this.row(valid, remoteChanged ? loaded : old);
      if (remoteChanged) next.sync!.conflict = old.sync!.value;
      // Keep earlier atomic local operations in the same remote commit.
      if (old?.sync?.dirty) next.sync!.group = old.sync.group;
      if (replaceLatest) this.discardLatest(store, rows, record.id, next.sync!.group);
      store.put(next);
    });
    this.loaded.set(record.id, (await this.rows()).find(row => row.id === record.id)!);
    this.changed();
  }
  async open(record: LocalProjectRecord) {
    await this.change((store, rows) => {
      if (rows.some(row => row.sync?.conflict)) throw conflict();
      const old = rows.find(row => row.id === record.id);
      if (!old || old.token !== record.token || (old.sync && deletionState(old.sync.value) !== "ACTIVE")) throw conflict();
      this.discardLatest(store, rows, record.id, crypto.randomUUID());
    });
    this.changed();
  }
  async delete(id: string, expectedToken: string | null) {
    await this.change((store, rows) => {
      if (rows.some(row => row.sync?.conflict)) throw conflict();
      const row = rows.find(row => row.id === id);
      if (!row || row.token !== expectedToken || !row.sync || deletionState(row.sync.value) !== "ACTIVE") throw conflict();
      const next = structuredClone(row);
      next.sync!.value = { ...next.sync!.value, deletion: "PENDING_DELETE", undoUntil: Date.now() + 10_000, version: crypto.randomUUID(), revision: next.sync!.baseRevision + 1 };
      next.sync!.dirty = true;
      store.put(next);
    });
    this.changed();
  }
  async undo(id: string) {
    await this.change((store, rows) => {
      const row = rows.find(row => row.id === id);
      if (!row?.sync || deletionState(row.sync.value) !== "PENDING_DELETE" || row.sync.conflict) throw unavailable();
      const next = structuredClone(row);
      next.sync!.value = { ...next.sync!.value, deletion: "ACTIVE", undoUntil: null, version: crypto.randomUUID(), revision: next.sync!.baseRevision + 1 };
      next.sync!.dirty = true; store.put(next);
    });
  }
  async status() {
    const rows = await this.rows();
    return { conflicts: rows.filter(row => row.sync?.conflict).map(row => ({ id: row.id, name: row.draft.name || "Latest" })),
      undo: rows.filter(row => row.sync && deletionState(row.sync.value) === "PENDING_DELETE").map(row => ({ id: row.id, name: row.draft.name || "Latest", until: row.sync!.value.undoUntil! })) };
  }
  private importCollision(row: Row, rows: Row[]) {
    return rows.some(other => other.id !== row.id && !other.checkpoint && other.sync &&
      !other.sync.importPending && other.sync.value.latestDeviceId === this.deviceId &&
      deletionState(other.sync.value) === "ACTIVE");
  }
  async confirmImports() {
    await this.change((store, rows) => {
      for (const row of rows) if (row.sync?.importPending &&
        (deletionState(row.sync.value) !== "ACTIVE" || row.checkpoint || !this.importCollision(row, rows))) {
        store.put({ ...row, sync: { ...row.sync, importPending: false } });
      }
    });
  }
  async importLatest(record: LocalProjectRecord, name: string | null) {
    const snapshot = (await this.rows()).find(row => row.id === record.id);
    if (snapshot && !snapshot.sync?.importPending) return;
    const copy = snapshot ? await this.decode(snapshot) : structuredClone(record);
    if (name !== null) {
      copy.draft.name = name.trim().slice(0, 60) || "route";
      copy.draft.revision += 1;
      copy.draft.updated_at = new Date().toISOString();
      copy.draft.metadata.project_name_auto = false;
      copy.checkpoint = structuredClone(copy.draft);
      copy.token = crypto.randomUUID();
    }
    const valid = await this.validate(copy);
    await this.change((store, rows) => {
      const current = rows.find(row => row.id === valid.id);
      if (current?.token !== snapshot?.token || current?.sync?.importPending !== snapshot?.sync?.importPending) throw conflict();
      const next = this.row(valid);
      if (name === null) next.sync!.value.deletion = "DELETED";
      store.put(next);
    });
    this.changed();
  }
  async missingImports(imported: LocalProjectRecord[]) {
    const rows = await this.rows(), ids = new Set(rows.map(row => row.id));
    return imported.filter(record => !ids.has(record.id) ||
      rows.some(row => row.id === record.id && row.sync?.importPending && this.importCollision(row, rows)));
  }
  async pending(): Promise<SyncMutation[][]> {
    await this.change((store, rows) => {
      for (const row of rows) if (row.sync && !row.sync.conflict && !row.sync.sent &&
        row.sync.value.deletion === "PENDING_DELETE" && deletionState(row.sync.value) === "DELETED") {
        store.put({ ...row, sync: { ...row.sync, dirty: true, value: {
          ...row.sync.value, deletion: "DELETED", version: crypto.randomUUID(), revision: row.sync.baseRevision + 1,
        } } });
      }
    });
    const groups = new Map<string, SyncMutation[]>();
    const rows = await this.rows();
    const blocked = new Set(rows.filter(row => row.sync?.conflict || row.sync?.importPending).map(row => row.sync!.group));
    for (const row of rows) if (row.sync?.dirty && !blocked.has(row.sync.group)) {
      const group = groups.get(row.sync.group) ?? [];
      group.push(row.sync.sent ?? { expectedVersion: row.sync.baseVersion, value: row.sync.value }); groups.set(row.sync.group, group);
    }
    return [...groups.values()];
  }
  async markSending(mutations: SyncMutation[]): Promise<boolean> {
    return this.change((store, rows) => {
      if (mutations.some(mutation => {
        const sync = rows.find(row => row.id === mutation.value.id)?.sync;
        return !sync || !sync.dirty || sync.conflict || sync.importPending ||
          sync.baseVersion !== mutation.expectedVersion ||
          (sync.sent?.value.version ?? sync.value.version) !== mutation.value.version;
      })) return false;
      for (const mutation of mutations) {
        const row = rows.find(row => row.id === mutation.value.id)!;
        store.put({ ...row, sync: { ...row.sync!, sent: mutation } });
      }
      return true;
    });
  }
  async receive(remote: SyncProject[]) {
    const validated: SyncProject[] = [];
    for (const value of remote) {
      const record = await this.validate(value.record);
      if (value.id !== record.id) throw unavailable();
      validated.push({ ...value, record });
    }
    await this.change((store, rows) => {
      for (const value of validated) {
        const old = rows.find(row => row.id === value.id);
        if (old?.sync && value.revision <= old.sync.baseRevision) continue;
        if (old?.sync?.dirty) {
          // A committed write whose acknowledgement was lost is recognized by its
          // idempotency version. Newer local edits keep their original base.
          if (old.sync.value.version === value.version) {
            store.put({ ...old, sync: { ...old.sync, baseVersion: value.version, baseRevision: value.revision, dirty: false, conflict: undefined, sent: undefined } });
          } else if (old.sync.sent?.value.version === value.version) {
            store.put({ ...old, sync: { ...old.sync, baseVersion: value.version, baseRevision: value.revision, value: { ...old.sync.value, revision: value.revision + 1 }, sent: undefined, conflict: undefined } });
          } else if (old.sync.baseVersion !== value.version) {
            store.put({ ...old, sync: { ...old.sync, conflict: value, sent: undefined } });
          }
        } else if (!old || old.sync?.baseVersion !== value.version) {
          store.put({ ...value.record, sync: { baseVersion: value.version, baseRevision: value.revision, value, dirty: false, group: crypto.randomUUID() } });
        }
      }
    });
  }
  async acknowledge(mutations: SyncMutation[]) {
    await this.change((store, rows) => {
      for (const mutation of mutations) {
        const old = rows.find(row => row.id === mutation.value.id);
        if (!old?.sync) continue;
        // Editing while a network request is outstanding remains Local-first.
        if (old.sync.baseVersion !== mutation.expectedVersion) continue;
        store.put({ ...old, sync: { ...old.sync, baseVersion: mutation.value.version, baseRevision: mutation.value.revision,
          value: { ...old.sync.value, revision: mutation.value.revision + (old.sync.value.version !== mutation.value.version ? 1 : 0) },
          dirty: old.sync.value.version !== mutation.value.version, conflict: undefined, sent: undefined } });
      }
    });
  }
  async resolve(id: string, choice: ConflictChoice): Promise<string> {
    const snapshot = await this.rows();
    const old = snapshot.find(row => row.id === id);
    if (!old?.sync?.conflict) throw conflict();
    const remote = old.sync.conflict;
    const stagedCopy = snapshot.find(row => row.id === old.sync!.resolutionCopyId && row.sync?.dirty && row.sync.baseVersion === null);
    const local = await this.cleanCalculation(await this.decode(stagedCopy ?? old), stagedCopy ? id : undefined);
    const remoteRecord = await this.cleanCalculation(remote.record);
    const copyId = choice === "both" ? stagedCopy?.id ?? crypto.randomUUID() : id;
    const copy = choice === "both" ? await this.cleanCalculation(local, copyId) : local;
    await this.change((store, rows) => {
      if (signature(snapshot) !== signature(rows)) throw conflict();
      const group = crypto.randomUUID();
      if (stagedCopy) store.delete(stagedCopy.id);
      for (const row of rows) if (row.id !== id && row.id !== stagedCopy?.id && row.sync?.dirty && row.sync.group === old.sync!.group) {
        store.put({ ...row, sync: { ...row.sync, group, sent: undefined } });
      }
      // Adopting a deleted remote never resurrects its identity. Local content
      // can still be preserved with 'both'.
      if (choice === "local" && deletionState(remote) === "DELETED") throw new ApplicationError("同期先では削除済みです。両方残すでこの端末の内容を保存してください。", "PROJECT_DELETED");
      const originalRecord = { ...(choice === "local" ? local : remoteRecord), token: crypto.randomUUID() };
      if (choice === "local") {
        originalRecord.draft.revision = Math.max(local.draft.revision, remoteRecord.draft.revision) + 1;
        if (originalRecord.checkpoint) originalRecord.checkpoint.revision = originalRecord.draft.revision;
      }
      const value: SyncProject = { ...(choice === "local" ? old.sync!.value : remote), record: originalRecord, version: crypto.randomUUID(), revision: remote.revision + 1 };
      store.put({ ...originalRecord, sync: { baseVersion: remote.version, baseRevision: remote.revision, value, dirty: true, group, resolutionCopyId: choice === "both" ? copyId : undefined } });
      if (choice === "both") {
        const newRow = this.row(copy);
        // A retained conflict copy is a normal saved Project, never a second Latest.
        newRow.checkpoint = structuredClone(copy.draft);
        newRow.sync!.value.record = { ...copy, checkpoint: newRow.checkpoint };
        newRow.sync!.value.latestDeviceId = null;
        newRow.sync!.group = group;
        store.put(newRow);
      }
    });
    return copyId;
  }
}
