import { expect, test, type Page } from "@playwright/test";
import { disableClipboardRead } from "./helpers/clipboard";

const kml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>RJFM-RJFO</name><LineString><coordinates>
131.4486111111,31.8772222222,0 131.5000000000,32.4000000000,0 131.6500000000,33.1000000000,0 131.7372222222,33.4794444444,0
</coordinates></LineString></Placemark></Document></kml>`;
const pasteButton = (page: Page) => page.getByRole("button", { name: "KMLを貼り付け", exact: true });
const pasteDialog = (page: Page) => page.getByRole("dialog", { name: "KML/XMLを貼り付け" });

async function clipboardText(page: Page, text: string) {
  await page.addInitScript((value) => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { readText: async () => value },
    });
  }, text);
}

function importedBodies(page: Page) {
  const bodies: unknown[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/import") bodies.push(request.postDataJSON());
  });
  return bodies;
}

async function expectCandidates(page: Page) {
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await expect(page.getByLabel("地図とKML記載順を確認しました")).not.toBeChecked();
  await expect(page.getByRole("button", { name: "経路を確定", exact: true })).toBeDisabled();
  await expect(page.locator(".route-workspace .leaflet-container")).toBeVisible();
  await expect(pasteDialog(page)).toBeHidden();
}

test.beforeEach(async ({ request }) => {
  const reset = await request.delete("/api/session");
  expect(reset.status()).toBe(204);
});

test("native clipboard imports with one click and still requires route confirmation", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await expect(pasteButton(page)).toBeEnabled();
  await page.evaluate((text) => navigator.clipboard.writeText(text), kml);
  const bodies = importedBodies(page);
  await pasteButton(page).click();
  await expectCandidates(page);
  expect(bodies).toEqual([{ filename: "pasted.kml", kml_text: kml }]);
});

test("clipboard reading requires the paste click's user activation", async ({ page }) => {
  await page.addInitScript((text) => {
    Object.defineProperty(navigator, "clipboard", {
      value: { readText: async () => {
        if (!navigator.userActivation.isActive) throw new DOMException("Activation required", "NotAllowedError");
        return text;
      } },
    });
  }, kml);
  await page.goto("/");
  await expect(pasteDialog(page)).toBeHidden();
  await pasteButton(page).click();
  await expectCandidates(page);
});

for (const mode of ["denied", "unsupported", "empty", "whitespace", "exception"] as const) {
  test(`${mode} clipboard opens manual input without sending an import`, async ({ page }) => {
    if (mode === "denied") await disableClipboardRead(page);
    else if (mode === "empty" || mode === "whitespace") await clipboardText(page, mode === "empty" ? "" : " \n\t ");
    else await page.addInitScript((failure) => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: failure === "unsupported" ? undefined : { readText: async () => { throw new Error("unavailable"); } },
      });
    }, mode);
    await page.goto("/");
    const bodies = importedBodies(page);
    await pasteButton(page).click();
    const dialog = pasteDialog(page);
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("alert")).toContainText("下の欄にKMLを貼り付けてください。");
    await expect(dialog.getByRole("textbox")).toBeFocused();
    expect(bodies).toEqual([]);
    await dialog.getByRole("textbox").fill(kml);
    await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();
    await expectCandidates(page);
    expect(bodies).toEqual([{ filename: "pasted.kml", kml_text: kml }]);
  });
}

test("read failures preserve a cancelled manual draft and restore button focus", async ({ page }) => {
  await disableClipboardRead(page);
  await page.goto("/");
  await pasteButton(page).click();
  await pasteDialog(page).getByRole("textbox").fill("unfinished KML draft");
  await pasteDialog(page).getByRole("button", { name: "キャンセル" }).click();
  await expect(pasteButton(page)).toBeFocused();
  await pasteButton(page).click();
  await expect(pasteDialog(page).getByRole("textbox")).toHaveValue("unfinished KML draft");
  await page.keyboard.press("Escape");
  await expect(pasteDialog(page)).toBeHidden();
  await expect(pasteButton(page)).toBeEnabled();
});

for (const failure of ["invalid KML", "server error"] as const) {
  test(`${failure} retains clipboard text and permits correction and retry`, async ({ page }) => {
    const text = failure === "invalid KML" ? "this is not KML" : kml;
    await clipboardText(page, text);
    if (failure === "server error") {
      await page.route("**/api/import", (route) => route.fulfill({
        status: 503, contentType: "application/json",
        body: JSON.stringify({ error: { message: "取り込みに失敗しました。", code: "IMPORT_FAILED" } }),
      }), { times: 1 });
    }
    await page.goto("/");
    await pasteButton(page).click();
    const dialog = pasteDialog(page);
    await expect(dialog.getByRole("alert")).toBeVisible();
    if (failure === "server error") {
      await expect(dialog.getByRole("alert")).toHaveText("取り込みに失敗しました。");
    } else {
      await expect(dialog.getByRole("alert")).toContainText("KML");
    }
    await expect(dialog.getByRole("textbox")).toHaveValue(text);
    await expect(dialog.getByRole("button", { name: "キャンセル" })).toBeEnabled();
    await dialog.getByRole("textbox").fill(kml);
    await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();
    await expectCandidates(page);
  });
}

test("pending clipboard read and import prevent duplicate clicks and unlock on success", async ({ page }) => {
  await page.addInitScript((text) => {
    Object.defineProperty(navigator, "clipboard", {
      value: { readText: () => new Promise<string>((resolve) => {
        Object.assign(window, { finishClipboardRead: () => resolve(text) });
      }) },
    });
  }, kml);
  let finishImport: (() => void) | undefined;
  const importGate = new Promise<void>((resolve) => { finishImport = resolve; });
  await page.route("**/api/import", async (route) => { await importGate; await route.continue(); });
  await page.goto("/");
  const bodies = importedBodies(page);
  // Dispatch both clicks in one task, before React can render the disabled state.
  await pasteButton(page).evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect(pasteButton(page)).toBeDisabled();
  expect(bodies).toEqual([]);
  await page.evaluate(() => (window as unknown as { finishClipboardRead: () => void }).finishClipboardRead());
  await expect.poll(() => bodies.length).toBe(1);
  await expect(pasteButton(page)).toBeDisabled();
  finishImport!();
  await expectCandidates(page);
  await expect(pasteButton(page)).toBeEnabled();
  expect(bodies).toHaveLength(1);
});

for (const width of [390, 1100, 1440]) {
  test(`clipboard fallback and imported workflow at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await disableClipboardRead(page);
    await page.goto("/");
    await pasteButton(page).click();
    const dialog = pasteDialog(page);
    await expect(dialog.getByRole("textbox")).toBeFocused();
    const box = await dialog.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: testInfo.outputPath(`clipboard-fallback-${width}.png`) });
    await dialog.getByRole("textbox").fill(kml);
    await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();
    await expectCandidates(page);
    const regions = await Promise.all([".input-rail", ".route-workspace", ".status-rail"].map((selector) => page.locator(selector).boundingBox()));
    expect(regions.every(Boolean)).toBeTruthy();
    for (let index = 1; index < regions.length; index += 1) {
      const previous = regions[index - 1]!;
      const current = regions[index]!;
      if (width <= 1240) expect(current.y).toBeGreaterThanOrEqual(previous.y + previous.height - 1);
      else {
        expect(current.x).toBeGreaterThanOrEqual(previous.x + previous.width - 1);
        expect(Math.abs(current.y - previous.y)).toBeLessThanOrEqual(1);
      }
    }
    await page.screenshot({ path: testInfo.outputPath(`clipboard-imported-${width}.png`), fullPage: true });
  });
}
