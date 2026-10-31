import { expect, test, type Page } from "@playwright/test";
import { enterImportWorkflow } from "./helpers/importWorkflow";
import { observeLegacySession } from "./helpers/legacySession";

const local = () => test.info().config.metadata.applicationMode === "local";
const editor = (page: Page) => page.getByRole("region", { name: "チェックポイント設定" });
const form = (page: Page) => editor(page).locator(".checkpoint-form");
const name = (page: Page) => form(page).getByRole("textbox", { name: /^名称(?:\s|$)/ });
const kml = `<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
<name>RJFM-RJFO</name><LineString><coordinates>
131.4486111111,31.8772222222,0 131.5,32.4,0 131.65,33.1,0
131.7,33.4,0 131.7372222222,33.4794444444,0
</coordinates></LineString></Placemark></Document></kml>`;

async function openEditor(page: Page) {
  await editor(page).getByRole("button", { name: "チェックポイントを追加", exact: true }).click();
  await form(page).getByLabel("緯度", { exact: true }).fill("32.482176");
  await form(page).getByLabel("経度", { exact: true }).fill("131.517485");
}
async function references(page: Page) {
  return page.evaluate(async (isLocal) => isLocal
    ? JSON.parse(sessionStorage.getItem("autonavlog.working-session.v1")!).working.project.visual_references
    : (await (await fetch("/api/state")).json()).project.visual_references, local());
}

test.beforeEach(async ({ page, context }) => {
  if (local()) await context.route("**/api/**", route => route.abort());
  else await observeLegacySession(page);
  // External map layers are unrelated to checkpoint submission.
  await context.route(/https:\/\/.*(?:openstreetmap|gsi\.go\.jp).*/, route => route.fulfill({ status: 200, contentType: "application/json", body: '{"type":"FeatureCollection","features":[]}' }));
  await page.goto("/");
  await expect(page).toHaveTitle(/NavMate/);
  await enterImportWorkflow(page);
  await page.locator('input[type="file"]').setInputFiles({ name: "checkpoint.kml", mimeType: "application/vnd.google-earth.kml+xml", buffer: Buffer.from(kml) });
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await openEditor(page);
});

for (const width of [1100, 1440]) {
  test(`checkpoint name Enter validates input, respects IME and preserves click at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 });
    const add = form(page).getByRole("button", { name: "追加", exact: true });
    const assertBlocked = async () => {
      await expect(add).toBeDisabled();
      await name(page).press("Enter");
      await expect(form(page)).toBeVisible();
      expect(await references(page)).toHaveLength(0);
    };
    await assertBlocked();
    await name(page).fill("   ");
    await assertBlocked();
    await name(page).fill("岩瀬ダム");
    for (const latitude of ["", "91", "-91"]) {
      await form(page).getByLabel("緯度", { exact: true }).fill(latitude);
      await assertBlocked();
    }
    await form(page).getByLabel("緯度", { exact: true }).fill("32.482176");
    for (const longitude of ["", "181", "-181"]) {
      await form(page).getByLabel("経度", { exact: true }).fill(longitude);
      await assertBlocked();
    }
    await form(page).getByLabel("緯度", { exact: true }).fill("35");
    await form(page).getByLabel("経度", { exact: true }).fill("140");
    await assertBlocked();
    await form(page).getByLabel("緯度", { exact: true }).fill("32.389063");
    await form(page).getByLabel("経度", { exact: true }).fill("131.597572");
    await expect(form(page).getByLabel("関連Leg")).toHaveValue("");
    await assertBlocked();
    await form(page).getByLabel("緯度", { exact: true }).fill("32.482176");
    await form(page).getByLabel("経度", { exact: true }).fill("131.517485");
    await expect(add).toBeEnabled();

    // Synthetic events cover browser/IME event orderings; they are not physical IME proof.
    await name(page).dispatchEvent("keydown", { key: "Enter", isComposing: true });
    await expect(form(page)).toBeVisible();
    await name(page).dispatchEvent("compositionstart", { data: "いわせ" });
    await name(page).press("Enter");
    await expect(form(page)).toBeVisible();
    await name(page).dispatchEvent("compositionend", { data: "岩瀬" });
    await name(page).dispatchEvent("keydown", { key: "Enter", keyCode: 229 });
    await expect(form(page)).toBeVisible();
    await name(page).dispatchEvent("keydown", { key: "Enter", repeat: true });
    expect(await references(page)).toHaveLength(0);
    await name(page).press("Enter");
    await expect(form(page)).toHaveCount(0);
    await expect(editor(page).getByText("岩瀬ダム", { exact: true })).toBeVisible();
    await expect(page.locator(".checkpoint-confirmed-marker")).toHaveCount(1);
    await expect(editor(page).getByText(/Leg内 .* NM/)).toBeVisible();
    const entered = (await references(page))[0];
    expect(entered.name).toBe("岩瀬ダム");
    expect(entered.latitude_deg).toBe(32.482176);
    expect(entered.longitude_deg).toBe(131.517485);
    expect(entered.linked_section_id).toBeTruthy();
    await editor(page).screenshot({ path: info.outputPath(`checkpoint-${width}.png`) });

    await openEditor(page);
    await name(page).fill("クリックCP");
    await add.click();
    await expect(form(page)).toHaveCount(0);
    const clicked = (await references(page))[1];
    expect(clicked).toMatchObject({ latitude_deg: entered.latitude_deg, longitude_deg: entered.longitude_deg, linked_section_id: entered.linked_section_id });
    await editor(page).getByRole("button", { name: "岩瀬ダムを編集", exact: true }).click();
    await name(page).fill("岩瀬ダム改");
    await name(page).press("Enter");
    await expect(editor(page).getByText("岩瀬ダム改", { exact: true })).toBeVisible();
    expect(await references(page)).toHaveLength(2);
    await expect(page.locator(".vite-error-overlay, vite-error-overlay")).toHaveCount(0);
    expect(await page.pageErrors()).toEqual([]);
    const consoleErrors = (await page.consoleMessages()).filter(message => message.type() === "error");
    // The unconfigured Legacy migration probe intentionally returns 404.
    expect(consoleErrors.filter(message => !(!local()
      && message.location().url === new URL("/api/account-link", page.url()).href
      && message.text().includes("404")))).toEqual([]);
  });
}

test("checkpoint name Enter locks pending saves and allows retry after failure", async ({ page }) => {
  test.skip(local(), "Legacy response interception verifies the shared editor's pending-save lock");
  let requests = 0;
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/project/check-points", async route => {
    requests++;
    if (requests === 1) {
      await pending;
      await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Test save failure" }) });
    } else await route.continue();
  });
  await name(page).fill("連打CP");
  // All events run before React can re-render its disabled button.
  await name(page).evaluate(input => {
    const button = input.closest(".checkpoint-form")!.querySelector<HTMLButtonElement>(".primary-button")!;
    for (let i = 0; i < 5; i++) {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
      button.click();
    }
  });
  await expect(form(page).getByRole("button", { name: "追加中…", exact: true })).toBeDisabled();
  await name(page).press("Enter");
  expect(requests).toBe(1);
  release();
  await expect(form(page).getByRole("button", { name: "追加", exact: true })).toBeEnabled();
  expect(await references(page)).toHaveLength(0);
  await name(page).press("Enter");
  await expect(form(page)).toHaveCount(0);
  expect(requests).toBe(2);
  expect(await references(page)).toHaveLength(1);
  await expect(editor(page).getByText("連打CP", { exact: true })).toBeVisible();
});


for (const width of [390, 1100, 1440]) {
  test(`checkpoint MAP pick focuses once, reveals name and saves with Enter at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await form(page).getByRole("button", { name: "キャンセル", exact: true }).click();
    await editor(page).getByRole("button", { name: "チェックポイントを追加", exact: true }).click();
    await expect(name(page)).not.toBeFocused();
    const route = page.getByRole("region", { name: "経路地図とLeg設定" });
    const map = route.locator(".route-map");
    for (const suffix of ["first", "second"]) {
      if (suffix === "second") await form(page).getByRole("button", { name: "地図から座標を選択" }).click();
      await map.click({ position: { x: 150, y: 150 } });
      await expect(name(page)).toBeFocused();
      await expect(name(page)).toBeInViewport({ ratio: 1 });
      await expect(form(page).getByLabel("緯度", { exact: true })).not.toHaveValue("");
      await expect(form(page).getByLabel("経度", { exact: true })).not.toHaveValue("");
      await expect(route.locator("#route-map-frame")).not.toHaveClass(/is-picking-checkpoint/);
      await page.keyboard.type(suffix);
      await expect(name(page)).toHaveValue(suffix === "first" ? "first" : "firstsecond");
      await page.keyboard.press("Tab");
      await expect(name(page)).not.toBeFocused();
      const latitude = form(page).getByLabel("緯度", { exact: true });
      await latitude.fill("32.482176");
      await expect(latitude).toBeFocused();
      await expect(name(page)).not.toBeFocused();
    }
    await form(page).getByRole("button", { name: "キャンセル", exact: true }).click();
    await expect(form(page)).toHaveCount(0);
    await expect(route.locator(".checkpoint-draft-marker")).toHaveCount(0);
    await openEditor(page);
    // Find a point on rendered route geometry for a valid real MAP pick,
    // instead of invoking application handlers or manually replacing coordinates.
    const segment = map.locator('path[stroke="#173b5e"]').first();
    await map.scrollIntoViewIfNeeded();
    const point = await segment.evaluate((element) => {
      const path = element as SVGPathElement;
      const point = path.getPointAtLength(path.getTotalLength() * 0.55);
      const screen = point.matrixTransform(path.getScreenCTM()!);
      return { x: screen.x, y: screen.y };
    });
    await page.mouse.click(point.x, point.y);
    await expect(name(page)).toBeFocused();
    await page.keyboard.type("MAP Enter CP");
    const leg = form(page).getByLabel("関連Leg");
    if (await leg.count()) await leg.selectOption({ index: 1 });
    await expect(form(page).getByRole("button", { name: "追加", exact: true })).toBeEnabled();
    // Re-pick the same point so typing and Enter follow the map focus without
    // any input focus operation, even when a Leg had to be explicitly selected.
    await form(page).getByRole("button", { name: "地図から座標を選択" }).click();
    await map.scrollIntoViewIfNeeded();
    const again = await segment.evaluate((element) => {
      const path = element as SVGPathElement;
      const screen = path.getPointAtLength(path.getTotalLength() * 0.55).matrixTransform(path.getScreenCTM()!);
      return { x: screen.x, y: screen.y };
    });
    await page.mouse.click(again.x, again.y);
    await expect(name(page)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(form(page)).toHaveCount(0);
    expect(await references(page)).toHaveLength(1);
    await expect(editor(page).getByText("MAP Enter CP", { exact: true })).toBeVisible();
    await expect(route.locator(".checkpoint-draft-marker")).toHaveCount(0);
    await editor(page).screenshot({ path: info.outputPath(`map-enter-${width}.png`) });
    expect(await page.pageErrors()).toEqual([]);
  });
}
