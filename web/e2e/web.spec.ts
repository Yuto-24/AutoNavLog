import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

const kml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document><Placemark><name>RJFM-RJFO</name><LineString><coordinates>
    131.4486111111,31.8772222222,0
    131.5000000000,32.4000000000,0
    131.6500000000,33.1000000000,0
    131.7000000000,33.4000000000,0
    131.7372222222,33.4794444444,0
  </coordinates></LineString></Placemark></Document>
</kml>`;

const multiDocumentKmz = Buffer.from(
  "UEsDBBQAAAAIAG0kCl36V3yWdAAAAJwAAAAJAAAAZmlyc3Qua21sTY1BCgMhDEWvMsx6MKi7kuYEXRR6ApmmU1HjoAF7/NKu3H14vPcxlbx8SpZ+Xd+q5wVgjGHqyXLEboQVUsngjFsJ7znsXEJLhBIK0yu2rgj/jbco/NAW5SDca23PKEG5k/V283ax3m3eIcwIYZZgyv9O6QtQSwMEFAAAAAgAbSQKXeGCuSN0AAAAnQAAAAoAAABzZWNvbmQua21sTY1BCsMgEEWvErIODuouTOcEXRR6AjFDIuoYVLDHL+3K5efx3seY0/LJSdpjvXq/d4Axhio3yxmaEu4QcwKjzEr4Ss5zdjUSistMjX2RA+E/8BmE370GOQl9KfUI4jo30lZvVi/ams0ahBkhzBJM/d8rfQFQSwECFAMUAAAACABtJApd+ld8lnQAAACcAAAACQAAAAAAAAAAAAAAgAEAAAAAZmlyc3Qua21sUEsBAhQDFAAAAAgAbSQKXeGCuSN0AAAAnQAAAAoAAAAAAAAAAAAAAIABmwAAAHNlY29uZC5rbWxQSwUGAAAAAAIAAgBvAAAANwEAAAAA",
  "base64",
);

async function calculateNavLog(page: Page): Promise<void> {
  const openPaste = page.getByRole("button", { name: "KMLを貼り付け" });
  await openPaste.click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  const textbox = dialog.getByRole("textbox");
  await expect(textbox).toBeFocused();
  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("Tab");
    await expect(dialog.locator(":focus")).toHaveCount(1);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(openPaste).toBeFocused();

  await openPaste.click();
  await textbox.fill(kml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();

  await expect(page.getByLabel("飛行経路にする形状")).toHaveValue("line:0");
  await expect(page.getByLabel("TO")).toHaveValue(/RJFO/);
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  await expect(page.getByText("VREP", { exact: true }).first()).toBeVisible();
  const altitudeInputs = page.locator(".table-number-input");
  await expect(altitudeInputs.first()).toHaveValue("");
  await expect(altitudeInputs.last()).toHaveValue("1500");
  for (let index = 0; index < await altitudeInputs.count(); index += 1) {
    await altitudeInputs.nth(index).fill("4500");
  }
  const cruiseAltitude = page.getByLabel(/出発Legの巡航高度候補/).first();
  const cruiseCandidate = await cruiseAltitude.locator("option").first().getAttribute("value");
  if (cruiseCandidate === null) {
    throw new Error("Cruise altitude candidate is missing");
  }
  await cruiseAltitude.selectOption(cruiseCandidate);
  const patternAltitude = page.getByLabel("今回採用する場周経路高度");
  const confirmDestination = page.getByRole("button", {
    name: "目的空港・場周高度を確定",
  });
  await expect(patternAltitude).toHaveValue("1000");
  await patternAltitude.fill("");
  await expect(confirmDestination).toBeDisabled();
  await patternAltitude.fill("1000");
  await expect(confirmDestination).toBeEnabled();
  await patternAltitude.fill("1300");
  await expect(patternAltitude).toHaveValue("1300");
  await confirmDestination.click();
  await expect(patternAltitude).toHaveValue("1300");
  await expect(altitudeInputs.first()).toHaveValue("4500");
  await expect(altitudeInputs.last()).toHaveValue("1800");
  await expect(cruiseAltitude).toHaveValue(cruiseCandidate);
  await expect(page.locator(".altitude-review-row")).toHaveCount(0);
  await page.getByRole("button", { name: "NAV LOGを作る" }).click();
  await expect(page.getByLabel("計算済みNAV LOG")).toBeFocused();
  const firstRow = page.locator(".nav-log-table .nav-leg-detail-row").first();
  await expect(firstRow.locator("td").nth(2)).toHaveText("4500");
}

test("desktop workflow renders and stays fail-closed", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await expect(page.locator("body")).not.toBeEmpty();
  await expect(page.getByText("開発用固定気象（出力不可）", { exact: true })).toBeVisible();
  await expect(page.getByLabel("TO")).toHaveValue("");
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0);
  await page.screenshot({
    path: path.join(repositoryRoot, "docs/web-design/implementation-desktop.png"),
    fullPage: false,
  });

  await calculateNavLog(page);

  await expect(
    page.getByText("PATTERN_ALTITUDE_REQUIRED", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("DEVELOPMENT_WEATHER_PROVIDER", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "A4転記補助HTMLを出力" })).toBeDisabled();
  await page.screenshot({
    path: path.join(repositoryRoot, "docs/web-design/implementation-calculated.png"),
    fullPage: true,
  });

  expect(pageErrors).toEqual([]);
});

test("changed ALT appears in PA with lesson display precision", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("/");
  await calculateNavLog(page);

  await expect(
    page.getByLabel("ALT・Phase・FUEL・VAR・TGLを原資料と照合しました"),
  ).toHaveCount(0);
  const calculate = page.getByRole("button", { name: "NAV LOGを再計算" });
  await page.locator(".table-number-input").first().fill("5500");

  await expect(calculate).toBeEnabled();
  await calculate.click();

  const firstRow = page.locator(".nav-log-table .nav-leg-detail-row").first();
  await expect(firstRow.locator("td").nth(2)).toHaveText("5500");
  await expect(firstRow.locator("td").nth(6)).toHaveText(/^\d{3}$/);
  await expect(firstRow.locator("td").nth(7)).toHaveText(/^[+-]\d+$/);
  await expect(firstRow.locator("td").nth(8)).toHaveText(/^\d{3}$/);
  await expect(firstRow.locator("td").nth(10)).toHaveText(/^[+-]\d+$/);
  await expect(firstRow.locator("td").nth(11)).toHaveText(/^\d{3}$/);
  await expect(firstRow.locator("td").nth(12)).toHaveText(
    /^\d+\.[05] \/ \d+\.[05]$/,
  );
  await expect(firstRow.locator("td").nth(14)).toHaveText(
    /^\d+\.[05] \/ \d+\.[05]$/,
  );
  await expect(firstRow.locator("td").nth(18)).toHaveText(
    /^\d+\.\d \/ \d+\.\d$/,
  );
  await expect(page.getByText("DESCENT_END", { exact: true })).toHaveCount(0);
  await expect(page.locator(".nav-leg-heading-row")).not.toHaveCount(0);

  const fuelTable = page.locator(".fuel-plan-table");
  await expect(fuelTable.locator("col")).toHaveCount(5);
  await expect(fuelTable.getByText("TAXI・RUN UP", { exact: true })).toBeVisible();
  await expect(fuelTable.getByText("MIN REQUIRED", { exact: true })).toBeVisible();
  await expect(fuelTable.locator("tbody tr")).toHaveCount(10);
  const tableLayout = await page.locator(".nav-log-tables").evaluate((container) => {
    const navTable = container.querySelector<HTMLElement>(".nav-log-table");
    const planTable = container.querySelector<HTMLElement>(".fuel-plan-table");
    if (navTable === null || planTable === null) throw new Error("NAV LOG tables are missing");
    return {
      gap: planTable.offsetLeft - (navTable.offsetLeft + navTable.offsetWidth),
      fuelWidth: planTable.offsetWidth,
      navWidth: navTable.offsetWidth,
      fuelRowHeight: planTable.querySelector<HTMLElement>("tbody tr")?.offsetHeight ?? 0,
      fuelAmountAlignment: getComputedStyle(
        planTable.querySelector<HTMLElement>(".fuel-amount")!,
      ).justifyContent,
    };
  });
  expect(tableLayout.gap).toBeGreaterThanOrEqual(11);
  expect(tableLayout.gap).toBeLessThanOrEqual(13);
  expect(tableLayout.navWidth).toBeLessThanOrEqual(1700);
  expect(tableLayout.fuelWidth).toBeLessThanOrEqual(430);
  expect(tableLayout.fuelWidth).toBeLessThan(tableLayout.navWidth / 2);
  expect(tableLayout.fuelRowHeight).toBeLessThanOrEqual(25);
  expect(tableLayout.fuelAmountAlignment).toBe("center");
  const unexpectedConsoleErrors = consoleErrors.filter(
    (message) => !message.includes("401 (Unauthorized)"),
  );
  expect(unexpectedConsoleErrors).toEqual([]);
});

test("NAV LOG safe inputs validate and recalculate automatically", async ({ page }) => {
  await page.goto("/");
  await calculateNavLog(page);

  const recalculationRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/project/recalculate")) {
      recalculationRequests.push(request.url());
    }
  });

  const altitude = page.locator(".nav-log-table").getByLabel(/計画高度$/).first();
  const altitudeResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/project/recalculate") && response.ok(),
  );
  await altitude.fill("5500");
  await altitudeResponse;
  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  await expect(altitude).toHaveValue("5500");

  const windDirection = page.locator(".nav-log-table").getByLabel(/手動風向$/).first();
  const windSpeed = page.locator(".nav-log-table").getByLabel(/手動風速$/).first();
  const requestCount = recalculationRequests.length;
  await windDirection.fill("270");
  await expect(page.getByText(/入力を確認してください。直前の正常な計算結果/)).toBeVisible();
  await expect(windDirection).toHaveAttribute("aria-invalid", "true");
  await page.waitForTimeout(850);
  expect(recalculationRequests).toHaveLength(requestCount);

  const windResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/project/recalculate") && response.ok(),
  );
  await windSpeed.fill("15");
  await windResponse;
  await expect(page.getByText("自動再計算しました.", { exact: true })).toHaveCount(0);
  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  await expect(windDirection).toHaveAttribute("aria-invalid", "false");
  await expect(page.locator(".nav-log-table th").nth(6)).toHaveText("TC");
  await expect(page.locator(".derived-readonly-cell").first()).toHaveAttribute("title", /読み取り専用/);
});

test("calculated mobile layout has no body overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();

  await calculateNavLog(page);

  await expect(page.getByRole("heading", { name: "準備状況" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "NAV LOG" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: path.join(repositoryRoot, "docs/web-design/implementation-mobile.png"),
    fullPage: true,
  });
});

test("KMZ document selection modal moves and traps focus", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await page.locator("#route-file").setInputFiles({
    name: "multiple.kmz",
    mimeType: "application/vnd.google-earth.kmz",
    buffer: multiDocumentKmz,
  });

  const dialog = page.getByRole("dialog", { name: "KMZ内のKMLを選択" });
  const documentSelect = dialog.getByLabel("KML文書");
  await expect(dialog).toBeVisible();
  await expect(documentSelect).toBeFocused();
  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("Tab");
    await expect(dialog.locator(":focus")).toHaveCount(1);
  }

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});
