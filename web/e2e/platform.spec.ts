import { enterImportWorkflow } from "./helpers/importWorkflow";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";

const isLocal = () => test.info().config.metadata.applicationMode === "local";
const kml = readFileSync(resolve("../tests/fixtures/issue_43_golden.kml"), "utf8");
const key = "autonavlog.working-session.v1";
const snapshot = (page: Page) => page.evaluate(k => JSON.parse(sessionStorage.getItem(k)!), key);
const paste = (page: Page) => page.getByRole("button", { name: "KMLを貼り付け", exact: true });

test.beforeEach(async ({ context }) => {
  if (isLocal()) await context.route("**/api/**", route => route.abort());
});
async function open(page: Page) {
  await page.goto("/");
  await enterImportWorkflow(page);
  await expect(paste(page)).toBeEnabled();
}
async function drop(page: Page, name: string, content: string) {
  const transfer = await page.evaluateHandle(({ name, content }) => {
    const data = new DataTransfer();
    data.items.add(new File([content], name, { type: "application/vnd.google-earth.kml+xml" }));
    return data;
  }, { name, content });
  await page.locator(".drop-zone").dispatchEvent("drop", { dataTransfer: transfer });
  await transfer.dispose();
}

test("picker opens compressed KMZ; drop replaces content; cancelled picker keeps the import", async ({ page }) => {
  const bytes = execFileSync("python3", ["-c", "import io,zipfile,sys; b=io.BytesIO(); z=zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED); z.writestr('doc.kml',sys.stdin.buffer.read()); z.close(); sys.stdout.buffer.write(b.getvalue())"], { input: kml });
  await open(page);
  const chooser = page.waitForEvent("filechooser");
  await page.getByText("ファイルを選択", { exact: true }).click();
  await (await chooser).setFiles({ name: "圧縮経路.kmz", mimeType: "application/vnd.google-earth.kmz", buffer: bytes });
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await expect(page.locator(".imported-file")).toContainText("圧縮経路.kmz");
  await expect(page.getByLabel("地図とKML記載順を確認しました")).not.toBeChecked();
  await drop(page, "ドロップ.kml", kml);
  await expect(page.locator(".imported-file")).toContainText("ドロップ.kml");
  const previous = (await snapshot(page)).working.import_result;
  await page.locator('input[type="file"]').setInputFiles([]);
  expect((await snapshot(page)).working.import_result).toEqual(previous);
  await drop(page, "broken.kml", "<broken");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator(".imported-file")).toContainText("ドロップ.kml");
  expect((await snapshot(page)).working.import_result).toEqual(previous);
});

for (const mode of ["supported", "denied", "unsupported"] as const) {
  test(`${mode} Clipboard preserves click activation and manual paste recovery`, async ({ page }) => {
    await page.addInitScript(({ mode, kml }) => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: mode === "unsupported" ? undefined : {
        readText: async () => {
          if (mode === "denied" || !navigator.userActivation.isActive) throw new DOMException("Denied", "NotAllowedError");
          return kml;
        },
      } });
    }, { mode, kml });
    await open(page);
    await paste(page).click();
    const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
    if (mode !== "supported") {
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole("alert")).toContainText(mode === "unsupported" ? "この環境では" : "読み取れませんでした");
      await page.getByLabel("KML/XML", { exact: true }).fill(kml);
      await page.getByRole("button", { name: "貼付KMLを読み込む" }).click();
    }
    await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
    await expect(dialog).toBeHidden();
    await expect(page.getByLabel("地図とKML記載順を確認しました")).not.toBeChecked();
  });
}

test("download and Clipboard export the displayed result, survive denial, and retain workflow order", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await open(page);
  // Loaded Local assets can calculate/import/export without a connectivity gate.
  if (isLocal()) await context.setOffline(true);
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByLabel("地上風速 kt").fill("15");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("270");
  await page.getByLabel("5,000 ft風速 kt").fill("30");
  await drop(page, "route.kml", kml);
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(name + "出発Legの計画高度", { exact: true }).fill(altitude);
  }
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  for (const width of [1100, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    const regions = await page.evaluate(() => [".input-rail", ".route-workspace", ".status-rail", ".nav-log-scroll", ".nav-log-disclaimer"].map(selector => {
      const rect = document.querySelector(selector)!.getBoundingClientRect();
      return { x: rect.x, y: rect.y, bottom: rect.bottom };
    }));
    if (width <= 1240) {
      expect(regions[1].y).toBeGreaterThanOrEqual(regions[0].bottom);
      expect(regions[2].y).toBeGreaterThanOrEqual(regions[1].bottom);
    } else {
      expect(regions[0].x).toBeLessThan(regions[1].x);
      expect(regions[1].x).toBeLessThan(regions[2].x);
    }
    expect(regions[3].y).toBeGreaterThanOrEqual(Math.max(...regions.slice(0, 3).map(region => region.bottom)));
    expect(regions[4].y).toBeGreaterThanOrEqual(regions[3].bottom);
  }
  const original = (await snapshot(page)).working;
  // Uncalculated planning edits must not relabel or recalculate the exported result.
  await page.getByLabel("TGL", { exact: true }).fill("2");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "NAV LOG JSONをダウンロード" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("autonavlog-navlog.json");
  const text = readFileSync((await file.path())!, "utf8");
  const result = JSON.parse(text);
  expect(result).toEqual({ format: "autonavlog.navlog", version: 1, outcome: original.outcome, destinationWind: original.destination_wind });
  await page.getByRole("button", { name: "NAV LOG JSONをコピー" }).click();
  await expect(page.getByRole("status").filter({ hasText: "NAV LOG JSONをコピーしました。" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(text);
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => { throw new DOMException("Denied", "NotAllowedError"); } } }));
  await page.getByRole("button", { name: "NAV LOG JSONをコピー" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "コピーできませんでした" })).toBeVisible();
  await page.evaluate(() => { URL.createObjectURL = undefined as any; });
  await page.getByRole("button", { name: "NAV LOG JSONをダウンロード" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "この環境ではファイルを保存できません" })).toBeVisible();
  expect((await snapshot(page)).working.outcome).toEqual(original.outcome);

});

test("source links open without an opener and cannot unlock a pending save", async ({ page, context }) => {
  await open(page);
  await page.getByLabel("気象モード").selectOption("FTD");
  await drop(page, "route.kml", kml);
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  const link = page.getByRole("link", { name: "国土交通省", exact: true });
  const href = (await link.getAttribute("href"))!;
  await context.route(href, route => route.fulfill({ contentType: "text/html", body: "<title>Reference</title>" }));
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(name + "出発Legの計画高度", { exact: true }).fill(altitude);
  }
  const save = page.getByRole("button", { name: "保存", exact: true });
  await page.getByLabel("プロジェクト", { exact: true }).fill("External reference during save");
  await expect(save).toBeEnabled();
  let held = false;
  let release = () => {};
  if (isLocal()) {
    await page.evaluate(() => {
      const original = Worker.prototype.postMessage;
      Worker.prototype.postMessage = function(message: any, ...rest: any[]) {
        if (message.type === "APPLY" && message.argumentList?.[0]?.value === "state") {
          (window as any).resumePlatformSave = () => {
            Worker.prototype.postMessage = original;
            (original as any).call(this, message, ...rest);
          };
          return;
        }
        return (original as any).call(this, message, ...rest);
      };
    });
  } else {
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/projects/save", async route => {
      held = true;
      await pending;
      await route.continue();
    });
  }
  await save.click();
  if (isLocal()) await page.waitForFunction(() => (window as any).resumePlatformSave);
  else await expect.poll(() => held).toBe(true);
  try {
    const popup = page.waitForEvent("popup");
    await link.click();
    const reference = await popup;
    await expect(reference).toHaveURL(href);
    expect(await reference.evaluate(() => window.opener)).toBeNull();
    await expect(save).toBeDisabled({ timeout: 1000 });
    await expect(page.getByRole("button", { name: "新規", exact: true })).toBeDisabled();
  } finally {
    if (isLocal()) await page.evaluate(() => (window as any).resumePlatformSave());
    else release();
  }
  await expect(save).toBeEnabled();
});
