import { expect, test } from "@playwright/test";
import { IDBFactory } from "fake-indexeddb";
import { AccountProjectRepository } from "../src/accountProjectRepository";
import { IndexedDbProjectRepository, type LocalProjectRecord } from "../src/localProjectRepository";
import { deletionState, type SyncMutation, type SyncProject } from "../src/accountSync";
import { AccountSyncController } from "../src/accountSyncController";
import type { Project } from "../src/types";

const validate = async (raw: unknown) => {
  const value = structuredClone(raw) as LocalProjectRecord;
  if (!value.draft || value.draft.id !== value.id || (value.checkpoint && value.checkpoint.revision !== value.draft.revision)) throw Error("invalid");
  return value;
};
function record(id = crypto.randomUUID(), saved = true): LocalProjectRecord {
  const draft = { id, revision: 0, name: id, status: "DRAFT", metadata: {}, updated_at: new Date().toISOString() } as Project;
  return { schemaVersion: 2, id, token: crypto.randomUUID(), draft, checkpoint: saved ? structuredClone(draft) : null,
    lastCalculation: { sentinel: "calculation" }, updatedAt: draft.updated_at };
}
const edit = (row: LocalProjectRecord, name: string) => ({ ...structuredClone(row), token: crypto.randomUUID(), draft: { ...row.draft, name } });
function repo(device: string, factory = new IDBFactory(), account = "account-a") {
  let active = true;
  const callbacks = new Set<() => void>();
  const context = { account_id: account, assertActive() { if (!active) throw Error("closed"); },
    onClose(fn: () => void) { callbacks.add(fn); return () => { callbacks.delete(fn); }; } };
  const repository = new AccountProjectRepository(validate, context, device, async (raw, id) => {
    const value = structuredClone(raw);
    if (id) { value.id = id; value.draft.id = id; value.draft.revision = 0; if (value.checkpoint) { value.checkpoint.id = id; value.checkpoint.revision = 0; } }
    return value;
  }, () => factory);
  return { repository, close() { active = false; callbacks.forEach(fn => fn()); } };
}
class Remote {
  rows = new Map<string, SyncProject>();
  async commit(mutations: SyncMutation[]) {
    const conflicts = mutations.filter(m => {
      const current = this.rows.get(m.value.id);
      return current?.version !== m.value.version && ((current?.version ?? null) !== m.expectedVersion ||
        (current && deletionState(current) === "DELETED" && deletionState(m.value) !== "DELETED"));
    }).map(m => this.rows.get(m.value.id)!);
    if (conflicts.length) return { committed: false as const, conflicts };
    mutations.forEach(m => this.rows.set(m.value.id, structuredClone(m.value)));
    return { committed: true as const };
  }
  async sync(local: AccountProjectRepository) {
    for (const mutations of await local.pending()) {
      await local.markSending(mutations);
      const result = await this.commit(mutations);
      if (result.committed) await local.acknowledge(mutations); else await local.receive(result.conflicts);
    }
    await local.receive([...this.rows.values()]);
  }
}
test("union, full Last Calculation, fresh device offline copies and account isolation", async () => {
  const a = repo("A").repository, factory = new IDBFactory(), b = repo("B", factory).repository, remote = new Remote();
  const first = record(), second = record();
  await a.write(first, null, false); await b.write(second, null, false);
  await remote.sync(a); await remote.sync(b); await remote.sync(a);
  expect((await a.list()).projects).toHaveLength(2);
  expect((await b.read(first.id)).lastCalculation).toEqual(first.lastCalculation);
  const reopened = repo("B", factory).repository;
  expect((await reopened.list()).projects).toHaveLength(2);
  expect((await repo("B", factory, "account-b").repository.list()).projects).toHaveLength(0);
});
test("Latest moves only on edit; own open discards durably, another device's Latest survives", async () => {
  const a = repo("A").repository, b = repo("B").repository, remote = new Remote();
  const aLatest = record(undefined, false), bLatest = record(undefined, false), saved = record();
  await a.write(aLatest, null, true); await a.write(saved, null, false); await b.write(bLatest, null, true);
  await remote.sync(a); await remote.sync(b); await remote.sync(a);
  expect((await a.list()).projects).toHaveLength(3);
  await b.open(await b.read(aLatest.id));
  await remote.sync(b);
  expect(remote.rows.get(aLatest.id)?.latestDeviceId).toBe("A");
  expect(deletionState(remote.rows.get(bLatest.id)!)).toBe("DELETED");
  const loaded = await b.read(aLatest.id);
  await b.write(edit(loaded, "B edit"), loaded.token, true); await remote.sync(b); await remote.sync(a);
  expect(remote.rows.get(aLatest.id)?.latestDeviceId).toBe("B");
  await a.open(await a.read(saved.id)); await remote.sync(a);
  expect(deletionState(remote.rows.get(aLatest.id)!)).toBe("ACTIVE");
  await b.open(await b.read(saved.id)); await remote.sync(b);
  expect(deletionState(remote.rows.get(aLatest.id)!)).toBe("DELETED");
});
test("stale remote revision autosaves locally, blocks further edits and keeps both atomically", async () => {
  const a = repo("A").repository, b = repo("B").repository, remote = new Remote(), original = record();
  await a.write(original, null, false); await remote.sync(a); await remote.sync(b);
  const aRead = await a.read(original.id), bRead = await b.read(original.id);
  await a.write(edit(aRead, "remote"), aRead.token, false); await remote.sync(a);
  await b.write(edit(bRead, "local"), bRead.token, false); await remote.sync(b);
  expect((await b.status()).conflicts).toHaveLength(1);
  expect((await b.read(original.id)).draft.name).toBe("local");
  await expect(b.write(edit(bRead, "more"), bRead.token, false)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  const copyId = await b.resolve(original.id, "both");
  const groups = await b.pending(); expect(groups).toHaveLength(1); expect(groups[0]).toHaveLength(2);
  await remote.sync(b);
  expect(remote.rows.get(original.id)?.record.draft.name).toBe("remote");
  expect(remote.rows.get(copyId)?.record.draft).toMatchObject({ id: copyId, name: "local", revision: 0 });
  expect((await b.status()).conflicts).toHaveLength(0);
});
test("remote refresh while an open Project is clean does not reject the first local autosave", async () => {
  const a = repo("A").repository, b = repo("B").repository, remote = new Remote(), original = record();
  await a.write(original, null, false); await remote.sync(a); await remote.sync(b);
  const before = await b.read(original.id), current = await a.read(original.id);
  await a.write(edit(current, "remote update"), current.token, false); await remote.sync(a); await remote.sync(b);
  expect((await b.readWorking(original.id, before.token)).draft.name).toBe(original.draft.name);
  await b.write(edit(before, "local update"), before.token, false);
  expect((await b.read(original.id)).draft.name).toBe("local update");
  expect((await b.status()).conflicts).toHaveLength(1);
});
test("durable deletion, Undo, expiry without tab, offline stale resurrection prevented", async () => {
  const factory = new IDBFactory(), a = repo("A", factory).repository, b = repo("B").repository, remote = new Remote(), original = record();
  await a.write(original, null, false); await remote.sync(a); await remote.sync(b);
  const stale = await b.read(original.id);
  await a.delete(original.id, original.token); await remote.sync(a); await remote.sync(b);
  expect((await b.list()).projects).toHaveLength(0);
  await a.undo(original.id); await remote.sync(a); expect(deletionState(remote.rows.get(original.id)!)).toBe("ACTIVE");
  await a.delete(original.id, original.token); await remote.sync(a);
  const deadline = remote.rows.get(original.id)!.undoUntil!;
  const now = Date.now; Date.now = () => deadline + 1;
  try {
    expect(deletionState(remote.rows.get(original.id)!)).toBe("DELETED");
    const restart = repo("A", factory).repository;
    expect((await restart.status()).undo).toHaveLength(0);
    await expect(restart.undo(original.id)).rejects.toMatchObject({ code: "LOCAL_PROJECT_UNAVAILABLE" });
    await b.write(edit(stale, "stale offline"), stale.token, false);
    await remote.sync(b);
    expect(deletionState(remote.rows.get(original.id)!)).toBe("DELETED");
    expect((await b.status()).conflicts).toHaveLength(1);
  } finally { Date.now = now; }
});
test("lost commit acknowledgement survives restart and newer local edits", async () => {
  const factory = new IDBFactory(), a = repo("A", factory).repository, remote = new Remote(), original = record();
  await a.write(original, null, false);
  const [sent] = await a.pending(); await a.markSending(sent!); await remote.commit(sent!);
  await a.write(edit(original, "second"), original.token, false);
  const restart = repo("A", factory).repository; await remote.sync(restart); await remote.sync(restart);
  expect(remote.rows.get(original.id)?.record.draft.name).toBe("second");
  expect((await restart.status()).conflicts).toHaveLength(0);
});
test("anonymous claim is durable, hidden from anonymous/B, interrupted import resumes for A", async () => {
  const factory = new IDBFactory(), anonymous = new IndexedDbProjectRepository(validate, undefined, () => factory);
  const one = record(); await anonymous.write(one, null, false);
  const claimed = await anonymous.claimAnonymous("account-a");
  expect((await anonymous.list()).projects).toHaveLength(0);
  expect(await anonymous.claimAnonymous("account-b")).toEqual([]);
  expect(await anonymous.claimAnonymous("account-a")).toEqual(claimed);
  await expect(anonymous.write(edit(one, "stale anonymous"), one.token, false)).rejects.toMatchObject({ code: "PROJECT_REVISION_CONFLICT" });
  const account = repo("A", factory).repository; await account.initialize(claimed);
  expect((await account.list()).projects).toHaveLength(1);
});
test("anonymous Latest collision preserves account Latest until named save or discard", async () => {
  const account = repo("A").repository, existing = record(undefined, false), imported = record(undefined, false);
  await account.write(existing, null, true); await account.initialize([imported]);
  expect(await account.missingImports([imported])).toHaveLength(1);
  expect((await account.list()).projects.map(p => p.id)).toEqual([existing.id]);
  await account.importLatest(imported, "Imported");
  expect((await account.read(imported.id)).checkpoint?.name).toBe("Imported");
  expect((await account.list()).projects.filter(p => p.kind === "LATEST").map(p => p.id)).toEqual([existing.id]);
  const discarded = record(undefined, false); await account.importLatest(discarded, null);
  await account.initialize([discarded]); expect(await account.missingImports([discarded])).toHaveLength(0);
  expect((await account.list()).projects).toHaveLength(2);
});

test("own sync acknowledgement never conflicts with the next edit", async () => {
  const a = repo("A").repository, remote = new Remote(), original = record();
  await a.write(original, null, false); await remote.sync(a);
  const second = edit(original, "second");
  await a.write(second, original.token, false); await remote.sync(a);
  await a.write(edit(second, "third"), second.token, false); await remote.sync(a);
  expect((await a.status()).conflicts).toHaveLength(0);
  expect(remote.rows.get(original.id)?.record.draft.name).toBe("third");
});

test("first sync unions cloud Latest before importing an anonymous Latest on the same device", async () => {
  const account = repo("A").repository, remote = new Remote();
  const previous = record(undefined, false), anonymous = record(undefined, false);
  const old = repo("A").repository;
  await old.write(previous, null, true); await remote.sync(old);
  const controller = new AccountSyncController(account,
    { account_id: "account-a", assertActive() {}, onClose() { return () => {}; } },
    async () => ({ list: async () => [...remote.rows.values()], commit: m => remote.commit(m),
      subscribe: () => () => {}, dispose() {} }), async () => [anonymous]);
  try {
    await controller.run();
    expect(controller.getState().imports.map(p => p.id)).toEqual([anonymous.id]);
    expect((await account.list()).projects.map(p => p.id)).toEqual([previous.id]);
    expect(remote.rows.has(anonymous.id)).toBe(false);
    await controller.importLatest(anonymous.id, "Imported");
    await controller.run();
    expect(remote.rows.get(anonymous.id)?.record.checkpoint?.name).toBe("Imported");
    expect(remote.rows.get(previous.id)?.latestDeviceId).toBe("A");
  } finally { controller.dispose(); }
});

test("reload retains the old checkpoint and autosaves before a remote revision conflict", async () => {
  const factory = new IDBFactory(), a = repo("A").repository, b = repo("B", factory).repository, remote = new Remote();
  const original = record();
  await a.write(original, null, false); await remote.sync(a); await remote.sync(b);
  const loaded = await b.read(original.id);
  const baseline = await b.captureWorking(original.id, loaded.token);
  const saved = edit(original, "new remote save"); saved.draft.revision = 1;
  saved.checkpoint = structuredClone(saved.draft);
  await a.write(saved, original.token, false); await remote.sync(a); await remote.sync(b);
  const reloaded = repo("B", factory).repository;
  await reloaded.restoreWorking({ version: 1, project: loaded.draft, durableToken: loaded.token,
    repositoryRecovery: baseline, last_calculation: loaded.lastCalculation } as any);
  const working = await reloaded.readWorking(original.id, loaded.token);
  expect(working.checkpoint?.revision).toBe(0);
  await reloaded.write(edit(working, "local after reload"), loaded.token, false);
  expect((await reloaded.read(original.id)).draft.name).toBe("local after reload");
  expect((await reloaded.status()).conflicts).toHaveLength(1);
});

for (const choice of ["remote", "both"] as const) test("resolution retry retains one atomic choice: " + choice, async () => {
  const a = repo("A").repository, b = repo("B").repository, remote = new Remote(), original = record();
  await a.write(original, null, false); await remote.sync(a); await remote.sync(b);
  const base = await b.read(original.id);
  let current = await a.read(original.id);
  await a.write(edit(current, "remote first"), current.token, false); await remote.sync(a);
  await b.write(edit(base, "local"), base.token, false); await remote.sync(b);
  const firstCopy = await b.resolve(original.id, "both");
  current = await a.read(original.id);
  await a.write(edit(current, "remote again"), current.token, false); await remote.sync(a);
  await remote.sync(b);
  expect((await b.status()).conflicts).toHaveLength(1);
  const selected = await b.resolve(original.id, choice);
  const groups = await b.pending();
  expect(groups).toHaveLength(1);
  expect(groups[0]).toHaveLength(choice === "both" ? 2 : 1);
  await remote.sync(b);
  expect(remote.rows.size).toBe(choice === "both" ? 2 : 1);
  expect(remote.rows.get(original.id)?.record.draft.name).toBe("remote again");
  if (choice === "both") { expect(selected).toBe(firstCopy); expect(remote.rows.get(selected)?.record.draft.name).toBe("local"); }
});

test("quota and backend outage preserve local writes and retry without idle full downloads", async () => {
  const local = repo("A").repository, remote = new Remote();
  let failure = "resource-exhausted", lists = 0;
  const controller = new AccountSyncController(local,
    { account_id: "account-a", assertActive() {}, onClose() { return () => {}; } },
    async () => ({ list: async () => { lists++; return [...remote.rows.values()]; },
      commit: async m => { if (failure) throw { code: failure }; return remote.commit(m); },
      subscribe: () => () => {}, dispose() {} }), async () => []);
  try {
    const original = record(); await local.write(original, null, false);
    await controller.run();
    await expect.poll(() => controller.getState().notice).toContain("他の端末");
    expect((await local.read(original.id)).lastCalculation).toEqual(original.lastCalculation);
    const modified = edit(original, "offline edit");
    await local.write(modified, original.token, false);
    failure = "unavailable"; await controller.run();
    expect((await local.read(original.id)).draft.name).toBe("offline edit");
    failure = ""; await controller.run(); await controller.run(); await controller.run();
    expect(remote.rows.get(original.id)?.record.draft.name).toBe("offline edit");
    expect(controller.getState().notice).toBe("");
    expect(lists).toBe(1);
  } finally { controller.dispose(); }
});

test("two tabs cannot pin an obsolete sent mutation over a newer local edit", async () => {
  const factory = new IDBFactory(), a = repo("A", factory).repository, b = repo("A", factory).repository;
  const remote = new Remote(), original = record();
  await a.write(original, null, false);
  const [stale] = await b.pending();
  await remote.sync(a);
  await a.write(edit(original, "newer"), original.token, false);
  expect(await b.markSending(stale!)).toBe(false);
  await remote.sync(b);
  expect(remote.rows.get(original.id)?.record.draft.name).toBe("newer");
});
test("another tab's acknowledgement advances the sent base without a false conflict", async () => {
  const a = repo("A").repository, remote = new Remote(), original = record();
  await a.write(original, null, false);
  const [sent] = await a.pending(); await a.markSending(sent!); await remote.commit(sent!);
  await a.write(edit(original, "newer"), original.token, false);
  await a.receive([...remote.rows.values()]);
  expect((await a.status()).conflicts).toHaveLength(0);
  await remote.sync(a);
  expect(remote.rows.get(original.id)?.record.draft.name).toBe("newer");
});
test("remote tombstone cannot replace the loaded session baseline before reload", async () => {
  const factory = new IDBFactory(), a = repo("A").repository, b = repo("B", factory).repository;
  const remote = new Remote(), original = record();
  await a.write(original, null, false); await remote.sync(a); await remote.sync(b);
  const loaded = await b.read(original.id), baseline = await b.captureWorking(original.id, loaded.token);
  await a.delete(original.id, original.token); await remote.sync(a); await remote.sync(b);
  expect(await b.captureWorking(original.id, loaded.token)).toEqual(baseline);
  const reloaded = repo("B", factory).repository;
  await reloaded.restoreWorking({ version: 1, project: loaded.draft, durableToken: loaded.token,
    repositoryRecovery: baseline, last_calculation: loaded.lastCalculation } as any);
  const working = await reloaded.readWorking(original.id, loaded.token);
  await reloaded.write(edit(working, "retained after deletion"), loaded.token, false);
  expect((await reloaded.read(original.id)).draft.name).toBe("retained after deletion");
  expect((await reloaded.status()).conflicts).toHaveLength(1);
});
test("anonymous Latest remains locally editable while initial cloud union is unavailable", async () => {
  const local = repo("A").repository, original = record(undefined, false);
  const controller = new AccountSyncController(local,
    { account_id: "account-a", assertActive() {}, onClose() { return () => {}; } },
    async () => { throw { code: "unavailable" }; }, async () => [original]);
  try {
    await controller.run();
    expect((await local.list()).projects.map(p => p.id)).toEqual([original.id]);
    const row = await local.read(original.id);
    await local.write(edit(row, "offline import edit"), row.token, true);
    expect((await local.read(original.id)).draft.name).toBe("offline import edit");
    expect(await local.pending()).toEqual([]);
    expect(controller.getState().imports).toEqual([]);
  } finally { controller.dispose(); }
});

test("a delayed server snapshot cannot roll back an acknowledged autosave", async () => {
  const a = repo("A").repository, remote = new Remote(), first = record();
  await a.write(first, null, false); await remote.sync(a);
  const delayed = structuredClone([...remote.rows.values()]);
  let current = first;
  for (const name of ["second", "third"]) {
    const updated = edit(current, name);
    await a.write(updated, current.token, false); await remote.sync(a); current = updated;
  }
  await a.receive(delayed);
  expect((await a.read(first.id)).draft.name).toBe("third");
  await a.write(edit(current, "fourth"), current.token, false); await remote.sync(a);
  expect(remote.rows.get(first.id)?.record.draft.name).toBe("fourth");
});
