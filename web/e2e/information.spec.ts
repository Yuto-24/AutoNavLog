import { disableClipboardRead } from "./helpers/clipboard";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { canMigrateLegacyRelease, type InformationData } from "../src/releaseNotes";

const lastSeenUpdateKey = "autonavlog.information.lastSeenUpdate";
const lastSeenReleaseKey = "autonavlog.information.lastSeenRelease";
const informationData = JSON.parse(readFileSync(
  new URL("../src/generated/releaseNotes.json", import.meta.url),
  "utf8",
)) as {
  information: { id: string; releases: Array<{ version: string }> };
  compatibility: { legacyReleaseInformationIds: Record<string, string> };
};
const latestInformationId = informationData.information.id;
const latestRelease = informationData.information.releases[0]!;
const legacy110BaselineId = informationData.compatibility.legacyReleaseInformationIds["1.10.0"];
const routeKml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>RJFM-RJFO</name><LineString><coordinates>
131.4486111111,31.8772222222,0 131.5000000000,32.4000000000,0 131.6500000000,33.1000000000,0 131.7372222222,33.4794444444,0
</coordinates></LineString></Placemark></Document></kml>`;

const informationButton = (page: Page) => page.getByRole("button", { name: /Information/ });
const informationDialog = (page: Page) => page.getByRole("dialog", { name: "Information" });

async function openInformation(page: Page) {
  await informationButton(page).click();
  const dialog = informationDialog(page);
  await expect(dialog).toBeVisible();
  return dialog;
}

async function expectBaseWorkflow(page: Page) {
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await expect(page.getByRole("button", { name: "新規", exact: true })).toBeEnabled();
}

async function createEditCalculateAndSave(page: Page) {
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  if (await page.getByRole("button", { name: "KMLを貼り付け" }).count() === 0) {
    page.once("dialog", (dialog) => void dialog.accept());
    await Promise.all([
      page.waitForNavigation(),
      page.getByRole("button", { name: "新規", exact: true }).click(),
    ]);
  }
  await page.getByRole("button", { name: "KMLを貼り付け" }).click();
  const pasteDialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await pasteDialog.getByRole("textbox").fill(routeKml);
  await pasteDialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  const altitudeInputs = page.locator(".route-table tbody tr:not(.vrep-row) .table-number-input");
  await expect(altitudeInputs.first()).toBeVisible();
  for (let index = 0; index < await altitudeInputs.count(); index += 1) {
    await altitudeInputs.nth(index).fill("4500");
  }
  const altitudeCandidates = page.locator(".altitude-candidate-select");
  for (let index = 0; index < await altitudeCandidates.count(); index += 1) {
    await altitudeCandidates.nth(index).selectOption({ index: 0 });
  }
  const cruiseAltitude = page.getByLabel(/出発Leg.*巡航高度候補/).first();
  const cruiseCandidate = await cruiseAltitude.locator("option").first().getAttribute("value");
  if (cruiseCandidate === null) throw new Error("Cruise altitude candidate is missing");
  await cruiseAltitude.selectOption(cruiseCandidate);
  await page.getByLabel("今回採用する場周経路高度").fill("1300");
  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByLabel("地上風速 kt").fill("15");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("270");
  await page.getByLabel("5,000 ft風速 kt").fill("30");
  const calculateButton = page.locator(".status-actions .primary-button");
  await expect(calculateButton).toHaveText("NAV LOGを作る");
  await calculateButton.click();
  await expect(page.getByLabel("計算済みNAV LOG")).toBeVisible({ timeout: 30_000 });

  await page.getByLabel("プロジェクト").fill("storage fallback");
  const saved = page.waitForResponse((response) => (
    response.url().endsWith("/api/projects/save") && response.ok()
  ));
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await saved;
  await expect(page.getByText("Projectをローカルへ保存しました。", { exact: true })).toBeVisible();
}

test.beforeEach(async ({ page, request }) => {
  await disableClipboardRead(page);
  const reset = await request.delete("/api/session");
  expect(reset.status()).toBe(204);
});

test("Information exposes bundle-generated latest and historical release sections without auto-opening", async ({ page }) => {
  await page.goto("/");
  await expectBaseWorkflow(page);
  await expect(informationDialog(page)).toBeHidden();

  const button = informationButton(page);
  await expect(button).toHaveAccessibleName("Information（未読の更新があります）");
  await expect(button).not.toHaveClass(/information-warning/);
  await expect(button.locator(".information-unread-dot")).toBeVisible();

  const dialog = await openInformation(page);
  await expect(dialog.getByRole("heading", { name: `v${latestRelease.version}` })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "追加", exact: true }).first()).toBeVisible();
  await expect(dialog.getByText("AutoNavLog のお知らせ", { exact: true })).toBeVisible();
  await expect(dialog.locator(".information-known-issues")).toHaveCount(0);
  await expect(dialog).not.toContainText(/Issue #|localStorage|JSON|Python|配布/);
  await expect(dialog.getByRole("heading", { name: "v1.9.5" })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "修正", exact: true }).first()).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "更新履歴", exact: true })).toBeVisible();
});

test("Information traps focus, closes with Escape and backdrop, and keeps latest release seen after reload", async ({ page }) => {
  await page.goto("/");
  const button = informationButton(page);
  const dialog = await openInformation(page);
  await expect(dialog.getByRole("button", { name: "Informationを閉じる" })).toBeFocused();
  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("Tab");
    await expect(dialog.locator(":focus")).toHaveCount(1);
  }

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(button).toBeFocused();

  await openInformation(page);
  await page.locator(".modal-backdrop").click({ position: { x: 4, y: 4 } });
  await expect(dialog).toBeHidden();
  await expect(button).toBeFocused();

  await page.reload();
  await expectBaseWorkflow(page);
  await expect(informationButton(page)).toHaveAccessibleName("Information");
  await expect(informationButton(page).locator(".information-unread-dot")).toBeHidden();
  await expect(informationDialog(page)).toBeHidden();
});

test("Information keeps the exact current update ID seen after reload", async ({ page }) => {
  await page.addInitScript(([key, value]) => window.localStorage.setItem(key, value), [
    lastSeenUpdateKey,
    latestInformationId,
  ]);
  await page.goto("/");
  await expect(informationButton(page)).toHaveAccessibleName("Information");
  await expect(informationButton(page).locator(".information-unread-dot")).toBeHidden();
});

test("Information treats the known same-version legacy baseline as unread after content changes", async ({ page }) => {
  expect(legacy110BaselineId).toMatch(/^information:sha256:[a-f0-9]{64}$/);
  expect(legacy110BaselineId).not.toBe(latestInformationId);
  await page.addInitScript(([key, value]) => window.localStorage.setItem(key, value), [
    lastSeenUpdateKey,
    legacy110BaselineId,
  ]);
  await page.goto("/");
  await expect(informationButton(page)).toHaveAccessibleName("Information（未読の更新があります）");
  await expect(informationButton(page).locator(".information-unread-dot")).toBeVisible();
});

test("legacy migration accepts only an exact controlled Information snapshot", () => {
  const controlledData: InformationData = {
    information: { id: "information:sha256:controlled", releases: [] },
    compatibility: { legacyReleaseInformationIds: { "1.10.0": "information:sha256:controlled" } },
  };
  expect(canMigrateLegacyRelease("1.10.0", controlledData)).toBe(true);
  expect(canMigrateLegacyRelease("1.10.0", {
    ...controlledData,
    information: { ...controlledData.information, id: "information:sha256:changed" },
  })).toBe(false);
  expect(canMigrateLegacyRelease("1.9.5", controlledData)).toBe(false);
});

for (const [name, key, storedValue] of [
  ["an older update ID", lastSeenUpdateKey, "information:sha256:" + "0".repeat(64)],
  ["an unknown update ID", lastSeenUpdateKey, "information:sha256:" + "f".repeat(64)],
  ["a malformed update marker", lastSeenUpdateKey, "{"],
  ["a legacy release marker for the changed same-version content", lastSeenReleaseKey, "1.10.0"],
] as const) {
  test(`Information treats ${name} as unread without interrupting the workflow`, async ({ page }) => {
    await page.addInitScript(([key, value]) => window.localStorage.setItem(key, value), [
      key,
      storedValue,
    ]);
    await page.goto("/");
    await expectBaseWorkflow(page);
    await expect(informationDialog(page)).toBeHidden();
    await expect(informationButton(page)).toHaveAccessibleName("Information（未読の更新があります）");
    await expect((await openInformation(page)).getByRole("heading", { name: `v${latestRelease.version}` })).toBeVisible();
  });
}

test("Baseline storage completes the same create, edit, calculate, and save workflow", async ({ page }) => {
  await page.goto("/");
  await createEditCalculateAndSave(page);
  await expect(informationDialog(page)).toBeHidden();
});

test("Information tolerates unavailable local storage without blocking create, edit, calculate, or save", async ({ page }) => {
  await page.addInitScript((key) => {
    const originalGetItem = Storage.prototype.getItem;
    const originalSetItem = Storage.prototype.setItem;
    const failForInformation = function (this: Storage, requestedKey: string) {
      if (requestedKey === key) throw new DOMException("storage disabled", "SecurityError");
      return originalGetItem.call(this, requestedKey);
    };
    Storage.prototype.getItem = failForInformation;
    Storage.prototype.setItem = function (requestedKey: string, value: string) {
      if (requestedKey === key) throw new DOMException("storage disabled", "SecurityError");
      originalSetItem.call(this, requestedKey, value);
    };
  }, lastSeenUpdateKey);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await expect(page.getByRole("button", { name: "新規", exact: true })).toBeEnabled();
  await expect(informationButton(page)).toHaveAccessibleName("Information（未読の更新があります）");
  await openInformation(page);
  await page.keyboard.press("Escape");
  await expect(informationDialog(page)).toBeHidden();
  await createEditCalculateAndSave(page);

  await page.reload();
  await expect(page.getByLabel("プロジェクト")).toHaveValue("storage fallback");
  await expect(page.getByLabel("計算済みNAV LOG")).toBeVisible();
  await expect(page.getByRole("button", { name: "新規", exact: true })).toBeEnabled();
  await expect(informationButton(page)).toHaveAccessibleName("Information（未読の更新があります）");
});

test("Information remains reachable without displacing header actions or workflow regions", async ({ page }) => {
  await page.goto("/");
  await createEditCalculateAndSave(page);

  for (const width of [320, 390, 820, 1100, 1440, 1600, 1273, 1242, 997, 996, 631, 630, 629] as const) {
    await page.setViewportSize({ width, height: 900 });
    const button = informationButton(page);
    const [header, information, save, newProject, input, route, status] = await Promise.all([
      page.locator(".app-header").boundingBox(),
      button.boundingBox(),
      page.getByRole("button", { name: "保存", exact: true }).boundingBox(),
      page.getByRole("button", { name: "新規", exact: true }).boundingBox(),
      page.locator(".input-rail").boundingBox(),
      page.locator(".route-workspace").boundingBox(),
      page.locator(".status-rail").boundingBox(),
    ]);
    if (!header || !information || !save || !newProject || !input || !route || !status) {
      throw new Error(`Information header or workflow geometry is missing at ${width}px`);
    }
    expect(header.height).toBeLessThanOrEqual(110);
    const project = await page.locator(".header-project").boundingBox();
    const nameInput = await page.locator("#project-name").boundingBox();
    const label = await page.locator(".header-project-label").boundingBox();
    if (!project || !nameInput || !label) throw new Error("Project geometry missing");
    expect(label.height).toBeLessThan(20);
    expect(label.width).toBeGreaterThan(50);
    const revision = await page.locator(".header-revision").boundingBox();
    if (!revision) throw new Error("Revision missing");
    expect(revision.height).toBeLessThan(20);
    await expect(page.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
    if ([390, 1100, 1242, 1440].includes(width)) {
      await page.screenshot({ path: `/tmp/issue140-header-${width}.png` });
    }
    if (width <= 1320) {
      expect(project.y).toBeGreaterThanOrEqual(information.y + information.height - 1);
      expect(project.width).toBeGreaterThan(header.width - 75);
      expect(nameInput.width).toBeGreaterThan(project.width - 145);
    }
    for (const control of [".saved-project-control select", ".saved-project-control button:first-of-type", ".saved-project-control button:last-of-type"]) {
      const bounds = await page.locator(control).boundingBox();
      if (!bounds) throw new Error("Saved control missing");
      expect(bounds.x).toBeGreaterThanOrEqual(header.x);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(header.x + header.width);
      expect(Math.abs(bounds.y - information.y)).toBeLessThanOrEqual(3);
    }
    expect(information.x).toBeGreaterThanOrEqual(header.x);
    expect(information.x + information.width).toBeLessThanOrEqual(header.x + header.width + 1);
    expect(save.x + save.width).toBeLessThanOrEqual(header.x + header.width + 1);
    expect(newProject.x + newProject.width).toBeLessThanOrEqual(header.x + header.width + 1);
    await expect(button).toBeVisible();
    await expect(page.getByRole("button", { name: "新規", exact: true })).toBeEnabled();

    if (width <= 1240) {
      expect(route.y).toBeGreaterThanOrEqual(input.y + input.height - 1);
      expect(status.y).toBeGreaterThanOrEqual(route.y + route.height - 1);
    } else {
      expect(route.x).toBeGreaterThanOrEqual(input.x + input.width - 1);
      expect(status.x).toBeGreaterThanOrEqual(route.x + route.width - 1);
      expect(Math.abs(route.y - input.y)).toBeLessThanOrEqual(1);
      expect(Math.abs(status.y - input.y)).toBeLessThanOrEqual(1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth))
      .toBeLessThanOrEqual(1);
  }
});


test("compact header still saves, loads, deletes and starts a new project", async ({ page }) => {
  await page.goto("/");
  await createEditCalculateAndSave(page);
  await page.setViewportSize({ width: 390, height: 900 });
  const selector = page.locator("#saved-project");
  const id = await selector.inputValue();
  expect(id).not.toBe("");
  const loaded = page.waitForResponse((response) => response.url().endsWith("/api/projects/load") && response.ok());
  await page.getByRole("button", { name: "保存済みProjectを開く", exact: true }).click();
  await loaded;
  await expect(page.getByLabel("プロジェクト")).toHaveValue("storage fallback");
  await page.getByLabel("プロジェクト").fill("狭い画面で保存");
  const saved = page.waitForResponse((response) => response.url().endsWith("/api/projects/save") && response.ok());
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await saved;
  page.once("dialog", (dialog) => void dialog.accept());
  const deleted = page.waitForResponse((response) => response.request().method() === "DELETE" && response.url().includes("/api/projects/") && response.ok());
  await page.getByRole("button", { name: "保存済みProjectを削除", exact: true }).click();
  await deleted;
  await expect(selector.locator(`option[value="${id}"]`)).toHaveCount(0);
  page.once("dialog", (dialog) => void dialog.accept());
  await Promise.all([page.waitForNavigation(), page.getByRole("button", { name: "新規", exact: true }).click()]);
  await expect(page.getByRole("button", { name: "KMLを貼り付け" })).toBeVisible();
});
