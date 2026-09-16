import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { compare } from "./helpers/compare";
import { calculationCoreGolden } from "./helpers/localGolden";

const fixture = resolve("../tests/fixtures/msm-portable");
const frozen = JSON.parse(readFileSync(resolve(fixture, "catalog.json"), "utf8"));
const reference = (pinned = false) => JSON.parse(execFileSync(
  process.env.AUTONAVLOG_REFERENCE_PYTHON ?? resolve("../.venv/bin/python"),
  [resolve("../scripts/local_reference.py"), "--feed", fixture, ...(pinned ? ["--pinned"] : [])],
  { encoding: "utf8" },
));
const state = (page: Page) => page.evaluate(() => (window as any).weatherStates.at(-1));
const record = (page: Page) => page.evaluate(() => new Promise<any>((resolve, reject) => {
  const opening = indexedDB.open("autonavlog.projects", 1);
  opening.onerror = () => reject(opening.error);
  opening.onsuccess = () => {
    const db = opening.result;
    const request = db.transaction("projects").objectStore("projects").getAll();
    request.onsuccess = () => { resolve(request.result[0]); db.close(); };
  };
}));

async function setup(page: Page) {
  await page.goto("/");
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-16");
  await page.getByLabel("ETD JST", { exact: true }).fill("12:00");
  await page.getByLabel("気象モード").selectOption("FORECAST");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    const field = page.getByLabel(`${name}出発Legの計画高度`, { exact: true });
    await field.fill(altitude!); await field.blur();
  }
}
async function calculate(page: Page) {
  const previous = await page.evaluate(() => (window as any).weatherStates.length);
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await page.waitForFunction(count => (window as any).weatherStates.length > count, previous);
  await expect.poll(async () => (await state(page))?.readiness?.calculationIsCurrent).toBe(true);
  await expect(page.locator(".nav-log-table")).toBeVisible();
}

test.beforeEach(async ({ page, context }) => {
  await context.route("**/api/**", route => { throw new Error(`Local called Legacy: ${route.request().url()}`); });
  await context.route("**/database.rish.kyoto-u.ac.jp/**", route => {
    throw new Error(`Offline CI called Weather source: ${route.request().url()}`);
  });
  await context.route("**/weather/msm/catalog.json", route => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ ...frozen,
      generated_at: new Date(Date.now() - 1000).toISOString(),
      expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    }),
  }));
  await context.route("**/weather/msm/*.npz", route => route.fulfill({
    contentType: "application/octet-stream",
    body: readFileSync(resolve(fixture, new URL(route.request().url()).pathname.split("/").at(-1)!)),
  }));
  await page.addInitScript(() => {
    (window as any).weatherStates = [];
    const Original = window.Worker;
    window.Worker = class extends Original {
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options);
        this.addEventListener("message", event => {
          if (typeof event.data.value === "string") {
            try { const parsed = JSON.parse(event.data.value);
              if (parsed.workingRecovery || parsed.error) (window as any).weatherStates.push(parsed);
            } catch { /* other RPC result */ }
          }
        });
      }
    };
  });
});

test("portable real MSM: cold, warm after reload, Python reference and saved NAV LOG after eviction", async ({ page, context }) => {
  const downloads: string[] = [];
  context.on("request", r => { if (r.url().includes("/weather/msm/")) downloads.push(r.url()); });
  await setup(page); await calculate(page);
  compare(calculationCoreGolden(await state(page)), calculationCoreGolden(reference()));
  expect((await state(page)).outcome.selected_forecast_run_id).toBe("20260915210000");
  expect(downloads.filter(url => url.endsWith(".npz"))).toHaveLength(1);
  const before = (await record(page)).lastCalculation;
  await page.reload();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await calculate(page);
  expect(downloads.filter(url => url.endsWith(".npz"))).toHaveLength(1);
  expect(downloads.filter(url => url.endsWith("catalog.json"))).toHaveLength(1);
  await page.evaluate(async () => { for (const key of await caches.keys())
    if (key.startsWith("autonavlog.weather.")) await caches.delete(key); });
  await page.reload();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  expect((await record(page)).lastCalculation.outcome.summary).toEqual(before.outcome.summary);
});

test("saved Run stays fixed while a newer Run is available", async ({ page }) => {
  const native = reference(true);
  const recovery = native.workingRecovery;
  for (const project of [recovery.project, recovery.last_calculation.project]) delete project.metadata.web_owner_id;
  await page.goto("/");
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await page.evaluate(recovery => new Promise<void>((resolve, reject) => {
    const opening = indexedDB.open("autonavlog.projects", 1);
    opening.onsuccess = () => {
      const db = opening.result;
      const tx = db.transaction("projects", "readwrite");
      tx.objectStore("projects").put({ schemaVersion: 2, id: recovery.project.id,
        token: crypto.randomUUID(), updatedAt: new Date().toISOString(),
        checkpoint: recovery.project, draft: recovery.project, lastCalculation: recovery.last_calculation });
      tx.oncomplete = () => { db.close(); resolve(); };
      tx.onabort = () => reject(tx.error);
    };
  }), recovery);
  await page.goto("/");
  await page.getByLabel("保存済み", { exact: true }).selectOption(recovery.project.id);
  await page.getByRole("button", { name: "保存済みProjectを開く", exact: true }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await calculate(page);
  expect((await state(page)).outcome.selected_forecast_run_id).toBe("20260915180000");
  expect((await state(page)).outcome.issues.map((i: any) => i.code)).toContain("FORECAST_UPDATE_AVAILABLE");
  compare(calculationCoreGolden(await state(page)), calculationCoreGolden(native));
});

test("Project quota retry releases the actual Weather cache and keeps Last Calculation", async ({ page }) => {
  await setup(page); await calculate(page);
  const before = await record(page);
  expect(await page.evaluate(() => caches.keys())).toContain("autonavlog.weather.msm.v1");
  await page.evaluate(() => {
    const put = IDBObjectStore.prototype.put;
    let fail = true;
    IDBObjectStore.prototype.put = function (...args: Parameters<typeof put>) {
      if (fail) { fail = false; throw new DOMException("quota", "QuotaExceededError"); }
      return put.apply(this, args);
    };
  });
  await page.getByLabel("プロジェクト", { exact: true }).fill("Weather quota");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(async () => (await record(page)).checkpoint?.name).toBe("Weather quota");
  expect(await page.evaluate(() => caches.keys())).not.toContain("autonavlog.weather.msm.v1");
  expect((await record(page)).lastCalculation).toEqual(before.lastCalculation);
});

for (const failure of ["listing", "communication", "missing", "integrity", "decode", "expired"] as const) {
  test(`failure ${failure} is explicit and never selects Legacy`, async ({ page, context }) => {
    if (failure === "listing") await context.route("**/weather/msm/catalog.json", route => route.fulfill({ status: 503, body: "" }));
    if (failure === "communication") await context.route("**/weather/msm/catalog.json", route => route.abort());
    if (failure === "missing") await context.route("**/weather/msm/*.npz", route => route.fulfill({ status: 404, body: "" }));
    if (failure === "integrity" || failure === "decode") {
      const { createHash } = await import("node:crypto");
      const bad = Buffer.from("not a portable payload");
      const digest = createHash("sha256").update(bad).digest("hex");
      await context.route("**/weather/msm/*.npz", route => route.fulfill({ body: bad }));
      if (failure === "decode") await context.route("**/weather/msm/catalog.json", route => route.fulfill({
        contentType: "application/json", body: JSON.stringify({ ...frozen,
          generated_at: new Date(Date.now() - 1000).toISOString(), expires_at: new Date(Date.now() + 3600000).toISOString(),
          assets: frozen.assets.map((a: any) => ({ ...a, file: `${digest}.npz`, sha256: digest, bytes: bad.length })),
        }),
      }));
    }
    if (failure === "expired") await context.route("**/weather/msm/catalog.json", route => route.fulfill({
      body: JSON.stringify({ ...frozen, generated_at: "2026-01-01T00:00:00Z", expires_at: "2026-01-01T01:00:00Z" }),
    }));
    await setup(page);
    const before = await record(page);
    await page.getByRole("button", { name: "NAV LOGを作る", exact: true }).click();
    const code = { listing: "WEATHER_DISCOVERY_FAILED", communication: "WEATHER_COMMUNICATION_FAILED",
      missing: "WEATHER_SOURCE_UNAVAILABLE", integrity: "WEATHER_PAYLOAD_INTEGRITY_FAILED",
      decode: "WEATHER_PAYLOAD_INTEGRITY_FAILED", expired: "WEATHER_CATALOG_EXPIRED" }[failure];
    await expect.poll(async () => (await state(page))?.error?.code).toBe(code);
    const after = await record(page);
    expect(after.draft.id).toBe(before.draft.id);
    expect(after.draft.sections.slice(0, 3).map((s: any) => s.planned_altitude_ft_msl)).toEqual([6500, 7500, 6500]);
    expect((await record(page)).lastCalculation).toBeNull();
  });
}
