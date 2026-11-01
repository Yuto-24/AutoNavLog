import { test, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { resolve } from "node:path";

const python = process.env.AUTONAVLOG_REFERENCE_PYTHON ?? "python3";
const root = resolve("..");
const fixture = (forecast = false) => JSON.parse(execFileSync(python, ["-c", `
import json
from test_local_calculation import LocalApplication, calculate, ROOT
from autonavlog.local_persistence import migrate_record
from uuid import uuid4
app=LocalApplication(ROOT/'data', forecast_fixture=ROOT/'tests/fixtures/msm')
w=calculate(app,forecast=${forecast ? "True" : "False"})['workingRecovery'];p=w['project']
r={'schemaVersion':2,'id':p['id'],'token':str(uuid4()),'checkpoint':p,'draft':p,'lastCalculation':w['last_calculation'],'updatedAt':p['updated_at']}
print(migrate_record(r).model_dump_json());app.close()
`], { cwd: root, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONPATH: `${root}/src:${root}/tests/integration` } }));
const oldShape = (record: any) => {
  const copy = structuredClone(record);
  for (const project of [copy.draft, copy.checkpoint, copy.lastCalculation?.project]) {
    if (project) for (const node of project.route_nodes) delete node.reference_snapshot;
  }
  return copy;
};
async function start(page: Page, options: any) {
  await page.goto("/e2e/storage-upgrade-harness/index.html");
  await expect(page).toHaveTitle("Project storage upgrade fixture");
  await expect(page.locator("#ready")).toHaveText("Ready");
  await page.evaluate(options => (window as any).upgrade.init(options), options);
}
const call = (page: Page, operation: string, ...args: any[]): Promise<any> =>
  page.evaluate(({ operation, args }) => (window as any).upgrade[operation](...args), { operation, args });

test("historical adapters are unchanged pinned 1.20.1 source", () => {
  const provenance = JSON.parse(readFileSync(resolve("e2e/fixtures/v1.20.1/provenance.json"), "utf8"));
  expect(provenance.commit).toBe("a4de2a226dfa205b8be42fb62a710a1b06271ed8");
  for (const [name, hash] of Object.entries(provenance.files)) {
    expect(createHash("sha256").update(readFileSync(resolve("e2e/fixtures/v1.20.1", name))).digest("hex")).toBe(hash);
  }
});

test("same-profile old tab cannot delete new Latest; reload and fresh SDK device retain Saved, Latest and calculation", async ({ page: old, context, browser }) => {
  const account = "upgrade-" + randomUUID(), subject = "upgrade-" + randomUUID();
  const saved = fixture(), initial = oldShape(saved);
  await start(old, { old: true, account, subject });
  await call(old, "write", initial, null); await call(old, "flush");
  const loaded = await call(old, "read", initial.id);
  await old.evaluate(value => sessionStorage.setItem("old-session", JSON.stringify(value)), initial);
  const next = await context.newPage(); await start(next, { account, subject });
  await call(next, "list"); // Upgrade commits before the first new-schema write.
  const latest = fixture(true); latest.checkpoint = null;
  const snapshot = JSON.parse(readFileSync(resolve("../data/reference/vrep.json"), "utf8"))[0];
  latest.draft.route_nodes[1].reference_snapshot = snapshot;
  await call(next, "write", latest, null, true); await call(next, "flush");
  const before = await call(next, "snapshot"), cloud = await call(next, "remoteList");
  for (const [operation, args] of [
    ["open", [loaded]], ["write", [{ ...initial, token: randomUUID() }, initial.token]],
    ["delete", [initial.id, initial.token]], ["pending", []],
  ] as [string, any[]][]) expect(await call(old, "attempt", operation, args)).toBe("LOCAL_STORAGE_FAILED");
  expect(await call(next, "snapshot")).toEqual(before);
  expect(await call(next, "remoteList")).toEqual(cloud);
  expect(await old.evaluate(() => JSON.parse(sessionStorage.getItem("old-session")!))).toEqual(initial);
  await next.reload(); await next.evaluate(options => (window as any).upgrade.init(options), { account, subject });
  expect((await call(next, "read", latest.id)).lastCalculation).toEqual(latest.lastCalculation);
  expect((await call(next, "read", latest.id)).draft.route_nodes[1].reference_snapshot).toEqual(snapshot);
  const fresh = await browser.newContext(), device = await fresh.newPage();
  try {
    await start(device, { account, subject, device: "fresh-device" }); await call(device, "flush");
    expect((await call(device, "read", saved.id)).checkpoint).toEqual(initial.checkpoint);
    expect((await call(device, "read", latest.id)).lastCalculation).toEqual(latest.lastCalculation);
    expect((await call(device, "list")).projects).toHaveLength(2);
  } finally { await call(device, "dispose"); await fresh.close(); await call(next, "dispose"); await call(old, "dispose"); }
});

for (const sameProfile of [true, false]) test(`delayed old outbox cannot overwrite new data (${sameProfile ? "shared" : "different"} profile)`, async ({ page: old, context, browser }) => {
  const account = "inflight-" + randomUUID(), subject = "inflight-" + randomUUID();
  const record = fixture(), initial = oldShape(record);
  await start(old, { old: true, account, subject, device: "old-device" });
  await call(old, "write", initial, null); await call(old, "flush");
  const edit = { ...initial, token: randomUUID(), draft: { ...initial.draft, pilot_name: "old pending edit" } };
  await call(old, "write", edit, initial.token); await call(old, "holdPending");
  const targetContext = sameProfile ? context : await browser.newContext(), next = await targetContext.newPage();
  try {
    await start(next, { account, subject, device: "new-device" }); await call(next, "flush");
    const base = await call(next, "read", record.id);
    const updated = { ...record, token: randomUUID(), draft: { ...record.draft, pilot_name: "new saved work" } };
    await call(next, "write", updated, base.token); await call(next, "flush");
    const before = await call(next, "remoteList");
    expect(await call(old, "attempt", "finishPending", [])).toBe("old domain reader");
    expect(await call(next, "remoteList")).toEqual(before);
    expect((await call(next, "read", record.id)).draft.pilot_name).toBe("new saved work");
    const local = await call(old, "snapshot");
    // Shared-profile pending was replayed/acknowledged by new code before the newer edit.
    // A different profile retains its unacknowledged original for recovery.
    if (!sameProfile) expect(local[0].sync.sent.value.record.draft.pilot_name).toBe("old pending edit");
  } finally { await call(next, "dispose"); await call(old, "dispose"); if (!sameProfile) await targetContext.close(); }
});

test("upgrade waits for an old write transaction and preserves its committed record", async ({ page: old, context }) => {
  await start(old, { old: true }); const record = oldShape(fixture()); await call(old, "write", record, null);
  await call(old, "hold", false);
  const next = await context.newPage(); await start(next, {}); await call(next, "startUpgrade");
  await expect.poll(() => old.evaluate(() => (window as any).upgrade.status.versionchange)).toBe(true);
  expect(await next.evaluate(() => (window as any).upgrade.status.upgraded)).toBe(false);
  await call(old, "release");
  await expect.poll(() => old.evaluate(() => (window as any).upgrade.status.transactionComplete)).toBe(true);
  await call(next, "list");
  expect((await call(next, "read", record.id)).draft.pilot_name).toBe("transaction completed before upgrade");
  expect((await call(next, "read", record.id)).lastCalculation).toEqual(record.lastCalculation);
  expect(await call(old, "attempt", "write", [{ ...record, token: randomUUID() }, record.token])).toBe("LOCAL_STORAGE_FAILED");
});

test("blocked upgrade fails without changing original rows and can retry after the old connection closes", async ({ page: old, context }) => {
  await start(old, { old: true }); const record = oldShape(fixture()); await call(old, "write", record, null);
  const before = await call(old, "snapshot"); await call(old, "hold", true);
  const next = await context.newPage(); await start(next, {});
  expect(await call(next, "attempt", "list", [])).toBe("LOCAL_STORAGE_FAILED");
  expect(await call(old, "heldSnapshot")).toEqual(before);
  await call(old, "closeBlocker"); await call(next, "list");
  expect(await call(next, "snapshot")).toEqual(before);
});

for (const sameProfile of [true, false]) test(`old SDK commit already read before new save is rejected (${sameProfile ? "shared" : "different"} profile)`, async ({ page: old, context, browser }) => {
  const account = "rpc-" + randomUUID(), subject = "rpc-" + randomUUID();
  const record = fixture(), initial = oldShape(record);
  await start(old, { old: true, account, subject, device: "old-device" });
  await call(old, "write", initial, null); await call(old, "flush");
  const edit = { ...initial, token: randomUUID(), draft: { ...initial.draft, pilot_name: "started old RPC" } };
  await call(old, "write", edit, initial.token); await call(old, "holdPending");
  let commitObserved!: () => void, releaseCommit!: () => void;
  const observed = new Promise<void>(resolve => { commitObserved = resolve; });
  const released = new Promise<void>(resolve => { releaseCommit = resolve; });
  await old.route("http://127.0.0.1:8088/**/documents:commit?*", async route => {
    const body = route.request().postDataJSON();
    // The real SDK has completed its read and built an updateTime precondition.
    expect(body.writes[0].currentDocument.updateTime).toBeTruthy();
    commitObserved(); await released; await route.continue();
  });
  await call(old, "startPending"); await observed;
  const targetContext = sameProfile ? context : await browser.newContext(), next = await targetContext.newPage();
  try {
    await start(next, { account, subject, device: "new-device" }); await call(next, "flush");
    const base = await call(next, "read", record.id);
    const updated = { ...record, token: randomUUID(), draft: { ...record.draft, pilot_name: "newer SDK commit" } };
    await call(next, "write", updated, base.token); await call(next, "flush");
    const before = await call(next, "remoteList");
    releaseCommit();
    // Rules can reject the stale base before the SDK updateTime precondition is checked.
    expect(["permission-denied", "aborted", "failed-precondition"]).toContain(await call(old, "attempt", "finishStartedPending", []));
    expect(await call(next, "remoteList")).toEqual(before);
    expect((await call(next, "read", record.id)).draft.pilot_name).toBe("newer SDK commit");
    if (!sameProfile) expect((await call(old, "snapshot"))[0].sync.sent.value.record.draft.pilot_name).toBe("started old RPC");
  } finally {
    releaseCommit(); await call(next, "dispose"); await call(old, "dispose");
    if (!sameProfile) await targetContext.close();
  }
});
