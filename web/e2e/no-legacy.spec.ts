import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { readFileSync, readdirSync } from "node:fs";
import { backend, waitForSyncAccount } from "./helpers/sync-auth";
import { enterImportWorkflow } from "./helpers/importWorkflow";

// Enable the real activation callback; its missing URL must short-circuit itself.
const entry = "/e2e/auth-harness/index.html?sync&migration";
async function observe(context: BrowserContext, attempts: string[]) {
  context.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/") || /legacy|(?:autonavlog|navlog)\.yuto24/.test(url.hostname)) {
      attempts.push(`${url.origin}${url.pathname}`);
    }
  });
  await backend(context);
  await context.route(/https?:\/\/[^/]*(?:legacy|(?:autonavlog|navlog)\.yuto24)[^/]*\//, route => route.abort());
}
async function login(page: Page, subject: string) {
  await page.goto(entry);
  await expect.poll(() => page.evaluate(() => Boolean((window as any).authTest))).toBe(true);
  await page.evaluate(subject => (window as any).authTest.signIn(subject), subject);
  await waitForSyncAccount(page, subject);
  await enterImportWorkflow(page);
}
async function rows(page: Page) {
  return page.evaluate(async () => (window as any).authTest.contexts.at(-1).repository.rows());
}
async function synced(page: Page, name: string, fuel = 90) {
  await expect.poll(async () => (await rows(page)).some((r: any) =>
    r.draft.name === name && r.draft.total_usable_fuel_gal === fuel && !r.sync.dirty)).toBe(true);
}
async function open(page: Page, name: string, calculation = true) {
  await expect(page.locator("#saved-project option").filter({ hasText: name })).toHaveCount(1);
  await page.getByLabel("保存済み", { exact: true }).selectOption({ label: name });
  await page.getByRole("button", { name: "保存済みプロジェクトを開く" }).click();
  if (calculation) await expect(page.locator(".nav-log-table")).toBeVisible();
}
async function create(page: Page, name: string) {
  await enterImportWorkflow(page);
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  for (const [node, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    const input = page.getByLabel(`${node}出発Legの計画高度`, { exact: true });
    await input.click(); await input.fill(altitude!);
  }
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await page.getByLabel("プロジェクト", { exact: true }).fill(name);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator("#saved-project option").filter({ hasText: name })).toHaveCount(1);
}

test("no Legacy: reload, fresh-device restoration, two-way sync, offline save and account boundary", async ({ page, context, browser }, info) => {
  const attempts: string[] = [];
  await observe(context, attempts);
  const subject = `no-legacy-${crypto.randomUUID()}`;
  await login(page, subject);
  expect(await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith("autonavlog.migration.active.")))).toEqual([]);
  await create(page, "Existing saved route");
  await synced(page, "Existing saved route");
  const first = (await rows(page)).find((r: any) => r.draft.name === "Existing saved route");
  expect(first.lastCalculation).toBeTruthy();
  // Reload preserves the same origin/profile and its IndexedDB. Do not destroy it.
  await page.reload(); await waitForSyncAccount(page, subject); await enterImportWorkflow(page);
  await open(page, "Existing saved route");
  expect((await rows(page)).find((r: any) => r.id === first.id)?.lastCalculation).toEqual(first.lastCalculation);

  // A new nonpersistent context starts empty; it tests remote restore, not disk persistence.
  const freshContext = await browser.newContext({ viewport: page.viewportSize()!, serviceWorkers: "block" });
  await observe(freshContext, attempts);
  const fresh = await freshContext.newPage();
  try {
    await fresh.goto(entry);
    await expect.poll(() => fresh.evaluate(() => Boolean((window as any).authTest))).toBe(true);
    expect(await fresh.evaluate(() => (window as any).authTest.state().account)).toBeNull();
    expect(await fresh.evaluate(() => indexedDB.databases().then(dbs =>
      dbs.some(db => db.name?.startsWith("autonavlog.projects.account_"))))).toBe(false);
    await login(fresh, subject); await open(fresh, "Existing saved route");
    const restored = (await rows(fresh)).find((r: any) => r.id === first.id);
    expect(restored.lastCalculation).toEqual(first.lastCalculation);
    await freshContext.setOffline(true);
    await fresh.getByLabel("FUEL gal", { exact: true }).fill("78");
    await fresh.getByRole("button", { name: "保存", exact: true }).click();
    await expect.poll(async () => (await rows(fresh)).some((r: any) =>
      r.id === first.id && r.draft.total_usable_fuel_gal === 78 && r.sync.dirty)).toBe(true);
    expect((await rows(page)).find((r: any) => r.id === first.id)?.draft.total_usable_fuel_gal).toBe(90);
    // Retain the offline context and its local data until reconnect and acknowledgement.
    await freshContext.setOffline(false); await synced(fresh, "Existing saved route", 78);
    await synced(page, "Existing saved route", 78);
    await open(page, "Existing saved route", false);
    await page.getByLabel("FUEL gal", { exact: true }).fill("76");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await synced(page, "Existing saved route", 76); await synced(fresh, "Existing saved route", 76);
    await fresh.evaluate(() => (window as any).authTest.signOut());
    await login(fresh, `other-${subject}`);
    expect((await rows(fresh)).some((r: any) => r.id === first.id)).toBe(false);
    await expect(fresh.locator("#saved-project option").filter({ hasText: "Existing saved route" })).toHaveCount(0);
    await create(fresh, "Other account route");
    await synced(fresh, "Other account route");
    expect((await rows(page)).some((r: any) => r.draft.name === "Other account route")).toBe(false);
    await fresh.evaluate(() => (window as any).authTest.signOut());
    await login(fresh, subject); await synced(fresh, "Existing saved route", 76);
    expect((await rows(fresh)).some((r: any) => r.draft.name === "Other account route")).toBe(false);
  } finally {
    await freshContext.close();
    await info.attach("legacy-request-attempts", { body: JSON.stringify(attempts), contentType: "application/json" });
    expect(attempts).toEqual([]);
  }
});

test("no Legacy Local: synthetic authentication, same-context reload and offline account save", async ({ page, context }, info) => {
  const attempts: string[] = [];
  await observe(context, attempts);
  await page.goto("/e2e/auth-harness/index.html?migration");
  await expect.poll(() => page.evaluate(() => Boolean((window as any).authTest))).toBe(true);
  await page.evaluate(() => (window as any).authTest.signIn("local-no-legacy"));
  await waitForSyncAccount(page, "local-no-legacy");
  await create(page, "Local saved route");
  await page.reload(); await waitForSyncAccount(page, "local-no-legacy");
  await enterImportWorkflow(page); await open(page, "Local saved route");
  await context.setOffline(true);
  await page.getByLabel("FUEL gal", { exact: true }).fill("78");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => page.evaluate(async () => {
    const app = (window as any).authTest.contexts.at(-1);
    const listing = await app.repository.list();
    const item = listing.projects.find((r: any) => r.name === "Local saved route");
    return item ? (await app.repository.read(item.id)).draft.total_usable_fuel_gal : null;
  })).toBe(78);
  await context.setOffline(false);
  await page.reload(); await waitForSyncAccount(page, "local-no-legacy");
  await enterImportWorkflow(page); await open(page, "Local saved route", false);
  await expect(page.getByLabel("FUEL gal", { exact: true })).toHaveValue("78");
  await page.evaluate(() => (window as any).authTest.signOut());
  await page.evaluate(() => (window as any).authTest.signIn("other-local-no-legacy"));
  await waitForSyncAccount(page, "other-local-no-legacy"); await enterImportWorkflow(page);
  await expect(page.locator("#saved-project option").filter({ hasText: "Local saved route" })).toHaveCount(0);
  await info.attach("legacy-request-attempts", { body: JSON.stringify(attempts), contentType: "application/json" });
  expect(attempts).toEqual([]);
});

test("no Legacy artifact: omitted configuration, anonymous boot and saved calculation reload", async ({ page, context }, info) => {
  const release = JSON.parse(readFileSync("dist-static/release.json", "utf8"));
  expect(release.configuration).not.toHaveProperty("VITE_LEGACY_MIGRATION_URL");
  for (const file of readdirSync("dist-static/assets").filter(f => f.endsWith(".js"))) {
    const source = readFileSync(`dist-static/assets/${file}`, "utf8");
    expect(source).not.toContain("navlog.yuto24.com");
    expect(source).not.toContain("VITE_LEGACY_MIGRATION_URL");
  }
  const attempts: string[] = [];
  await observe(context, attempts);
  await page.goto("http://127.0.0.1:4188/");
  await create(page, "Static local route");
  await page.reload(); await enterImportWorkflow(page); await open(page, "Static local route");
  await expect(page.getByLabel("FUEL gal", { exact: true })).toHaveValue("90");
  await info.attach("legacy-request-attempts", { body: JSON.stringify(attempts), contentType: "application/json" });
  expect(attempts).toEqual([]);
});
