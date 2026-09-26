import { expect, test, type Page } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  page.on("console", message => { if (message.type() === "error") console.log(message.text()); });
  await page.addInitScript(() => {
    const Original = Worker;
    window.Worker = class extends Original {
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options);
        this.addEventListener("message", event => {
          if (event.data?.name === "throw") console.error("Local worker failure", String(event.data.value?.value?.message ?? event.data.value));
        });
      }
    };
  });
});

async function start(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路作成", exact: true })).toBeVisible({ timeout: 90_000 });
}
const strip = (page: Page) => page.getByRole("list", { name: "Route Strip" });
const airport = (page: Page, icao: string) => page.locator(`.airport-draft-marker[title^="空港 ${icao} "]`);
async function pick(page: Page, latitude: number, longitude: number) {
  const view = await page.evaluate(() => JSON.parse(localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last")!));
  const frame = (await page.locator(".route-map").boundingBox())!;
  const project = (lat: number, lon: number) => {
    const scale = 256 * 2 ** view.zoom;
    const sine = Math.sin(lat * Math.PI / 180);
    return [scale * (lon + 180) / 360, scale * (0.5 - Math.log((1 + sine) / (1 - sine)) / (4 * Math.PI))];
  };
  const target = project(latitude, longitude), center = project(view.latitude, view.longitude);
  await page.mouse.click(frame.x + frame.width / 2 + target[0]! - center[0]!, frame.y + frame.height / 2 + target[1]! - center[1]!);
}
for (const width of [1100, 1440]) {
  test(`MAP to Planning and NAV LOG, reload, save and reopen at ${width}px`, async ({ page }, info) => {
    test.setTimeout(360_000);
    const local = info.config.metadata.applicationMode === "local";
    const api: string[] = [];
    if (local) await page.context().route("**/api/**", route => { api.push(route.request().url()); return route.abort(); });
    await page.setViewportSize({ width, height: 1100 });
    await start(page);
    await expect(page.getByLabel("DATE", { exact: true })).toHaveCount(0);
    const draftInput = (await page.locator(".input-rail").boundingBox())!;
    const draftRoute = (await page.locator(".route-workspace").boundingBox())!;
    if (width <= 1240) expect(draftInput.y + draftInput.height).toBeLessThanOrEqual(draftRoute.y + 2);
    else expect(draftInput.x + draftInput.width).toBeLessThanOrEqual(draftRoute.x + 2);
    const before = await page.locator(".route-map").boundingBox();
    expect(before!.height).toBeGreaterThan(500);
    const viewport = await page.evaluate(() => localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last"));
    await airport(page, "RJFM").click();
    await expect(strip(page).locator("li")).toHaveCount(1);
    expect(await page.evaluate(() => localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last"))).toBe(viewport);
    await page.getByRole("button", { name: "Zoom out" }).click();
    await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last")!).zoom)).toBe(8);
    await page.getByRole("button", { name: "Zoom out" }).click();
    await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last")!).zoom)).toBe(7);
    await pick(page, 32.115635, 130.337678);
    await expect(strip(page)).toContainText("WP1");
    await pick(page, 32.893463, 130.536873);
    await pick(page, 33.029605, 130.444088);
    await expect(strip(page).locator("li")).toHaveCount(4);
    await expect(page.getByRole("button", { name: "経路を確定", exact: true })).toBeDisabled();
    await page.locator(".route-workspace").screenshot({ path: info.outputPath("map-draft.png") });
    await page.reload();
    await expect(page.getByText("AutoNavLogを起動しています", { exact: true })).toHaveCount(0, { timeout: 120_000 });
    await expect(strip(page).locator("li")).toHaveCount(4, { timeout: 90_000 });
    await airport(page, "RJFS").click();
    await expect(strip(page).locator("li")).toHaveCount(5);
    await page.getByRole("button", { name: "経路を確定", exact: true }).click();
    await expect(page.getByLabel("DATE", { exact: true })).toBeInViewport();
    await expect(page.locator(".route-workspace.is-route-building")).toHaveCount(0);
    const input = (await page.locator(".input-rail").boundingBox())!;
    const route = (await page.locator(".route-workspace").boundingBox())!;
    const readiness = (await page.locator(".status-rail").boundingBox())!;
    if (width <= 1240) { expect(input.y + input.height).toBeLessThanOrEqual(route.y + 2); expect(route.y + route.height).toBeLessThanOrEqual(readiness.y + 2); }
    else { expect(input.x + input.width).toBeLessThanOrEqual(route.x + 2); expect(route.x + route.width).toBeLessThanOrEqual(readiness.x + 2); }
    await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
    await page.getByLabel("気象モード").selectOption("FTD");
    for (const [name, altitude] of [["RJFM", "6500"], ["WP1", "7500"], ["WP2", "6500"]]) {
      await page.getByLabel(`${name}出発Legの計画高度`, { exact: true }).fill(altitude!);
    }
    await page.getByRole("button", { name: "チェックポイントを追加", exact: true }).click();
    const cp = page.locator(".checkpoint-form");
    await cp.getByLabel("名称", { exact: true }).fill("MAP CP");
    await cp.getByLabel("緯度", { exact: true }).fill("32.0");
    await cp.getByLabel("経度", { exact: true }).fill("130.9");
    await cp.getByRole("button", { name: "追加", exact: true }).click();
    await expect(page.getByRole("button", { name: "MAP CPを編集", exact: true })).toBeVisible();
    await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
    await expect(page.locator(".nav-log-table")).toBeVisible();
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "Projectをローカルへ保存しました" })).toBeVisible();
    await page.reload();
    await expect(page.getByText("AutoNavLogを起動しています", { exact: true })).toHaveCount(0, { timeout: 120_000 });
    await expect(page.locator(".nav-log-table")).toBeVisible();
    await expect(page.locator(".route-workspace.is-route-building")).toHaveCount(0);
    const projectId = await page.getByLabel("保存済み", { exact: true }).inputValue();
    const savedView = await page.evaluate(id => localStorage.getItem(`autonavlog.map-viewport.v1.anonymous.project.${id}`), projectId);
    page.once("dialog", dialog => dialog.accept());
    await page.getByRole("button", { name: "新規", exact: true }).click();
    await expect(page.getByRole("heading", { name: "経路作成", exact: true })).toBeVisible({ timeout: 90_000 });
    expect(await page.evaluate(() => localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last"))).toBe(savedView);
    await airport(page, "RJFM").click();
    await page.getByRole("button", { name: "Zoom in" }).click();
    await page.getByLabel("保存済み", { exact: true }).selectOption(projectId);
    page.once("dialog", dialog => dialog.dismiss());
    await page.getByRole("button", { name: "保存済みProjectを開く", exact: true }).click();
    await expect(strip(page).locator("li")).toHaveCount(1);
    page.once("dialog", dialog => dialog.accept());
    await page.getByRole("button", { name: "保存済みProjectを開く", exact: true }).click();
    await expect(page.locator(".nav-log-table")).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last"))).toBe(savedView);
    // A synced/older Project without a device preference must not open at another route.
    await page.evaluate(id => {
      localStorage.removeItem(`autonavlog.map-viewport.v1.anonymous.project.${id}`);
      localStorage.setItem("autonavlog.map-viewport.v1.anonymous.last", JSON.stringify({ latitude: 35.68, longitude: 139.76, zoom: 12 }));
    }, projectId);
    await page.reload();
    await expect(page.locator(".nav-log-table")).toBeVisible({ timeout: 120_000 });
    await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last")!).longitude)).toBeLessThan(132);
    const fitted = await page.evaluate(() => localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last"));
    await page.reload();
    await expect(page.locator(".nav-log-table")).toBeVisible({ timeout: 120_000 });
    expect(await page.evaluate(() => localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last"))).toBe(fitted);
    page.once("dialog", dialog => dialog.accept());
    await page.getByRole("button", { name: "新規", exact: true }).click();
    await expect(page.getByRole("heading", { name: "経路作成", exact: true })).toBeVisible();
    await page.evaluate(id => {
      localStorage.removeItem(`autonavlog.map-viewport.v1.anonymous.project.${id}`);
      localStorage.setItem("autonavlog.map-viewport.v1.anonymous.last", JSON.stringify({ latitude: 35.68, longitude: 139.76, zoom: 12 }));
    }, projectId);
    await page.reload();
    await expect(page.getByRole("heading", { name: "経路作成", exact: true })).toBeVisible({ timeout: 120_000 });
    await page.getByLabel("保存済み", { exact: true }).selectOption(projectId);
    await page.getByRole("button", { name: "保存済みProjectを開く", exact: true }).click();
    await expect(page.locator(".nav-log-table")).toBeVisible();
    await expect.poll(async () => page.evaluate(() => JSON.parse(localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last")!).longitude)).toBeLessThan(132);
    expect(api).toEqual([]);
    await page.screenshot({ path: info.outputPath("map-route.png"), fullPage: true });
  });
}

test("repeated airport occurrence removal, clear and new-work cancellation", async ({ page }) => {
  await start(page);
  await airport(page, "RJFM").click();
  await pick(page, 31.98, 131.3);
  await airport(page, "RJFM").click();
  await pick(page, 32.01, 131.3);
  await airport(page, "RJFM").click();
  await expect(strip(page).locator("li")).toHaveCount(5);
  await page.getByRole("button", { name: "3番目のRJFMを削除" }).click();
  await expect(strip(page).locator("li")).toHaveCount(4);
  await expect(strip(page).locator("li").last()).toContainText("RJFM");
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "経路をクリア" }).click();
  await expect(strip(page).locator("li")).toHaveCount(4);
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "新規", exact: true }).click();
  await expect(strip(page).locator("li")).toHaveCount(4);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "経路をクリア" }).click();
  await expect(strip(page)).toHaveCount(0);
});


test("direct airport route survives failed local persistence and can be saved", async ({ page }, info) => {
  test.skip(info.config.metadata.applicationMode !== "local", "Local repository failure path");
  test.setTimeout(180_000);
  await start(page);
  const initialView = await page.evaluate(() => JSON.parse(localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last")!));
  expect(initialView.latitude).toBeCloseTo(31.8772, 2);
  expect(initialView.longitude).toBeCloseTo(131.4486, 2);
  await airport(page, "RJFM").click();
  await page.getByRole("button", { name: "Zoom out" }).click();
  await airport(page, "RJFK").click();
  await expect(strip(page).locator("li")).toHaveCount(2);
  await page.evaluate(() => {
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function(...args) {
      if (this.name === "projects") {
        IDBObjectStore.prototype.put = put;
        throw new DOMException("injected disk failure", "UnknownError");
      }
      return put.apply(this, args);
    };
  });
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toBeInViewport();
  await expect(page.locator(".route-table tbody tr")).toHaveCount(2);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Projectをローカルへ保存しました" })).toBeVisible();
  await page.reload();
  await expect(page.getByText("AutoNavLogを起動しています", { exact: true })).toHaveCount(0, { timeout: 120_000 });
  await expect(page.locator(".route-table tbody tr")).toHaveCount(2);
});
