import { expect, test } from "@playwright/test";
import { IDBFactory, IDBObjectStore } from "fake-indexeddb";
import { IndexedDbProjectRepository, type LocalProjectRecord } from "../src/localProjectRepository";
import { accountBoundaryClosed, type AuthProvider } from "../src/auth";
import { FirebaseAuthProvider, googleAccount, retainAuthDuringOutage } from "../src/firebaseAuthProvider";
import type { Auth, User } from "firebase/auth";
import { createAccountContext } from "../src/accountContext";
import { browserPlatform } from "../src/browserPlatform";
import { LocalApplication } from "../src/localApplication";
import { currentAccount, removeLocalAccount } from "../src/accountLifecycle";
import type { Project } from "../src/types";

const validate = async (row: unknown) => structuredClone(row) as LocalProjectRecord;
const record = (id: string, saved = false): LocalProjectRecord => ({ schemaVersion: 2, id, token: crypto.randomUUID(),
  draft: { id, name: id, revision: 0, status: "DRAFT" } as Project,
  checkpoint: saved ? { id, name: id } as Project : null, lastCalculation: { outcome: { secret: id } }, updatedAt: "2026-09-16" });

test("internal account identity is stable across devices/Firebase UIDs and independent of email", async () => {
  const user = (uid: string, email: string) => ({ uid, email, displayName: "Pilot", providerData: [{ providerId: "google.com", uid: "google-subject" }] }) as any;
  const account = await googleAccount(user("firebase-one", "old@example.test"));
  expect(account.account_id).toBe("account_v1_c8ed2cf1b1bdddb3f305f6d3fddb63df02594ca0769832b3b58f76faf4812739");
  expect(await googleAccount(user("firebase-two", "new@example.test"))).toEqual(account);
  expect((await googleAccount({ ...user("firebase-one", "old@example.test"), providerData: [{ providerId: "google.com", uid: "other" }] })).account_id).not.toBe(account.account_id);
  await expect(googleAccount({ ...user("a", "a"), providerData: [] })).rejects.toThrow();
});

test("account purge removes its namespace and claimed originals while preserving another owner", async () => {
  const factory = new IDBFactory();
  const previousIdb = globalThis.indexedDB;
  const previousSession = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  const sessions = new Map<string, string>();
  Object.defineProperty(globalThis, "indexedDB", { configurable: true, value: factory });
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: {
    removeItem(key: string) { sessions.delete(key); },
  } });
  try {
    const scope = (account_id: string | null) => new IndexedDbProjectRepository(validate, undefined, () => factory,
      { account_id, assertActive() {}, onClose: () => () => {} });
    const anonymous = scope(null), owner = scope("owner-a"), other = scope("owner-b");
    await anonymous.write(record("claimed", true), null, false);
    await anonymous.claimAnonymous("owner-a");
    await anonymous.write(record("unclaimed", true), null, false);
    await owner.write(record("owner-data", true), null, false);
    await other.write(record("other-data", true), null, false);
    sessions.set("autonavlog.working-session.v1.owner-a", "old input");
    await removeLocalAccount("owner-a");
    expect((await scope("owner-a").list()).projects).toHaveLength(0);
    expect((await anonymous.list()).projects.map(row => row.id)).toEqual(["unclaimed"]);
    expect((await other.list()).projects.map(row => row.id)).toEqual(["other-data"]);
    expect(sessions.size).toBe(0);
  } finally {
    if (previousIdb) Object.defineProperty(globalThis, "indexedDB", { configurable: true, value: previousIdb });
    else delete (globalThis as { indexedDB?: IDBFactory }).indexedDB;
    if (previousSession) Object.defineProperty(globalThis, "sessionStorage", previousSession);
    else delete (globalThis as { sessionStorage?: Storage }).sessionStorage;
  }
});

test("a remotely retired account cache cannot reopen its Local namespace offline", async () => {
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { onLine: false } });
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem(key: string) { return values.get(key) ?? null; },
  } });
  const user = { uid: "firebase-owner", providerData: [{ providerId: "google.com", uid: "offline-owner" }] } as User;
  try {
    values.set("autonavlog.account.current.offline-owner", "account_v1_old");
    expect((await currentAccount(user)).accountId).toBe("account_v1_old");
    values.set("autonavlog.account.current.offline-owner", "retired:account_v1_old");
    await expect(currentAccount(user)).rejects.toThrow("オンライン接続と再認証が必要");
  } finally {
    if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
    else delete (globalThis as { navigator?: Navigator }).navigator;
    if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage);
    else delete (globalThis as { localStorage?: Storage }).localStorage;
  }
});

test("remote generation change closes an in-flight old owner write before purging its database", async () => {
  const factory = new IDBFactory(), previousIdb = globalThis.indexedDB;
  const previousSession = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "indexedDB", { configurable: true, value: factory });
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: { removeItem() {} } });
  const user = { uid: "firebase-owner", providerData: [{ providerId: "google.com", uid: "google-owner" }] } as User;
  const auth = { currentUser: user } as Auth;
  let changed = false, active = true, change: Promise<void> | undefined;
  const closures = new Set<() => void>();
  const provider = new FirebaseAuthProvider(auth, async (_user, _register, close) => {
    if (!changed) return { accountId: "owner-old", deleting: false };
    close();
    await removeLocalAccount("owner-old");
    return { accountId: "owner-new", deleting: false };
  });
  const context = { account_id: "owner-old", assertActive() { if (!active) throw accountBoundaryClosed(); },
    onClose(fn: () => void) { closures.add(fn); return () => { closures.delete(fn); }; } };
  const repo = new IndexedDbProjectRepository(validate, undefined, () => factory, context);
  const getAll = IDBObjectStore.prototype.getAll;
  try {
    await (provider as any).accept(user);
    provider.subscribe(() => {
      if (!provider.getState().account) { active = false; closures.forEach(fn => fn()); }
    });
    await repo.write(record("old", true), null, false);
    changed = true;
    IDBObjectStore.prototype.getAll = function (...args: Parameters<typeof getAll>) {
      const request = getAll.apply(this, args);
      change ??= (provider as any).accept(user);
      return request;
    };
    await expect(repo.write(record("racing", true), null, false)).rejects.toMatchObject({ code: "ACCOUNT_CONTEXT_CLOSED" });
    await change;
    expect((await new IndexedDbProjectRepository(validate, undefined, () => factory, context).list().catch(() => null))).toBeNull();
    active = true;
    expect((await new IndexedDbProjectRepository(validate, undefined, () => factory, context).list()).projects).toHaveLength(0);
  } finally {
    IDBObjectStore.prototype.getAll = getAll;
    if (previousIdb) Object.defineProperty(globalThis, "indexedDB", { configurable: true, value: previousIdb });
    else delete (globalThis as { indexedDB?: IDBFactory }).indexedDB;
    if (previousSession) Object.defineProperty(globalThis, "sessionStorage", previousSession);
    else delete (globalThis as { sessionStorage?: Storage }).sessionStorage;
  }
});

test("all Repository operations isolate anonymous, A and B including Latest cleanup and Last Calculation", async () => {
  const factory = new IDBFactory();
  const scope = (account_id: string | null) => new IndexedDbProjectRepository(validate, undefined, () => factory, { account_id, assertActive() {}, onClose: () => () => {} });
  const anon = scope(null), a = scope("A"), b = scope("B");
  const anonymous = record("anonymous"), secret = record("secret", true), latest = record("A-latest"), foreign = record("B", true);
  await anon.write(anonymous, null, true); await a.write(secret, null, false); await a.write(latest, null, true); await b.write(foreign, null, false);
  expect((await anon.list()).projects.map(row => row.id)).toEqual(["anonymous"]);
  expect((await b.list()).projects.map(row => row.id)).toEqual(["B"]);
  await expect(b.read(secret.id)).rejects.toMatchObject({ code: "LOCAL_PROJECT_UNAVAILABLE" });
  await expect(b.open(secret)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  await expect(b.delete(secret.id, secret.token)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  await expect(b.write(secret, secret.token, false)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  await b.write(record(secret.id, true), null, false); // identical Project ID, different ownership
  await b.open(foreign); await b.delete(secret.id, (await b.read(secret.id)).token);
  expect(await a.read(secret.id)).toEqual(secret);
  expect(await a.read(latest.id)).toEqual(latest);
  expect(await anon.read(anonymous.id)).toEqual(anonymous);
  expect(await scope("A").read(secret.id)).toEqual(secret); // same account reauthentication
});

test("closed account handles reject reads and writes, including an in-flight transaction", async () => {
  const factory = new IDBFactory();
  let active = true;
  const assertActive = () => { if (!active) throw accountBoundaryClosed(); };
  const repository = new IndexedDbProjectRepository(validate, undefined, () => factory, { account_id: "A", assertActive, onClose: () => () => {} });
  const original = record("original", true);
  await repository.write(original, null, false);
  const getAll = IDBObjectStore.prototype.getAll;
  IDBObjectStore.prototype.getAll = function (...args: Parameters<typeof getAll>) {
    const request = getAll.apply(this, args);
    request.addEventListener("success", () => { active = false; });
    return request;
  };
  try { await expect(repository.write(record("late"), null, false)).rejects.toMatchObject({ code: "ACCOUNT_CONTEXT_CLOSED" }); }
  finally { IDBObjectStore.prototype.getAll = getAll; }
  await expect(repository.list()).rejects.toMatchObject({ code: "ACCOUNT_CONTEXT_CLOSED" });
  await expect(repository.read(original.id)).rejects.toMatchObject({ code: "ACCOUNT_CONTEXT_CLOSED" });
  await expect(repository.delete(original.id, original.token)).rejects.toMatchObject({ code: "ACCOUNT_CONTEXT_CLOSED" });
  active = true;
  expect((await repository.list()).projects.map(row => row.id)).toEqual(["original"]);
});

test("account Session cannot hydrate another account; disposing disables old UI persistence", () => {
  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
  const platform = { ...browserPlatform, session: { storage, isReload: () => true } };
  const auth = (id: string) => ({ getState: () => ({ account: { account_id: id, displayName: id }, available: true }) }) as AuthProvider;
  const a = createAccountContext(platform, auth("A"));
  a.platform.session.storage.setItem("ignored", "A raw drafts and snapshot");
  const b = createAccountContext(platform, auth("B"));
  const anon = createAccountContext(platform);
  expect(b.platform.session.storage.getItem("ignored")).toBeNull();
  expect(anon.platform.session.storage.getItem("ignored")).toBeNull();
  a.dispose();
  expect(() => a.platform.session.storage.setItem("ignored", "late A write")).toThrow();
  expect(createAccountContext(platform, auth("A"), false).platform.session.isReload()).toBe(false);
  expect(createAccountContext(platform, auth("A")).platform.session.storage.getItem("ignored")).toBeNull();
});

test("disposed Application cannot commit delayed worker output or queued operations", async () => {
  let release!: (value: any) => void;
  let writes = 0;
  const app = new LocalApplication(() => ({ write: async () => { writes++; }, list: async () => ({ projects: [], unavailable: [], tokens: {} }) }) as any,
    () => ({ request: () => new Promise(resolve => { release = resolve; }), dispose() {} }));
  const pending = app.confirmRoute({} as any);
  const queued = app.saveProject("queued");
  await new Promise(resolve => setTimeout(resolve, 0));
  app.dispose(); release({ workingRecovery: { project: record("late").draft } });
  await expect(pending).rejects.toMatchObject({ code: "ACCOUNT_CONTEXT_CLOSED" });
  await expect(queued).rejects.toMatchObject({ code: "ACCOUNT_CONTEXT_CLOSED" });
  expect(writes).toBe(0);
});

test("SDK startup outage guard preserves terminal responses and unrelated fetches, then restores fetch", async () => {
  const original = globalThis.fetch;
  try {
    for (const status of [429, 500, 503]) {
      globalThis.fetch = async () => Response.json({ error: { message: "INTERNAL_ERROR" } }, { status });
      const fetchBefore = globalThis.fetch;
      await expect(retainAuthDuringOutage(() => fetch("https://identitytoolkit.googleapis.com/v1/accounts:lookup"))).rejects.toThrow(TypeError);
      expect(globalThis.fetch).toBe(fetchBefore);
    }
    globalThis.fetch = async () => Response.json({ error: { message: "TOO_MANY_ATTEMPTS_TRY_LATER" } }, { status: 400 });
    await expect(retainAuthDuringOutage(() => fetch("https://identitytoolkit.googleapis.com/v1/accounts:lookup"))).rejects.toThrow(TypeError);
    globalThis.fetch = async () => Response.json({ error: { message: "USER_DISABLED" } }, { status: 400 });
    expect((await retainAuthDuringOutage(() => fetch("https://identitytoolkit.googleapis.com/v1/accounts:lookup"))).status).toBe(400);
    globalThis.fetch = async () => new Response("unrelated", { status: 503 });
    expect((await retainAuthDuringOutage(() => fetch("https://example.test/weather"))).status).toBe(503);
  } finally { globalThis.fetch = original; }
});


test("closing an account aborts writes after request success but before transaction commit", async () => {
  const factory = new IDBFactory();
  const callbacks = new Set<() => void>();
  let active = true;
  const context = { account_id: "A", assertActive() { if (!active) throw accountBoundaryClosed(); },
    onClose(listener: () => void) { callbacks.add(listener); return () => { callbacks.delete(listener); }; } };
  const repo = new IndexedDbProjectRepository(validate, undefined, () => factory, context);
  const original = record("latest"); await repo.write(original, null, true);
  const put = IDBObjectStore.prototype.put;
  IDBObjectStore.prototype.put = function (...args: Parameters<typeof put>) {
    const request = put.apply(this, args);
    request.addEventListener("success", () => { active = false; for (const callback of callbacks) callback(); });
    return request;
  };
  try { await expect(repo.write(record("replacement"), null, true)).rejects.toMatchObject({ code: "ACCOUNT_CONTEXT_CLOSED" }); }
  finally { IDBObjectStore.prototype.put = put; }
  active = true;
  expect(await repo.read(original.id)).toEqual(original);
  expect((await repo.list()).projects.map(row => row.id)).toEqual(["latest"]);
  expect(callbacks.size).toBe(0);
});


for (const operation of ["list", "read"] as const) test(`closing during async validation rejects Repository ${operation} without exposing data`, async () => {
  const factory = new IDBFactory();
  let active = true, hold = false;
  let release!: () => void;
  const context = { account_id: "A", assertActive() { if (!active) throw accountBoundaryClosed(); }, onClose: () => () => {} };
  const repo = new IndexedDbProjectRepository(async row => {
    if (hold) await new Promise<void>(resolve => { release = resolve; });
    return validate(row);
  }, undefined, () => factory, context);
  await repo.write(record("secret", true), null, false);
  hold = true;
  const pending = operation === "list" ? repo.list() : repo.read("secret");
  await expect.poll(() => Boolean(release)).toBe(true);
  active = false; release();
  await expect(pending).rejects.toMatchObject({ code: "ACCOUNT_CONTEXT_CLOSED" });
});
