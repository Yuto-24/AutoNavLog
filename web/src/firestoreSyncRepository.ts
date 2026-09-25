import { getApp } from "firebase/app";
import { getAuth } from "firebase/auth";
import { collection, doc, getDocsFromServer, getFirestore, onSnapshot, runTransaction,
  type Firestore, type DocumentData } from "firebase/firestore";
import { ApplicationError } from "./application";
import { accountBoundaryClosed } from "./auth";
import { deletionState, type AccountSyncRepository, type SyncProject, type SyncMutation, type SyncCommit } from "./accountSync";
import type { LocalAccountContext } from "./localProjectRepository";

const invalid = () => new ApplicationError("他の端末の保存データを読み込めません。端末内のデータは保持されています。", "SYNC_DATA_INVALID");
function decode(data: DocumentData, id: string): SyncProject {
  if (data.schema !== 1 || typeof data.payload !== "string" || typeof data.version !== "string" || !Number.isSafeInteger(data.revision) || data.revision < 1 ||
    !["ACTIVE", "PENDING_DELETE", "DELETED"].includes(data.deletion)) {
    throw invalid();
  }
  let record;
  try { record = JSON.parse(data.payload); } catch { throw invalid(); }
  if (data.id !== id || !record || typeof record !== "object" || record.id !== id) throw invalid();
  return { id: data.id, version: data.version, revision: data.revision, record, latestDeviceId: data.latestDeviceId,
    deletion: data.deletion, undoUntil: data.undoUntil };
}
export async function createFirestoreSyncRepository(context: LocalAccountContext): Promise<AccountSyncRepository> {
  const auth = getAuth(getApp());
  const user = auth.currentUser;
  if (!user || !context.account_id) throw accountBoundaryClosed();
  const subject = user.providerData.find(identity => identity.providerId === "google.com")!.uid;
  const assert = () => {
    context.assertActive();
    const current = auth.currentUser;
    if (current?.uid !== user.uid || current.providerData.find(identity => identity.providerId === "google.com")?.uid !== subject) throw accountBoundaryClosed();
  };
  return new FirestoreSyncRepository(getFirestore(getApp()), subject, assert, context.account_id);
}
export class FirestoreSyncRepository implements AccountSyncRepository {
  private active = true;
  private readonly projects;
  constructor(private readonly db: Firestore, subject: string, private readonly assertContext: () => void,
    accountId?: string) {
    // Backend key is verified by Rules against the authenticated Google subject.
    // Internal account_id remains independent of provider paths/UIDs.
    this.projects = accountId?.startsWith("account_v2_")
      ? collection(db, "googleAccounts", subject, "generations", accountId, "projects")
      : collection(db, "googleAccounts", subject, "projects");
  }
  private assert() { this.assertContext(); if (!this.active) throw accountBoundaryClosed(); }
  async list() {
    this.assert();
    const snapshot = await getDocsFromServer(this.projects);
    this.assert();
    return snapshot.docs.map(row => decode(row.data(), row.id));
  }
  async commit(mutations: SyncMutation[]): Promise<SyncCommit> {
    this.assert();
    return runTransaction(this.db, async transaction => {
      this.assert();
      const refs = mutations.map(mutation => doc(this.projects, mutation.value.id));
      const snapshots = await Promise.all(refs.map(ref => transaction.get(ref)));
      this.assert();
      const conflicts: SyncProject[] = [];
      for (let index = 0; index < mutations.length; index++) {
        const mutation = mutations[index]!;
        const snapshot = snapshots[index]!;
        const current = snapshot.exists() ? decode(snapshot.data(), snapshot.id) : null;
        if (current?.version === mutation.value.version) continue; // retry after lost acknowledgement
        if ((current?.version ?? null) !== mutation.expectedVersion ||
          (current && deletionState(current) === "DELETED" && deletionState(mutation.value) !== "DELETED")) {
          if (current) conflicts.push(current);
          else throw new ApplicationError("同期先のProjectを確認できません。", "SYNC_DATA_INVALID");
        }
      }
      if (conflicts.length) return { committed: false, conflicts };
      for (let index = 0; index < mutations.length; index++) {
        const { value, expectedVersion } = mutations[index]!;
        if (snapshots[index]!.data()?.version === value.version) continue;
        transaction.set(refs[index]!, { schema: 1, id: value.id, version: value.version, revision: value.revision,
          baseVersion: expectedVersion, latestDeviceId: value.latestDeviceId,
          deletion: deletionState(value), undoUntil: value.undoUntil, payload: JSON.stringify(value.record) });
      }
      return { committed: true };
    }, { maxAttempts: 1 }); // The durable controller owns retry/backoff.
  }
  subscribe(changed: () => void, failed: (error: unknown) => void) {
    this.assert();
    // Snapshot is an invalidation signal only. Never treat SDK cache/optimistic
    // pending writes as remotely committed data.
    return onSnapshot(this.projects, { includeMetadataChanges: true }, snapshot => {
      if (this.active && !snapshot.metadata.fromCache && !snapshot.metadata.hasPendingWrites) changed();
    }, failed);
  }
  dispose() { this.active = false; }
}
