import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";

const fixture = fileURLToPath(new URL("../../tests/fixtures/issue_43_golden.kml", import.meta.url));
const expectedHtml = readFileSync(new URL("../dist-local/index.html", import.meta.url), "utf8");
const expectedManifest = readFileSync(new URL("../dist-local/local/manifest.json", import.meta.url), "utf8");

test("Static Local workflow saves and restores a calculated Project across lifecycle changes", async ({ page, context, request }) => {
  // Bind this run to the prepared artifact, not a stale Vite server on the default port.
  const html = await request.get("/");
  const manifest = await request.get("/local/manifest.json");
  expect(html.ok()).toBe(true);
  expect(manifest.ok()).toBe(true);
  expect(await html.text()).toBe(expectedHtml);
  expect(await manifest.text()).toBe(expectedManifest);
  expect(expectedHtml).not.toContain("/@vite/client");
  const legacyRequests: string[] = [];
  context.on("request", request => {
    if (new URL(request.url()).pathname.startsWith("/api/")) legacyRequests.push(request.url());
  });
  await context.route("**/api/**", route => route.abort());
  await page.goto("/");
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByLabel("地上風速 kt").fill("15");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("270");
  await page.getByLabel("5,000 ft風速 kt").fill("30");
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await expect(page.locator(".route-workspace .leaflet-container")).toBeVisible();
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(`${name}出発Legの計画高度`, { exact: true }).fill(altitude);
  }
  await expect(page.locator(".status-rail")).toBeVisible();
  await page.getByRole("button", { name: "NAV LOGを作る", exact: true }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  const initialResult = await page.locator(".nav-log-table").innerText();
  const scroll = page.locator(".nav-log-scroll");
  await scroll.evaluate(element => { element.scrollLeft = 420; });
  expect(await scroll.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);

  await page.getByLabel("プロジェクト", { exact: true }).fill("Browser acceptance");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Projectをローカルへ保存しました" })).toBeVisible();
  const savedId = await page.getByLabel("保存済み", { exact: true }).inputValue();
  expect(savedId).not.toBe("");
  await page.reload();
  await expect(page.locator(".nav-log-table")).toHaveText(initialResult, { useInnerText: true });

  const other = await context.newPage();
  await other.goto("/");
  await expect(other.getByLabel("保存済み", { exact: true }).locator(`option[value="${savedId}"]`)).toHaveCount(1);
  await other.getByLabel("保存済み", { exact: true }).selectOption(savedId);
  await other.getByRole("button", { name: "保存済みProjectを開く", exact: true }).click();
  await expect(other.locator(".nav-log-table")).toHaveText(initialResult, { useInnerText: true });
  await page.bringToFront();
  await expect(page.locator(".nav-log-table")).toHaveText(initialResult, { useInnerText: true });
  expect(legacyRequests).toEqual([]);
});
