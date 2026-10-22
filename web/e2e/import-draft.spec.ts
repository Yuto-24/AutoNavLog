import { expect, test, type Page } from "@playwright/test";

const route = (name: string, coordinates = "131.4486,31.8772,0 131.2,32,0 130.7194,31.8034,0") =>
  `<Placemark><name>${name}</name><LineString><coordinates>${coordinates}</coordinates></LineString></Placemark>`;
const kml = (...routes: string[]) => `<kml xmlns="http://www.opengis.net/kml/2.2"><Document>${routes.join("")}</Document></kml>`;
const strip = (page: Page) => page.getByRole("list", { name: "経路リスト" });
const snapshot = (page: Page) => page.evaluate(() => JSON.parse(sessionStorage.getItem("autonavlog.working-session.v1")!));
async function upload(page: Page, text: string) {
  await page.locator('input[type="file"]').setInputFiles({ name: "route.kml", mimeType: "application/vnd.google-earth.kml+xml", buffer: Buffer.from(text) });
}
async function start(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路作成", exact: true })).toBeVisible({ timeout: 90_000 });
}

for (const width of [1100, 1440]) test(`#176 import Draft single route, reload and Planning order at ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 });
  await start(page);
  await upload(page, kml(route("one")));
  await expect(strip(page).locator("li")).toHaveCount(3);
  await expect(page.getByRole("dialog", { name: "経路選択", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("地図とKML記載順を確認しました")).toHaveCount(0);
  expect((await snapshot(page)).working.project).toBeNull();
  const before = (await snapshot(page)).mapRouteDraft;
  await page.reload();
  await expect(strip(page).locator("li")).toHaveCount(3);
  expect((await snapshot(page)).mapRouteDraft).toEqual(before);
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "KMLを取り込む", exact: true })).toHaveCount(0);
  const input = (await page.getByRole("complementary", { name: "経路と飛行計画" }).boundingBox())!;
  const map = (await page.getByRole("region", { name: "経路地図とLeg設定" }).boundingBox())!;
  const readiness = (await page.locator(".status-rail").boundingBox())!;
  if (width <= 1240) {
    expect(input.y + input.height).toBeLessThanOrEqual(map.y + 2);
    expect(map.y + map.height).toBeLessThanOrEqual(readiness.y + 2);
  } else {
    expect(input.x + input.width).toBeLessThanOrEqual(map.x + 2);
    expect(map.x + map.width).toBeLessThanOrEqual(readiness.x + 2);
  }
});

test("#176 invalid, cancelled and repeated import preserve the old Draft until a valid choice", async ({ page }) => {
  await start(page);
  await upload(page, kml(route("old")));
  await expect(strip(page).locator("li")).toHaveCount(3);
  const before = (await snapshot(page)).mapRouteDraft;
  page.on("dialog", dialog => void dialog.accept());
  await upload(page, "invalid KML");
  await expect(page.getByRole("alert").first()).toBeVisible();
  expect((await snapshot(page)).mapRouteDraft).toEqual(before);
  // The parser reduces a large collinear route to a small display preview. It
  // must not silently become the final MAP route or replace the existing Draft.
  const oversized = Array.from({ length: 2100 }, (_, i) =>
    `${131.4486 - i * 0.0001},${31.8772 + i * 0.00001},0`).join(" ");
  await upload(page, kml(route("oversized", oversized)));
  await expect(page.getByRole("alert").first()).toContainText("座標数の上限（500点）");
  expect((await snapshot(page)).mapRouteDraft).toEqual(before);
  // A line and Polygon remain separate parser candidates.
  const multiple = kml(route("one"), `<Placemark><name>two</name><Polygon><outerBoundaryIs><LinearRing><coordinates>130.7194,31.8034,0 131.1,31.8,0 131.4486,31.8772,0 130.7194,31.8034,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>`);
  await upload(page, multiple);
  const dialog = page.getByRole("dialog", { name: "経路選択", exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("combobox").selectOption("polygon:0");
  await page.reload();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("combobox")).toHaveValue("polygon:0");
  await dialog.getByRole("button", { name: "キャンセル", exact: true }).click();
  expect((await snapshot(page)).mapRouteDraft).toEqual(before);
  await upload(page, multiple);
  await expect(dialog).toBeVisible();
  await dialog.getByRole("combobox").selectOption("polygon:0");
  await dialog.getByRole("button", { name: "KMLを取り込む", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(strip(page).locator("li").first()).toContainText("RJFK");
  expect((await snapshot(page)).working.project).toBeNull();
});

test("#176 unmatched imported endpoints survive reload without enabling confirmation", async ({ page }) => {
  await start(page);
  await upload(page, kml(route("unmatched", "140,40,0 131.2,32,0 130.7194,31.8034,0")));
  await expect(page.getByRole("alert")).toContainText("KML始点から5 NM以内");
  await expect(page.getByRole("button", { name: "経路を確定", exact: true })).toBeDisabled();
  await page.reload();
  await expect(strip(page).locator("li")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "経路を確定", exact: true })).toBeDisabled();
  expect((await snapshot(page)).working.project).toBeNull();
  expect((await snapshot(page)).mapRouteDraft).toEqual([]);
});

test("#176 denied Clipboard falls back to manual paste without losing the Draft", async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", { configurable: true,
    value: { readText: () => Promise.reject(new DOMException("Denied", "NotAllowedError")) } }));
  await start(page);
  await upload(page, kml(route("old")));
  await expect(strip(page).locator("li")).toHaveCount(3);
  const before = (await snapshot(page)).mapRouteDraft;
  page.on("dialog", dialog => void dialog.accept());
  await page.getByRole("button", { name: "KMLを取り込む", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect((await snapshot(page)).mapRouteDraft).toEqual(before);
  await page.getByRole("dialog").getByRole("button", { name: "閉じる", exact: true }).click();
  expect((await snapshot(page)).mapRouteDraft).toEqual(before);
});

test("#176 parser warnings remain visible on the Map and after reload", async ({ page }) => {
  await start(page);
  await upload(page, kml(route("A", "131.4486,31.8772,0 131.1,31.9,0"),
    route("B", "131.1,31.90015,0 130.7194,31.8034,0")));
  await expect(page.locator(".map-input-rail .inline-warning")).toContainText("対応するPointがないまま");
  await expect(strip(page).locator("li")).toHaveCount(3);
  await page.reload();
  await expect(page.locator(".map-input-rail .inline-warning")).toContainText("対応するPointがないまま");
});

test("#176 distant import fits once and ordinary Draft editing retains the view", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("autonavlog.map-viewport.v1.anonymous.last",
    JSON.stringify({ latitude: 35, longitude: 139, zoom: 12 })));
  await start(page);
  const viewport = () => page.evaluate(() => JSON.parse(localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last")!));
  await upload(page, kml(route("one")));
  await expect(strip(page).locator("li")).toHaveCount(3);
  await expect.poll(async () => (await viewport()).longitude).toBeLessThan(133);
  const fitted = await viewport();
  await strip(page).getByRole("button", { name: "2番目のWP1を削除", exact: true }).click();
  await expect(strip(page).locator("li")).toHaveCount(2);
  expect(await viewport()).toEqual(fitted);
});

test("#176 empty Clipboard cannot offer a previously imported KML for replacement", async ({ page }) => {
  await page.addInitScript(text => {
    let reads = 0;
    Object.defineProperty(navigator, "clipboard", { configurable: true,
      value: { readText: () => Promise.resolve(reads++ === 0 ? text : "") } });
  }, kml(route("clipboard")));
  await start(page);
  await page.getByRole("button", { name: "KMLを取り込む", exact: true }).click();
  await expect(strip(page).locator("li")).toHaveCount(3);
  const before = (await snapshot(page)).mapRouteDraft;
  page.on("dialog", dialog => void dialog.accept());
  await page.getByRole("button", { name: "KMLを取り込む", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け", exact: true });
  await expect(dialog.getByRole("textbox", { name: "KML/XML", exact: true })).toHaveValue("");
  await expect(dialog.getByRole("button", { name: "貼付KMLを読み込む", exact: true })).toBeDisabled();
  expect((await snapshot(page)).mapRouteDraft).toEqual(before);
});
