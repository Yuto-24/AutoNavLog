import { expect, test, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

async function expectNavLogAtViewportTop(page: Page) {
  const navLog = page.getByLabel("計算済みNAV LOG");
  const topLimit = await page.evaluate(() => window.innerHeight / 3);
  await expect(navLog).toBeFocused();
  await expect.poll(() => navLog.evaluate((element) => element.getBoundingClientRect().top))
    .toBeGreaterThanOrEqual(0);
  await expect.poll(() => navLog.evaluate((element) => element.getBoundingClientRect().top))
    .toBeLessThan(topLimit);
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
}

test("first Local NAV LOG calculation and recalculation scroll to the result", async ({ page }) => {
  await page.addInitScript(() => {
    const original = Element.prototype.scrollIntoView;
    const originalPostMessage = Worker.prototype.postMessage;
    (window as any).navLogScrolls = 0;
    (window as any).navLogScrollsAtCalculationStart = -1;
    Worker.prototype.postMessage = function (message: any, ...args: any[]) {
      if (message?.type === "APPLY" && message.argumentList?.[0]?.value === "calculate") {
        (window as any).navLogScrollsAtCalculationStart = (window as any).navLogScrolls;
      }
      return (originalPostMessage as any).call(this, message, ...args);
    };
    Element.prototype.scrollIntoView = function (...args) {
      if (this.getAttribute("aria-label") === "計算済みNAV LOG") {
        (window as any).navLogScrolls += 1;
      }
      return original.apply(this, args);
    };
  });
  await page.goto("/");
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByLabel("地上風速 kt").fill("15");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("270");
  await page.getByLabel("5,000 ft風速 kt").fill("30");
  await page.locator('input[type="file"]').setInputFiles(fileURLToPath(new URL("../../tests/fixtures/issue_43_golden.kml", import.meta.url)));
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(`${name}出発Legの計画高度`, { exact: true }).fill(altitude);
  }

  await expect(page.getByLabel("計算済みNAV LOG")).toHaveCount(0);
  await page.getByRole("button", { name: "NAV LOGを作る", exact: true }).click();
  await expectNavLogAtViewportTop(page);
  expect(await page.evaluate(() => (window as any).navLogScrollsAtCalculationStart)).toBe(0);
  expect(await page.evaluate(() => (window as any).navLogScrolls)).toBe(1);
  const firstTable = await page.locator(".nav-log-table").innerText();

  await page.evaluate(() => window.scrollTo(0, 0));
  await expect.poll(() => page.getByLabel("計算済みNAV LOG").evaluate((element) => element.getBoundingClientRect().top))
    .toBeGreaterThan(await page.evaluate(() => window.innerHeight / 3));
  await page.getByRole("button", { name: "NAV LOGを再計算", exact: true }).click();
  await expectNavLogAtViewportTop(page);
  expect(await page.evaluate(() => (window as any).navLogScrollsAtCalculationStart)).toBe(1);
  expect(await page.evaluate(() => (window as any).navLogScrolls)).toBe(2);
  await expect(page.locator(".nav-log-table")).toHaveText(firstTable, { useInnerText: true });
});
