import { expect, test } from "@playwright/test";

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

test("desktop workflow renders and stays fail-closed", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await expect(page.locator("body")).not.toBeEmpty();
  await expect(page.getByLabel("TO").locator("option:checked")).toContainText(
    "場周 1,000 ft・未検証",
  );
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0);
  await page.screenshot({
    path: "../docs/web-design/implementation-desktop.png",
    fullPage: false,
  });

  await page.getByRole("button", { name: "KMLを貼り付け" }).click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(kml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();

  await expect(page.getByLabel("飛行経路にする形状")).toHaveValue("line:0");
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByLabel("PILOT").fill("Browser Test");
  await page.getByLabel("SHIP").fill("JA01AN");
  await page.getByLabel("ALT・Phase・FUEL・VAR・TGLを原資料と照合しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  await expect(page.getByText("VREP", { exact: true }).first()).toBeVisible();
  const patternAltitudeBlocker = page.getByText("PATTERN_ALTITUDE_REQUIRED", { exact: true });
  await expect(patternAltitudeBlocker).toHaveCount(1);
  await expect(patternAltitudeBlocker).toBeVisible();
  await page.getByRole("button", { name: "NAV LOGを作る" }).click();

  await expect(page.getByRole("heading", { name: "NAV LOG" })).toBeVisible();
  await expect(
    page.getByText("DEVELOPMENT_WEATHER_PROVIDER", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "A4転記補助HTMLを出力" })).toBeDisabled();
  await page.screenshot({
    path: "../docs/web-design/implementation-calculated.png",
    fullPage: true,
  });

  expect(pageErrors).toEqual([]);
});

test("mobile layout contains all primary actions without body overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await expect(page.getByRole("button", { name: "KMLを貼り付け" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "準備状況" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: "../docs/web-design/implementation-mobile.png",
    fullPage: true,
  });
});
