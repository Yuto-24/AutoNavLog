// Test-only surface: real browser IndexedDB and real Firestore SDK/emulator.
import { initializeApp, deleteApp } from "firebase/app";
import { initializeFirestore, connectFirestoreEmulator, terminate, setLogLevel } from "firebase/firestore";
import { AccountProjectRepository } from "../../src/accountProjectRepository";
import { IndexedDbProjectRepository } from "../../src/localProjectRepository";
import { FirestoreSyncRepository } from "../../src/firestoreSyncRepository";
import { AccountProjectRepository as OldAccount } from "../fixtures/v1.20.1/accountProjectRepository";
import { IndexedDbProjectRepository as OldLocal } from "../fixtures/v1.20.1/localProjectRepository";
import { FirestoreSyncRepository as OldRemote } from "../fixtures/v1.20.1/firestoreSyncRepository";

setLogLevel("silent"); // Expected stale-write Rules denials must not print full synthetic records.

let repository: any, remote: any, firebase: any, db: any, databaseName: string;
let pendingResult: Promise<any>;
let mutation: any, releaseTransaction = false, heldDb: IDBDatabase | undefined;
const status = { upgraded: false, error: "", transactionComplete: false, versionchange: false };
const copy = (value: any) => structuredClone(value);
// Domain read incompatibility was independently established with both Python models.
// Here the old reader rejects the known new field; storage/SDK code is frozen verbatim.
const validate = (old: boolean) => async (record: any) => {
  const value = copy(record);
  if (old && [value.draft, value.checkpoint, value.lastCalculation?.project].some(
    project => project?.route_nodes.some((node: any) => "reference_snapshot" in node))) throw Error("old domain reader");
  return value;
};
async function snapshot() {
  return new Promise<any[]>((resolve, reject) => {
    const open = indexedDB.open(databaseName);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const database = open.result, tx = database.transaction("projects", "readonly");
      const read = tx.objectStore("projects").getAll();
      tx.oncomplete = () => { database.close(); resolve(read.result); };
      tx.onabort = () => { database.close(); reject(tx.error); };
    };
  });
}
(window as any).upgrade = {
  status,
  async init({ old = false, account = null, subject = "", device = "same-device" }: any) {
    databaseName = account ? `autonavlog.projects.${account}` : "autonavlog.projects";
    const context = { account_id: account, assertActive() {}, onClose() { return () => {}; } };
    repository = account ? new (old ? OldAccount : AccountProjectRepository)(validate(old), context, device, async value => value)
      : new (old ? OldLocal : IndexedDbProjectRepository)(validate(old));
    if (subject) {
      firebase = initializeApp({ projectId: "demo-autonavlog-sync", apiKey: "test" }, crypto.randomUUID());
      // Emulator-only finite long polling avoids open streaming requests during tab teardown.
      db = initializeFirestore(firebase, { experimentalForceLongPolling: true });
      connectFirestoreEmulator(db, "127.0.0.1", 8088, { mockUserToken: { sub: `firebase-${subject}`,
        firebase: { sign_in_provider: "google.com", identities: { "google.com": [subject] } } } });
      remote = new (old ? OldRemote : FirestoreSyncRepository)(db, subject, () => {});
    }
  },
  list: () => repository.list(), read: (id: string) => repository.read(id),
  write: (record: any, token: string | null, latest = false) => repository.write(record, token, latest),
  open: (record: any) => repository.open(record), delete: (id: string, token: string) => repository.delete(id, token),
  snapshot,
  async attempt(operation: string, input: any) {
    try { await (window as any).upgrade[operation](...input); return "accepted"; }
    catch (error) { return (error as any).code ?? (error as Error).message; }
  },
  async flush() {
    for (const group of await repository.pending()) {
      if (!await repository.markSending(group)) throw Error("stale group");
      const result = await remote.commit(group);
      if (result.committed) await repository.acknowledge(group);
      else await repository.receive(result.conflicts);
    }
    await repository.receive(await remote.list());
  },
  remoteList: () => remote.list(), pending: () => repository.pending(),
  receive: (values: any) => repository.receive(values),
  async holdPending() {
    [mutation] = await repository.pending();
    if (!mutation || !await repository.markSending(mutation)) throw Error("missing pending mutation");
    return copy(mutation);
  },
  startPending() {
    pendingResult = remote.commit(mutation);
    void pendingResult.catch(() => {}); // The test releases and observes the held RPC later.
  },
  async finishStartedPending() {
    const result = await pendingResult;
    if (result.committed) await repository.acknowledge(mutation);
    else await repository.receive(result.conflicts);
  },
  async finishPending() {
    const result = await remote.commit(mutation);
    if (result.committed) await repository.acknowledge(mutation);
    else await repository.receive(result.conflicts);
  },
  startUpgrade() { void repository.list().then(() => { status.upgraded = true; }, (error: any) => { status.error = error.code; }); },
  hold(blockUpgrade: boolean) {
    return new Promise<void>((resolve, reject) => {
      const open = indexedDB.open(databaseName, 1);
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        heldDb = open.result;
        heldDb.onversionchange = () => { status.versionchange = true; if (!blockUpgrade) heldDb!.close(); };
        if (blockUpgrade) { resolve(); return; }
        const tx = heldDb.transaction("projects", "readwrite"), store = tx.objectStore("projects");
        tx.oncomplete = () => { status.transactionComplete = true; };
        tx.onabort = () => reject(tx.error);
        const keepAlive = () => {
          const read = store.getAll();
          read.onsuccess = () => {
            if (!releaseTransaction) keepAlive();
            else { const row = read.result[0]; row.draft.pilot_name = "transaction completed before upgrade"; store.put(row); }
          };
        };
        keepAlive(); resolve();
      };
    });
  },
  heldSnapshot() {
    return new Promise<any[]>((resolve, reject) => {
      const tx = heldDb!.transaction("projects", "readonly");
      const read = tx.objectStore("projects").getAll();
      tx.oncomplete = () => resolve(read.result); tx.onabort = () => reject(tx.error);
    });
  },
  release() { releaseTransaction = true; }, closeBlocker() { heldDb?.close(); },
  dispose() {
    remote?.dispose();
    // The Playwright context closes the transport. Never keep the page alive
    // awaiting the SDK's long-poll shutdown while testing an in-flight request.
    if (db) void terminate(db).catch(() => {});
    if (firebase) void deleteApp(firebase).catch(() => {});
  },
};
document.querySelector("#ready")!.textContent = "Ready";
