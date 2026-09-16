import { test, expect } from "@playwright/test";
import { initializeApp, deleteApp } from "firebase/app";
import { connectFirestoreEmulator, doc, getDoc, getFirestore, setDoc, terminate } from "firebase/firestore";
import { FirestoreSyncRepository } from "../src/firestoreSyncRepository";
import type { SyncProject } from "../src/accountSync";

test("Firestore Rules enforce Google ownership, CAS, atomic batches and irreversible expiry", async () => {
  const subject = `rules-${crypto.randomUUID()}`;
  const app = initializeApp({ projectId: "demo-autonavlog-sync", apiKey: "test" }, subject);
  const db = getFirestore(app);
  connectFirestoreEmulator(db, "127.0.0.1", 8088, { mockUserToken: { sub: "firebase-owner", firebase: { sign_in_provider: "google.com", identities: { "google.com": [subject] } } } });
  const repository = new FirestoreSyncRepository(db, subject, () => {});
  const value = { revision: 1, id: crypto.randomUUID(), version: crypto.randomUUID(), latestDeviceId: null, deletion: "ACTIVE", undoUntil: null,
    record: { id: "test-payload", nested: [[1, 2]], provenance: "retained" } } as unknown as SyncProject;
  value.record.id = value.id;
  try {
    expect(await repository.commit([{ expectedVersion: null, value }])).toEqual({ committed: true });
    expect((await repository.list())[0]?.record).toEqual(value.record);
    const ref = doc(db, "googleAccounts", subject, "projects", value.id);
    const raw = (await getDoc(ref)).data()!;
    await expect(setDoc(ref, { ...raw, version: "stale", baseVersion: null })).rejects.toMatchObject({ code: "permission-denied" });
    await expect(getDoc(doc(db, "googleAccounts", "someone-else", "projects", value.id))).rejects.toMatchObject({ code: "permission-denied" });
    await expect(setDoc(doc(db, "googleAccounts", "someone-else", "projects", value.id), raw)).rejects.toMatchObject({ code: "permission-denied" });
    const pending = { ...value, revision: 2, version: crypto.randomUUID(), deletion: "PENDING_DELETE" as const, undoUntil: Date.now() - 1000 };
    await repository.commit([{ expectedVersion: value.version, value: pending }]);
    const copy = { ...value, id: crypto.randomUUID(), version: crypto.randomUUID() };
    copy.record = { ...value.record, id: copy.id };
    const result = await repository.commit([{ expectedVersion: pending.version, value: { ...value, version: crypto.randomUUID() } }, { expectedVersion: null, value: copy }]);
    expect(result.committed).toBe(false);
    expect((await getDoc(doc(db, "googleAccounts", subject, "projects", copy.id))).exists()).toBe(false);
    await expect(setDoc(ref, { ...raw, revision: 3, baseVersion: pending.version, version: crypto.randomUUID() })).rejects.toMatchObject({ code: "permission-denied" });
    const tombstone = { ...pending, revision: 3, deletion: "DELETED" as const, version: crypto.randomUUID() };
    expect(await repository.commit([{ expectedVersion: pending.version, value: tombstone }])).toEqual({ committed: true });
  } finally { repository.dispose(); await terminate(db); await deleteApp(app); }
});

test("Rules bound the Undo window at create/update and reject extending a pending deletion", async () => {
  const subject = "deadline-" + crypto.randomUUID();
  const app = initializeApp({ projectId: "demo-autonavlog-sync", apiKey: "test" }, subject);
  const db = getFirestore(app);
  connectFirestoreEmulator(db, "127.0.0.1", 8088, { mockUserToken: { sub: "owner", firebase: { sign_in_provider: "google.com", identities: { "google.com": [subject] } } } });
  const id = crypto.randomUUID(), ref = doc(db, "googleAccounts", subject, "projects", id);
  const raw = { schema: 1, id, version: crypto.randomUUID(), revision: 1, baseVersion: null,
    latestDeviceId: null, deletion: "ACTIVE", undoUntil: null, payload: JSON.stringify({ id }) };
  try {
    await expect(setDoc(ref, { ...raw, deletion: "PENDING_DELETE", undoUntil: Date.now() + 60_000 })).rejects.toMatchObject({ code: "permission-denied" });
    await setDoc(ref, raw);
    const pending = { ...raw, baseVersion: raw.version, revision: 2, version: crypto.randomUUID(), deletion: "PENDING_DELETE", undoUntil: Date.now() + 8_000 };
    await expect(setDoc(ref, { ...pending, undoUntil: Date.now() + 60_000 })).rejects.toMatchObject({ code: "permission-denied" });
    await setDoc(ref, pending);
    const next = { ...pending, baseVersion: pending.version, revision: 3, version: crypto.randomUUID() };
    await expect(setDoc(ref, { ...next, undoUntil: pending.undoUntil + 1_000 })).rejects.toMatchObject({ code: "permission-denied" });
    await setDoc(ref, next);
    await setDoc(ref, { ...next, baseVersion: next.version, revision: 4, version: crypto.randomUUID(), deletion: "ACTIVE", undoUntil: null });
  } finally { await terminate(db); await deleteApp(app); }
});

test("Firestore adapter rejects missing/mismatched payload identities and malformed JSON before domain hydration", async () => {
  const subject = "payload-" + crypto.randomUUID();
  const app = initializeApp({ projectId: "demo-autonavlog-sync", apiKey: "test" }, subject);
  const db = getFirestore(app);
  connectFirestoreEmulator(db, "127.0.0.1", 8088, { mockUserToken: { sub: "owner", firebase: { sign_in_provider: "google.com", identities: { "google.com": [subject] } } } });
  const repository = new FirestoreSyncRepository(db, subject, () => {});
  const id = crypto.randomUUID(), ref = doc(db, "googleAccounts", subject, "projects", id);
  let version: string | null = null, revision = 0;
  try {
    for (const payload of [JSON.stringify({ id: "different" }), "{}", "null", "{broken"]) {
      const next = crypto.randomUUID();
      await setDoc(ref, { schema: 1, id, version: next, baseVersion: version, revision: ++revision,
        latestDeviceId: null, deletion: "ACTIVE", undoUntil: null, payload });
      version = next;
      await expect(repository.list()).rejects.toMatchObject({ code: "SYNC_DATA_INVALID" });
      await expect(repository.commit([{ expectedVersion: next, value: { id } as SyncProject }])).rejects.toMatchObject({ code: "SYNC_DATA_INVALID" });
    }
  } finally { repository.dispose(); await terminate(db); await deleteApp(app); }
});
