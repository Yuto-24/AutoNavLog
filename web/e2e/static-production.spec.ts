import { expect, test } from "@playwright/test";

test("production artifact shows storage limits and uses no Service Worker", async ({ page, context }) => {
  const api: string[] = [];
  context.on("request", request => {
    if (new URL(request.url()).pathname.startsWith("/api/")) api.push(request.url());
  });
  await context.route("**/api/**", route => route.abort());
  await page.goto("/");
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^Information/ }).click();
  await expect(page.getByText(/容量不足による自動削除/)).toBeVisible();
  expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length)).toBe(0);
  expect(api).toEqual([]);
});

test("production artifact rejects a different build manifest and recovers on reload", async ({ page, context }) => {
  await context.route("**/api/**", route => { throw new Error("Unexpected Legacy request: " + route.request().url()); });
  await page.goto("/");
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await page.getByLabel("FUEL gal", { exact: true }).fill("76");
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("autonavlog.working-session.v1"))).toContain("76");
  await context.route("**/local/manifest.json", async route => {
    const response = await route.fetch();
    const manifest = await response.json();
    // All package hashes remain internally valid: only a build binding detects this.
    await route.fulfill({ response, json: { ...manifest, version: "other-release" } });
  });
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("画面を再読み込みしてください");
  await context.unroute("**/local/manifest.json");
  await page.reload();
  await expect(page.getByLabel("FUEL gal", { exact: true })).toHaveValue("76");
});

test("production artifact calculates with its served real MSM feed and retains NAV LOG on acquisition failure", async ({ page, context, request }) => {
  const release = await (await request.get("/release.json")).json();
  const catalog = await (await request.get("/weather/msm/catalog.json")).json();
  expect(Date.parse(catalog.expires_at)).toBeGreaterThan(Date.now());
  const downloads: string[] = [];
  context.on("request", r => { if (r.url().includes("/weather/msm/")) downloads.push(r.url()); });
  await context.route("**/api/**", route => { throw new Error("Unexpected Legacy request: " + route.request().url()); });
  // Local origin is deliberately absent from the production TAF allowlist.
  // Exercise the configured adapter's failure isolation, without changing the remote Worker.
  await context.route(release.configuration.VITE_TAF_PROXY_URL + "*",
    route => route.fulfill({ status: 503, body: "quota fixture" }));
  await page.goto("/");
  const departure = new Date(Date.now() + 10 * 3600_000).toISOString(); // JST, one hour ahead
  await page.getByLabel("DATE", { exact: true }).fill(departure.slice(0, 10));
  await page.getByLabel("ETD JST", { exact: true }).fill(departure.slice(11, 16));
  await page.getByLabel("気象モード").selectOption("FORECAST");
  await page.locator('input[type="file"]').setInputFiles("../tests/fixtures/issue_43_golden.kml");
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(`${name}出発Legの計画高度`, { exact: true }).fill(altitude!);
  }
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  expect(downloads.some(url => url.endsWith(".npz"))).toBe(true);
  const navlog = await page.locator(".nav-log-table").innerText();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Projectをローカルへ保存しました" })).toBeVisible();
  await page.reload();
  await expect(page.locator(".nav-log-table")).toHaveText(navlog, { useInnerText: true });
  await page.evaluate(async () => {
    for (const key of await caches.keys()) if (key.startsWith("autonavlog.weather.")) await caches.delete(key);
  });
  await context.route("**/weather/msm/catalog.json", route => route.fulfill({ status: 503, body: "outage" }));
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator(".nav-log-table")).toHaveText(navlog, { useInnerText: true });
});
