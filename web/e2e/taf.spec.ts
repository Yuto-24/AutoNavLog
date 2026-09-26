import { enterImportWorkflow } from "./helpers/importWorkflow";
import { expect, test, type Page } from "@playwright/test";
import { resolve } from "node:path";

// Wind provenance/status reflects availability; adopted navigation values must not change.
const navigation = (outcome: any) => outcome.sections.map((section: any) => Object.fromEntries(
  Object.entries(section).map(([key, value]: [string, any]) => [key,
    value && typeof value === "object" && "automatic_value" in value
      ? [value.automatic_value, value.manual_override, value.adopted_source] : value]),
));
const working = (page: Page) => page.evaluate(() => JSON.parse(sessionStorage.getItem("autonavlog.working-session.v1")!).working);
const records = (page: Page): Promise<any[]> => page.evaluate(() => new Promise((resolve, reject) => {
  const open = indexedDB.open("autonavlog.projects", 1);
  open.onsuccess = () => {
    const tx = open.result.transaction("projects");
    const read = tx.objectStore("projects").getAll();
    tx.oncomplete = () => { open.result.close(); resolve(read.result); };
    tx.onabort = () => reject(tx.error);
  };
}));

test("Static Local TAF success, quota/timeout/outage and recovery preserve navigation and saved work", async ({ page, context }) => {
  test.setTimeout(240_000);
  // Keep this TAF regression's historical MSM input explicit, as in #125's
  // calculation regression. Production and #144 use the real Weather adapter.
  await context.route("**/assets/local.worker-*.js", async route => {
    const response = await route.fetch();
    const source = await response.text();
    const initialization = 'local_application = LocalApplication(Path("/home/pyodide/data"))';
    expect(source).toContain(initialization);
    await route.fulfill({ response, body: source.replace(initialization,
      'local_application = LocalApplication(Path("/home/pyodide/data"), forecast_fixture=Path("/home/pyodide/data/msm-fixture"))',
    ) });
  });
  let mode = "available";
  let count = 0;
  const apiRequests: string[] = [];
  context.on("request", request => { if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url()); });
  await context.route("**/api/**", route => route.abort());
  await context.route("**/taf?icao=*", async route => {
    count++;
    const icao = new URL(route.request().url()).searchParams.get("icao");
    expect(icao).toBe("RJFS");
    if (mode === "outage") return route.abort();
    if (mode === "timeout") { await new Promise(resolve => setTimeout(resolve, 7500)); return route.abort().catch(() => undefined); }
    if (mode === "quota") return route.fulfill({ status: 503, contentType: "text/html", body: "Cloudflare 1027" });
    const from = Date.parse("2026-09-12T00:00:00Z") / 1000;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify([{
      icaoId: icao, mostRecent: 1, validTimeFrom: from, validTimeTo: from + 86400,
      rawTAF: "TAF RJFS test fixture", fcsts: [{ timeFrom: from, timeTo: from + 86400, wdir: 240, wspd: 18 }],
    }]) });
  });
  await page.goto("/");
  await enterImportWorkflow(page);
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-12");
  await page.getByLabel("ETD JST", { exact: true }).fill("12:00");
  await page.getByLabel("気象モード").selectOption("FORECAST");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    const input = page.getByLabel(`${name}出発Legの計画高度`, { exact: true });
    await input.fill(altitude); await input.blur();
  }
  await page.getByRole("button", { name: "NAV LOGを作る", exact: true }).click();
  await expect(page.getByRole("region", { name: "目的地空港の風予報" })).toContainText("240/18 kt");
  const initial = await working(page);
  expect(count).toBe(1);
  await page.getByLabel("プロジェクト", { exact: true }).fill("TAF retained");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(async () => (await records(page))[0]?.checkpoint?.name).toBe("TAF retained");
  const saved = (await records(page))[0];
  expect(count).toBe(1);
  for (const failure of ["quota", "timeout", "outage"]) {
    mode = failure;
    await page.getByRole("button", { name: "NAV LOGを再計算", exact: true }).click();
    await expect.poll(async () => (await working(page)).destination_wind?.reason_code).toBe(
      failure === "timeout" ? "TAF_FETCH_TIMEOUT" : "TAF_FETCH_FAILED");
    await expect(page.getByRole("region", { name: "目的地空港の風予報" })).toContainText("取得できませんでした");
    const current = await working(page);
    expect(navigation(current.outcome)).toEqual(navigation(initial.outcome));
    expect(current.outcome.fuel_plan).toEqual(initial.outcome.fuel_plan);
    const persisted = (await records(page))[0];
    expect(persisted.id).toBe(saved.id);
    expect(persisted.checkpoint).toEqual(saved.checkpoint);
    expect(navigation(persisted.lastCalculation.outcome)).toEqual(navigation(initial.outcome));
  }
  const beforeReload = (await records(page))[0];
  await page.reload();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  expect(count).toBe(4);
  expect((await records(page))[0]).toEqual(beforeReload);
  expect((await working(page)).project.id).toBe(saved.id);
  mode = "available";
  await page.getByRole("button", { name: "NAV LOGを再計算", exact: true }).click();
  await expect(page.getByRole("region", { name: "目的地空港の風予報" })).toContainText("240/18 kt");
  expect(count).toBe(5);
  // NAV LOG edits use updateAndRecalculate, which must also tolerate a stopped Proxy.
  mode = "outage";
  const tas = page.locator(".nav-log-table").getByLabel(/手動TAS$/).first();
  await tas.fill("120"); await tas.blur();
  await expect.poll(async () => (await working(page)).destination_wind?.reason_code).toBe("TAF_FETCH_FAILED");
  expect(count).toBe(6);
  expect((await records(page))[0].id).toBe(saved.id);
  expect((await working(page)).outcome).not.toBeNull();
  expect(apiRequests).toEqual([]);
});
