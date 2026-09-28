import { enterImportWorkflow } from "./helpers/importWorkflow";
import { expect, test, chromium, type Page, type BrowserContext } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const database = "autonavlog.projects";
const sessionKey = "autonavlog.working-session.v1";
const working = (page: Page) => page.evaluate(key => JSON.parse(sessionStorage.getItem(key)!).working, sessionKey);
async function records(page: Page): Promise<any[]> {
  return page.evaluate(name => new Promise<any[]>((resolve, reject) => {
    const open = indexedDB.open(name, 1);
    open.onsuccess = () => {
      const tx = open.result.transaction("projects");
      const read = tx.objectStore("projects").getAll();
      tx.oncomplete = () => { open.result.close(); resolve(read.result); };
      tx.onabort = () => reject(tx.error);
    };
  }), database);
}
async function put(page: Page, rows: any[]) {
  await page.evaluate(({ name, rows }) => new Promise<void>((resolve, reject) => {
    const open = indexedDB.open(name, 1);
    open.onsuccess = () => {
      const tx = open.result.transaction("projects", "readwrite");
      for (const row of rows) tx.objectStore("projects").put(row);
      tx.oncomplete = () => { open.result.close(); resolve(); };
      tx.onabort = () => reject(tx.error);
    };
  }), { name: database, rows });
}
async function start(page: Page) {
  await page.goto("/");
  await enterImportWorkflow(page);
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
}
async function confirm(page: Page) {
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByLabel("RJFM出発Legの計画高度", { exact: true })).toBeVisible();
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(`${name}出発Legの計画高度`, { exact: true }).fill(altitude);
  }
}
async function calculate(page: Page) {
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(`${name}出発Legの計画高度`, { exact: true }).fill(altitude);
  }
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await expect.poll(async () => (await records(page))[0]?.lastCalculation?.outcome).not.toBeNull();
}
async function save(page: Page, name: string) {
  await page.getByLabel("プロジェクト", { exact: true }).fill(name);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Projectをローカルへ保存しました" })).toBeVisible();
}
async function load(page: Page, id: string) {
  await page.getByLabel("保存済み", { exact: true }).selectOption(id);
  await page.getByRole("button", { name: "保存済みProjectを開く", exact: true }).click();
  await expect.poll(async () => (await working(page)).project?.id).toBe(id);
}
function blockApi(context: BrowserContext) {
  return context.route("**/api/**", route => route.abort());
}
test.beforeEach(async ({ context }) => { await blockApi(context); });

test("browser process restart retains named draft and Last Calculation, opens only explicitly", async ({ baseURL }) => {
  test.setTimeout(180_000);
  const profile = mkdtempSync(join(tmpdir(), "autonavlog-124-profile-"));
  let context = await chromium.launchPersistentContext(profile, { baseURL });
  const requests: string[] = [];
  const observe = async () => {
    await blockApi(context);
    context.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request.url()); });
  };
  try {
    await observe();
    let page = context.pages()[0]!;
    await start(page); await confirm(page); await calculate(page);
    const initial = (await records(page))[0];
    expect(initial.checkpoint).toBeNull();
    await save(page, "Restart route");
    const saved = (await records(page))[0];
    expect(saved.draft.revision).toBe(initial.draft.revision + 1);
    expect(Date.parse(saved.draft.updated_at)).toBeGreaterThan(Date.parse(initial.draft.updated_at));
    expect(saved.draft.updated_at).toBe(saved.checkpoint.updated_at);
    expect(saved.draft.updated_at).toBe(saved.updatedAt);
    expect((await working(page)).project.updated_at).toBe(saved.draft.updated_at);
    await page.getByLabel("FUEL gal", { exact: true }).fill("76");
    await expect.poll(async () => (await records(page))[0].draft.total_usable_fuel_gal).toBe(76);
    const edited = (await records(page))[0];
    expect(edited.checkpoint.total_usable_fuel_gal).toBe(90);
    expect(edited.draft.revision).toBe(saved.draft.revision);
    expect(edited.lastCalculation).toEqual(saved.lastCalculation);
    await page.evaluate(async () => {
      const cache = await caches.open("autonavlog.weather.test");
      await cache.put("/disposable-weather", new Response("weather"));
      await caches.delete("autonavlog.weather.test");
    });
    await context.close();
    context = await chromium.launchPersistentContext(profile, { baseURL });
    await observe();
    page = context.pages()[0]!;
    await start(page);
    expect((await working(page)).project).toBeNull();
    await expect(page.getByLabel("保存済み", { exact: true }).locator("option")).toHaveCount(2);
    // Loading is forbidden to send calculate RPC, even if weather is no longer available.
    await page.workers()[0]!.evaluate(() => {
      self.addEventListener("message", event => {
        if (event.data?.argumentList?.[0]?.value === "calculate") throw Error("Unexpected calculation on load");
      });
    });
    await load(page, saved.id);
    await expect(page.locator(".nav-log-table")).toBeVisible();
    expect((await working(page)).last_calculation).toEqual(saved.lastCalculation);
    await expect(page.getByLabel("FUEL gal", { exact: true })).toHaveValue("76");
    // Invalid ephemeral edits win over the durable validated draft on same-tab reload.
    await page.getByLabel("FUEL gal", { exact: true }).fill("");
    await page.reload();
    await expect(page.getByLabel("FUEL gal", { exact: true })).toHaveValue("");
    expect((await working(page)).project.id).toBe(saved.id);
    expect((await records(page))[0].draft.total_usable_fuel_gal).toBe(76);
    expect(requests).toEqual([]);
  } finally { await context.close(); }
});

test("Latest replacement, promotion, opening another Project and explicit delete", async ({ page }) => {
  await start(page); await confirm(page);
  const first = (await records(page))[0];
  await save(page, "Named");
  page.on("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "新規", exact: true }).click();
  await page.getByRole("button", { name: "KML/KMZから開始", exact: true }).click();
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await confirm(page);
  expect((await records(page)).filter(row => !row.checkpoint)).toHaveLength(1);
  const oldLatest = (await working(page)).project.id;
  await page.getByRole("button", { name: "新規", exact: true }).click();
  await page.getByRole("button", { name: "KML/KMZから開始", exact: true }).click();
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await confirm(page);
  const replaced = await records(page);
  expect(replaced).toHaveLength(2);
  expect(replaced.some(row => row.id === oldLatest)).toBe(false);
  await load(page, first.id);
  expect((await records(page)).map(row => row.id)).toEqual([first.id]);
  await page.getByRole("button", { name: "保存済みProjectを削除", exact: true }).click();
  await expect.poll(async () => (await records(page)).length).toBe(0);
  await expect.poll(async () => (await working(page)).project).toBeNull();
});

test("quota evicts only Weather cache, preserves last good records, and notifies failure", async ({ page }) => {
  await start(page); await confirm(page); await calculate(page); await save(page, "Safe");
  const before = (await records(page))[0];
  await page.evaluate(async () => {
    for (const name of ["autonavlog.weather.fixture", "unrelated-cache"]) {
      const cache = await caches.open(name); await cache.put("/sample", new Response("data"));
    }
    const original = IDBObjectStore.prototype.put;
    (window as any).restorePut = () => { IDBObjectStore.prototype.put = original; };
    IDBObjectStore.prototype.put = function () { throw new DOMException("injected quota", "QuotaExceededError"); };
  });
  await page.getByLabel("FUEL gal", { exact: true }).fill("73");
  await expect(page.getByRole("alert")).toContainText(/保存/);
  expect(await page.evaluate(() => caches.keys())).toEqual(["unrelated-cache"]);
  expect((await records(page))[0]).toEqual(before);
  await expect(page.getByLabel("FUEL gal", { exact: true })).toHaveValue("73");
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await page.evaluate(() => (window as any).restorePut());
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(async () => (await records(page))[0].draft.total_usable_fuel_gal).toBe(73);
  expect((await records(page))[0].lastCalculation).toEqual(before.lastCalculation);
});

test("migration and corruption are isolated per Project, originals survive failed migration writes", async ({ page }) => {
  await start(page); await confirm(page); await save(page, "Good");
  const good = (await records(page))[0];
  const old = structuredClone(good), corrupt = structuredClone(good);
  old.schemaVersion = 1; delete old.updatedAt;
  old.id = old.draft.id = old.checkpoint.id = crypto.randomUUID();
  old.token = crypto.randomUUID(); old.draft.name = old.checkpoint.name = "Old";
  corrupt.id = corrupt.draft.id = corrupt.checkpoint.id = crypto.randomUUID();
  corrupt.schemaVersion = 999;
  await put(page, [old, corrupt]);
  await page.goto("/");
  await enterImportWorkflow(page); // fresh navigation, explicit selection only
  await expect(page.getByRole("alert")).toContainText("一部のProject");
  expect((await records(page)).find(row => row.id === corrupt.id)).toEqual(corrupt);
  await page.evaluate(() => {
    const original = IDBObjectStore.prototype.put;
    (window as any).restorePut = () => { IDBObjectStore.prototype.put = original; };
    IDBObjectStore.prototype.put = function () { throw new DOMException("quota", "QuotaExceededError"); };
  });
  await page.getByLabel("保存済み", { exact: true }).selectOption(old.id);
  await page.getByRole("button", { name: "保存済みProjectを開く", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "端末への保存に失敗" })).toBeVisible();
  expect((await records(page)).find(row => row.id === old.id)).toEqual(old);
  expect((await working(page)).project).toBeNull();
  await page.evaluate(() => (window as any).restorePut());
  await load(page, old.id);
  expect((await records(page)).find(row => row.id === old.id).schemaVersion).toBe(2);
  await load(page, good.id);
  await save(page, "Still usable");
  expect((await records(page)).find(row => row.id === corrupt.id)).toEqual(corrupt);
});


test("Forecast snapshot loads with its pinned Run and provenance without calculation or Weather cache", async ({ page }) => {
  const native = JSON.parse(execFileSync(process.env.AUTONAVLOG_REFERENCE_PYTHON ?? resolve("../.venv/bin/python"),
    [resolve("../scripts/local_reference.py"), "--forecast", "--pinned"], { encoding: "utf8" }));
  const workingCopy = native.workingRecovery;
  for (const project of [workingCopy.project, workingCopy.last_calculation.project]) delete project.metadata.web_owner_id;
  await page.addInitScript(() => {
    (window as any).calculateRequests = 0;
    const original = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function (message: any, ...args: any[]) {
      if (["calculate", "updateAndRecalculate"].includes(message?.argumentList?.[0]?.value)) (window as any).calculateRequests++;
      return original.call(this, message, ...args as [any]);
    };
    Object.defineProperty(navigator.storage, "persist", { value: () => Promise.reject(new Error("denied")) });
  });
  await start(page);
  await expect(page.getByRole("alert")).toHaveCount(0);
  const record = { schemaVersion: 2, id: workingCopy.project.id, token: crypto.randomUUID(),
    checkpoint: workingCopy.project, draft: workingCopy.project, lastCalculation: workingCopy.last_calculation,
    updatedAt: new Date().toISOString() };
  await put(page, [record]);
  await page.goto("/");
  await enterImportWorkflow(page);
  await expect(page.getByLabel("保存済み", { exact: true }).locator("option")).toHaveCount(2);
  await load(page, record.id);
  await expect(page.locator(".nav-log-table")).toBeVisible();
  const restored = await working(page);
  expect(restored.project.selected_forecast_run_id).toBe(record.draft.selected_forecast_run_id);
  expect(restored.last_calculation).toEqual(record.lastCalculation);
  expect(await page.evaluate(() => (window as any).calculateRequests)).toBe(0);
  expect(await page.evaluate(() => caches.keys())).toEqual([]);
});

test("a stale selector cannot delete a Project updated by another tab", async ({ page, context }) => {
  await start(page); await confirm(page); await save(page, "Shared");
  const saved = (await records(page))[0];
  const other = await context.newPage();
  await start(other);
  expect((await working(other)).project).toBeNull();
  await page.getByLabel("FUEL gal", { exact: true }).fill("74");
  await expect.poll(async () => (await records(page))[0].draft.total_usable_fuel_gal).toBe(74);
  other.on("dialog", dialog => dialog.accept());
  await other.getByLabel("保存済み", { exact: true }).selectOption(saved.id);
  await other.getByRole("button", { name: "保存済みProjectを削除", exact: true }).click();
  await expect(other.getByRole("alert")).toContainText("別のタブでProject");
  expect((await records(page))[0].draft.total_usable_fuel_gal).toBe(74);
  await other.reload();
  await expect(other.getByLabel("保存済み", { exact: true }).locator("option")).toHaveCount(2);
  await other.getByLabel("保存済み", { exact: true }).selectOption(saved.id);
  await other.getByRole("button", { name: "保存済みProjectを削除", exact: true }).click();
  await expect.poll(async () => (await records(other)).length).toBe(0);
});

for (const edit of ["navlog", "pattern"] as const) {
test(`failed updateAndRecalculate (${edit}) commits session recovery and can autosave after reload`, async ({ page }) => {
  // Fail inside Python calculation, after the real update has committed its validated draft.
  await page.route("**/assets/local.worker-*.js", async route => {
    const response = await route.fetch();
    const source = await response.text();
    const initialization = 'local_application = LocalApplication(Path("/home/pyodide/data"))';
    expect(source).toContain(initialization);
    await route.fulfill({ response, body: source.replace(initialization, `${initialization}
_original_calculate = local_application.app._calculate_outcome
def _fail_recalculation(*args, **kwargs):
    if local_path == "updateAndRecalculate":
        from autonavlog.web.facade import WebApplicationError
        raise WebApplicationError("TEST_CALCULATION_FAILED", "Injected calculation failure")
    return _original_calculate(*args, **kwargs)
local_application.app._calculate_outcome = _fail_recalculation
`) });
  });
  await start(page); await confirm(page); await calculate(page); await save(page, "Failure recovery");
  const before = (await records(page))[0];
  const field = () => edit === "navlog"
    ? page.locator(".nav-log-table").getByLabel(/手動TAS$/).first()
    : page.getByLabel("今回採用する場周経路高度");
  const value = edit === "navlog" ? "120" : "1400";
  await field().fill(value);
  await expect(page.getByText(/Injected calculation failure/)).toBeVisible();
  const failed = (await records(page))[0];
  expect(failed.token).not.toBe(before.token);
  expect(failed.draft.sections).not.toEqual(before.draft.sections);
  expect(failed.lastCalculation).toEqual(before.lastCalculation);
  await expect.poll(async () => (await working(page)).durableToken).toBe(failed.token);
  expect((await working(page)).project).toEqual(failed.draft);
  await expect(field()).toHaveValue(value);
  await page.reload();
  await expect(field()).toHaveValue(value);
  expect((await working(page)).durableToken).toBe(failed.token);
  await page.getByLabel("FUEL gal", { exact: true }).fill("72");
  await expect.poll(async () => (await records(page))[0].draft.total_usable_fuel_gal).toBe(72);
  const after = (await records(page))[0];
  expect(after.token).not.toBe(failed.token);
  expect(after.lastCalculation).toEqual(before.lastCalculation);
  await expect(page.getByRole("alert")).toHaveCount(0);
});
}
