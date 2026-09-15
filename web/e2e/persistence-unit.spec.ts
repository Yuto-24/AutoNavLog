import { expect, test } from "@playwright/test";
import { IDBFactory, IDBObjectStore } from "fake-indexeddb";
import { IndexedDbProjectRepository, LOCAL_DATABASE } from "../src/localProjectRepository";
import type { LocalProjectRecord } from "../src/localProjectRepository";
import type { Project } from "../src/types";

let factory: IDBFactory;
const validate = async (raw: unknown): Promise<LocalProjectRecord> => {
  const value = structuredClone(raw) as LocalProjectRecord;
  if (value.schemaVersion !== 2 || typeof value.token !== "string" || !value.draft || value.draft.id !== value.id || (value.lastCalculation as any)?.corrupt) throw Error("invalid");
  return value;
};
const repo = (evict = async () => {}) => new IndexedDbProjectRepository(validate, evict, () => factory);
function record(id: string, saved = false): LocalProjectRecord {
  const draft = { id, revision: 0, name: id, status: "DRAFT" } as Project;
  return { schemaVersion: 2, id, token: crypto.randomUUID(), draft,
    checkpoint: saved ? draft : null, lastCalculation: null, updatedAt: new Date().toISOString() };
}
async function rawPut(value: unknown) {
  await new Promise<void>((resolve, reject) => {
    const open = factory.open(LOCAL_DATABASE, 1);
    open.onsuccess = () => {
      const tx = open.result.transaction("projects", "readwrite");
      tx.objectStore("projects").put(value);
      tx.oncomplete = () => { open.result.close(); resolve(); };
      tx.onabort = () => reject(tx.error);
    };
  });
}
test.beforeEach(() => { factory = new IDBFactory(); });

test("Latest replacement, promotion, saved draft and explicit open are atomic", async () => {
  const repository = repo();
  const saved = record("saved", true), latest = record("latest"), newer = record("new");
  await repository.write(saved, null, false);
  await repository.write(latest, null, true);
  await repository.write(newer, null, true);
  expect((await repository.list()).projects.map(row => row.id).sort()).toEqual(["new", "saved"]);
  const promoted = { ...newer, token: crypto.randomUUID(), checkpoint: newer.draft };
  await repository.write(promoted, newer.token, false);
  const third = record("third");
  await repository.write(third, null, true);
  await repository.open(saved);
  expect((await repository.list()).projects.map(row => row.id).sort()).toEqual(["new", "saved"]);
  await expect(repository.write({ ...third, token: crypto.randomUUID() }, third.token, true)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  await repository.delete(saved.id, saved.token);
  expect((await repository.list()).projects.map(row => row.id)).toEqual(["new"]);
});

test("quota retries after weather eviction; failed replacement retains all previous data", async () => {
  let evictions = 0;
  const repository = repo(async () => { evictions++; });
  const old = record("old"), named = record("named", true);
  old.lastCalculation = { retained: true };
  await repository.write(old, null, true);
  await repository.write(named, null, false);
  const original = IDBObjectStore.prototype.put;
  IDBObjectStore.prototype.put = function () { throw new DOMException("full", "QuotaExceededError"); };
  try {
    await expect(repository.write(record("new"), null, true)).rejects.toMatchObject({ code: "LOCAL_STORAGE_FAILED" });
    expect(evictions).toBe(1);
    expect(await repository.read("old")).toEqual(old);
    expect(await repository.read("named")).toEqual(named);
  } finally { IDBObjectStore.prototype.put = original; }
  let tries = 0;
  IDBObjectStore.prototype.put = function (...args: Parameters<typeof original>) {
    if (tries++ === 0) throw new DOMException("full", "QuotaExceededError");
    return original.apply(this, args);
  };
  try { await repository.write(record("new"), null, true); }
  finally { IDBObjectStore.prototype.put = original; }
  expect(evictions).toBe(2);
  expect((await repository.list()).projects.map(row => row.id).sort()).toEqual(["named", "new"]);
});

test("corruption stays isolated and intact, including unavailable Latest", async () => {
  const repository = repo(), good = record("good", true), corrupt = record("corrupt");
  await repository.write(good, null, false);
  corrupt.lastCalculation = { corrupt: true };
  await rawPut(corrupt);
  expect(await repository.list()).toMatchObject({ unavailable: ["corrupt"], projects: [{ id: "good" }] });
  await expect(repository.read("corrupt")).rejects.toMatchObject({ code: "LOCAL_PROJECT_UNAVAILABLE" });
  await repository.write(record("new"), null, true);
  await repository.open(good);
  expect((await repository.list()).unavailable).toEqual(["corrupt"]);
});

test("concurrent Latest insertions cannot create multiple retained slots", async () => {
  const first = repo(), second = repo();
  const results = await Promise.allSettled([first.write(record("first"), null, true), second.write(record("second"), null, true)]);
  expect(results.some(result => result.status === "fulfilled")).toBe(true);
  expect((await first.list()).projects).toHaveLength(1);
});

test("stale writes and failed open never discard valid Latest or named checkpoints", async () => {
  const repository = repo(), named = record("named", true), latest = record("latest");
  await repository.write(named, null, false);
  await repository.write(latest, null, true);
  const next = { ...named, token: crypto.randomUUID() };
  await repository.write(next, named.token, false);
  await expect(repository.write(named, named.token, false)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  await expect(repository.open(named)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  expect(await repository.read(latest.id)).toEqual(latest);
  expect(await repository.read(named.id)).toEqual(next);
});

test("a transaction aborted after requests succeeded rolls back both replacement and eviction", async () => {
  const repository = repo();
  const latest = record("latest");
  await repository.write(latest, null, true);
  const original = IDBObjectStore.prototype.delete;
  IDBObjectStore.prototype.delete = function (...args: Parameters<typeof original>) {
    const request = original.apply(this, args);
    request.addEventListener("success", () => this.transaction.abort());
    return request;
  };
  try {
    await expect(repository.write(record("new"), null, true)).rejects.toMatchObject({ code: "LOCAL_STORAGE_FAILED" });
  } finally { IDBObjectStore.prototype.delete = original; }
  expect((await repository.list()).projects.map(row => row.id)).toEqual(["latest"]);
  expect(await repository.read(latest.id)).toEqual(latest);
});

test("storage denial is an explicit Local error", async () => {
  const repository = new IndexedDbProjectRepository(validate, async () => {}, () => { throw new DOMException("denied", "SecurityError"); });
  await expect(repository.list()).rejects.toMatchObject({ code: "LOCAL_STORAGE_FAILED" });
  await expect(repository.write(record("new"), null, true)).rejects.toMatchObject({ code: "LOCAL_STORAGE_FAILED" });
});


test("non-JSON corruption does not prevent healthy Project saves or Latest replacement", async () => {
  const repository = repo(), good = record("good", true), corrupt = record("corrupt");
  await repository.write(good, null, false);
  const cyclic: any = { corrupt: true, value: 1n };
  cyclic.self = cyclic;
  corrupt.lastCalculation = cyclic;
  await rawPut(corrupt);
  await repository.write(record("new"), null, true);
  await repository.open(good);
  expect((await repository.list()).unavailable).toEqual(["corrupt"]);
  expect(await repository.read(good.id)).toEqual(good);
});


test("corrupt records without a token are never inferred to be Latest deletion targets", async () => {
  const repository = repo(), good = record("good", true);
  await repository.write(good, null, false);
  await rawPut({ id: "missing-token", schemaVersion: 999 });
  await repository.write(record("new"), null, true);
  await repository.open(good);
  expect((await repository.list()).unavailable).toEqual(["missing-token"]);
});


test("stale explicit delete cannot remove a newer saved draft or Last Calculation", async () => {
  const repository = repo(), original = record("saved", true);
  await repository.write(original, null, false);
  const observed = (await repository.list()).tokens[original.id];
  const newer = { ...original, token: crypto.randomUUID(), lastCalculation: { retained: true } };
  await repository.write(newer, original.token, false);
  await expect(repository.delete(original.id, observed)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  expect(await repository.read(original.id)).toEqual(newer);
  await repository.delete(newer.id, newer.token);
  expect((await repository.list()).projects).toEqual([]);
});


test("missing-token existing data is not equivalent to an absent Project for a new write", async () => {
  const repository = repo();
  await repository.list();
  const original = { id: "same", schemaVersion: 999 };
  await rawPut(original);
  await expect(repository.write(record("same"), null, true)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  expect((await repository.list()).unavailable).toEqual(["same"]);
});
