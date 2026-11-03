import { enterImportWorkflow, confirmImportedDraft } from "./helpers/importWorkflow";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const fixture = resolve("../tests/fixtures/msm-portable");
const frozen = JSON.parse(readFileSync(resolve(fixture, "catalog.json"), "utf8"));
const state = (page: Page) => page.evaluate(() => (window as any).weatherStates.at(-1));
const record = (page: Page) => page.evaluate(() => new Promise<any>((resolve, reject) => {
  const opening = indexedDB.open("autonavlog.projects");
  opening.onerror = () => reject(opening.error);
  opening.onsuccess = () => {
    const db = opening.result;
    const request = db.transaction("projects").objectStore("projects").getAll();
    request.onsuccess = () => { resolve(request.result[0]); db.close(); };
  };
}));

async function setup(page: Page) {
  await page.goto("/");
  await enterImportWorkflow(page);
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await confirmImportedDraft(page);
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-16");
  await page.getByLabel("ETD JST", { exact: true }).fill("12:00");
  await page.getByLabel("気象モード").selectOption("FORECAST");
  for (const [name, altitude] of [["RJFM", "6500"], ["WP1", "7500"], ["WP2", "6500"]]) {
    const field = page.getByLabel(`${name}出発Legの計画高度`, { exact: true });
    await field.fill(altitude!); await field.blur();
  }
}
async function calculate(page: Page) {
  const previous = await page.evaluate(() => (window as any).weatherStates.length);
  const calculateButton = page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ });
  await calculateButton.click();
  // The forced draft save emits a current, last-good state before calculation.
  // Wait for the operation to finish, not merely for that intermediate RPC reply.
  await expect(page.getByText("NAV LOGを計算しました。準備状況と各値を確認してください。", { exact: true })).toBeVisible();
  await expect(calculateButton).toBeEnabled();
  await page.waitForFunction(count => (window as any).weatherStates.length > count, previous);
  await expect.poll(async () => (await state(page))?.readiness?.calculationIsCurrent).toBe(true);
  await expect(page.locator(".nav-log-table")).toBeVisible();
}


let delivery: "fresh" | "expired" | "offline" | "missing";
test.beforeEach(async ({ page, context }) => {
  delivery = "fresh";
  await context.route("**/api/**", route => {
    if (route.request().url().includes("/api/forecast-backup/jobs/")) return route.continue();
    throw new Error(`Guest accessed legacy API: ${route.request().url()}`);
  });
  await context.route("**/weather/msm/catalog.json", async route => {
    // A slow recovered feed must not let the previous result satisfy this test.
    if (delivery === "fresh") await new Promise(resolve => setTimeout(resolve, 1500));
    if (delivery === "offline") return route.abort("failed");
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...frozen,
      generated_at: new Date(Date.now() - 3600_000).toISOString(),
      expires_at: new Date(Date.now() + (delivery === "expired" ? -1000 : 3600_000)).toISOString(),
      ...(delivery === "missing" ? { assets: [] } : {}),
    }) });
  });
  await context.route("**/weather/msm/*.npz", route => route.fulfill({ contentType: "application/octet-stream",
    body: readFileSync(resolve(fixture, new URL(route.request().url()).pathname.split("/").at(-1)!)) }));
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
            } catch { /* other RPC */ }
          }
        });
      }
    };
  });
});
async function clearWeather(page: Page) {
  await page.evaluate(async () => { for (const key of await caches.keys())
    if (key.startsWith("autonavlog.weather.")) await caches.delete(key); });
}
for (const failure of ["expired", "offline", "missing"] as const) {
  test(`guest ${failure} delivery uses actual backup calculator and keeps Local save/reload`, async ({ page }) => {
    const puts: string[] = [];
    page.on("request", request => { if (request.method() === "PUT" && request.url().includes("/forecast-backup/")) puts.push(request.url()); });
    delivery = failure;
    await setup(page); await calculate(page);
    expect(puts).toHaveLength(1);
    const result = await state(page);
    expect(result.outcome.issues.map((item: any) => item.code)).toContain("FORECAST_BACKUP_USED");
    expect(result.outcome.selected_forecast_run_id).toBe("20260915210000");
    await expect.poll(async () => (await record(page))?.lastCalculation?.forecast_metadata?.calculation_source?.source)
      .toBe("forecast_backup_server");
    await page.getByLabel("プロジェクト", { exact: true }).fill("Guest backup fixture");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    // IndexedDB commits before the worker/session adopts the new revision and token.
    // Reload only after the complete save operation has returned to the UI.
    await expect(page.getByText("プロジェクトを保存しました。", { exact: true })).toBeVisible();
    await expect.poll(async () => (await record(page))?.checkpoint?.name).toBe("Guest backup fixture");
    await page.reload(); await expect(page.locator(".nav-log-table")).toBeVisible();
    expect((await record(page)).lastCalculation.outcome.issues.map((item: any) => item.code)).toContain("FORECAST_BACKUP_USED");
    delivery = "fresh"; await clearWeather(page); await calculate(page);
    expect(puts).toHaveLength(1);
    expect((await state(page)).outcome.issues.map((item: any) => item.code)).not.toContain("FORECAST_BACKUP_USED");
  });
}

test("normal then both paths fail preserves draft and Last Calculation", async ({ page, context }) => {
  await setup(page); await calculate(page);
  const before = (await record(page)).lastCalculation;
  delivery = "expired"; await clearWeather(page);
  await context.route("**/api/forecast-backup/jobs/**", route => route.fulfill({ status: 422,
    contentType: "application/json", body: JSON.stringify({ error: { code: "FORECAST_BACKUP_FAILED" } }) }));
  await page.getByLabel("ETD JST", { exact: true }).fill("12:01");
  const button = page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ });
  await button.click(); await expect(button).toBeEnabled();
  await expect(page.getByText(/MSM気象データの取得および予備サーバーでの計算に失敗しました/)).toBeVisible();
  expect((await record(page)).lastCalculation).toEqual(before);
  expect((await record(page)).draft.planned_departure_time_jst).toContain("12:01");
  await page.reload(); await expect(page.getByLabel("ETD JST", { exact: true })).toHaveValue("12:01");
  expect((await record(page)).lastCalculation).toEqual(before);
});
