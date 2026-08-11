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
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  await expect(page.getByText("VREP", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "NAV LOGを作る" }).click();
  await expect(page.getByLabel("計算済みNAV LOG")).toBeFocused();
}

test("desktop workflow renders and stays fail-closed", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await expect(page.locator("body")).not.toBeEmpty();
  await expect(page.getByText("開発用固定気象（出力不可）", { exact: true })).toBeVisible();
  await expect(page.getByLabel("TO").locator("option:checked")).toContainText(
    "場周 1,000 ft・未検証",
  );
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0);
  await page.screenshot({
    path: path.join(repositoryRoot, "docs/web-design/implementation-desktop.png"),
    fullPage: false,
  });

  await calculateNavLog(page);

  const patternAltitudeBlocker = page.getByText("PATTERN_ALTITUDE_REQUIRED", { exact: true });
  await expect(patternAltitudeBlocker).toHaveCount(1);
  await expect(patternAltitudeBlocker).toBeVisible();
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
  await page.goto("/");
  await calculateNavLog(page);

  await expect(
    page.getByLabel("ALT・Phase・FUEL・VAR・TGLを原資料と照合しました"),
  ).toHaveCount(0);
  const calculate = page.getByRole("button", { name: "NAV LOGを再計算" });
  await page.locator(".table-number-input").first().fill("5500");

  await expect(calculate).toBeEnabled();
  await calculate.click();

  const firstRow = page.locator(".nav-log-table tbody tr").first();
  await expect(firstRow.locator("td").nth(2)).toHaveText("5500");
  await expect(firstRow.locator("td").nth(6)).toHaveText(/^\d{1,3}°$/);
  await expect(firstRow.locator("td").nth(8)).toHaveText(/^\d{1,3}°$/);
  await expect(firstRow.locator("td").nth(12)).toHaveText(
    /^\d+\.[05] \/ \d+\.[05]$/,
  );
  await expect(firstRow.locator("td").nth(14)).toHaveText(
    /^\d+\.[05] \/ \d+\.[05]$/,
  );
  await expect(firstRow.locator("td").nth(18)).toHaveText(
    /^\d+\.\d \/ \d+\.\d$/,
  );
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
