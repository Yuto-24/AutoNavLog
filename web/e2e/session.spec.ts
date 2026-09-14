import { expect, test, type Page } from "@playwright/test";
import { resolve } from "node:path";

const key = "autonavlog.working-session.v1";
const isLocal = () => test.info().config.metadata.applicationMode === "local";
const snapshot = (page: Page) => page.evaluate(k => JSON.parse(sessionStorage.getItem(k)!), key);

test.beforeEach(async ({ context }) => {
  if (isLocal()) await context.route("**/api/**", route => route.abort());
});
async function open(page: Page) {
  await page.goto("/");
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
}
async function imported(page: Page) {
  await open(page);
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByLabel("地上風速 kt").fill("15");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("270");
  await page.getByLabel("5,000 ft風速 kt").fill("30");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
}
async function confirmed(page: Page) {
  await imported(page);
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByLabel("RJFM出発Legの計画高度", { exact: true })).toBeVisible();
}
async function calculated(page: Page) {
  await confirmed(page);
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(name + "出発Legの計画高度", { exact: true }).fill(altitude);
  }
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
}
test("reload preserves raw invalid planning values, import and route selection", async ({ page }) => {
  await imported(page);
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByLabel("TGL", { exact: true }).fill("99");
  await page.getByLabel("地上風向 ° FROM").fill("1.5");
  await page.reload();
  await expect(page.getByLabel("TGL", { exact: true })).toHaveValue("99");
  await expect(page.getByLabel("地上風向 ° FROM")).toHaveValue("1.5");
  await expect(page.getByLabel("地図とKML記載順を確認しました")).toBeChecked();
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await page.getByLabel("TGL", { exact: true }).fill("1");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByLabel("RJFM出発Legの計画高度", { exact: true })).toBeVisible();
  const before = await snapshot(page);
  await page.getByLabel("RJFM出発Legの計画高度", { exact: true }).fill("123");
  await page.reload();
  await expect(page.getByLabel("RJFM出発Legの計画高度", { exact: true })).toHaveValue("123");
  expect((await snapshot(page)).working.project.id).toBe(before.working.project.id);
});

test("reload retains last-good and NAV LOG draft without auto calculation", async ({ page }) => {
  await calculated(page);
  const before = await snapshot(page);
  const tas = page.locator(".nav-log-table").getByLabel(/手動TAS$/).first();
  await tas.fill("999");
  await page.reload();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await expect(page.locator(".nav-log-table").getByLabel(/手動TAS$/).first()).toHaveValue("999");
  expect((await snapshot(page)).working.outcome).toEqual(before.working.outcome);
  await page.waitForTimeout(1000); // exceeds both existing auto-recalculation debounces
  expect((await snapshot(page)).working.outcome).toEqual(before.working.outcome);
  await page.locator(".nav-log-table").getByLabel(/手動TAS$/).first().fill("120");
  await expect.poll(async () => (await snapshot(page)).working.outcome).not.toEqual(before.working.outcome);
});

test("new discards recovery; new tabs including opener clones never inherit work", async ({ page, context }) => {
  await confirmed(page);
  await page.getByLabel("TGL", { exact: true }).fill("3");
  const second = await context.newPage();
  await open(second);
  expect((await snapshot(second)).working.project).toBeNull();
  await confirmed(second);
  await second.getByLabel("TGL", { exact: true }).fill("7");
  const popupPromise = page.waitForEvent("popup");
  await page.evaluate(() => window.open(location.href, "_blank"));
  const popup = await popupPromise;
  await expect(popup.getByLabel("TGL", { exact: true })).toHaveValue("0");
  expect((await snapshot(popup)).working.project).toBeNull();
  await popup.close();
  await second.reload();
  await expect(second.getByLabel("TGL", { exact: true })).toHaveValue("7");
  await page.reload();
  await expect(page.getByLabel("TGL", { exact: true })).toHaveValue("3");
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "新規", exact: true }).click();
  await expect(page.getByLabel("TGL", { exact: true })).toHaveValue("0");
  await page.reload();
  expect((await snapshot(page)).working.project).toBeNull();
  await expect(second.getByLabel("TGL", { exact: true })).toHaveValue("7");
});

for (const corruption of ["json", "version", "working"]) {
  test("corrupt recovery starts safely: " + corruption, async ({ page }) => {
    await confirmed(page);
    await page.evaluate(({ key, corruption }) => {
      const saved = JSON.parse(sessionStorage.getItem(key)!);
      if (corruption === "version") saved.version = 99;
      if (corruption === "working") saved.working.project = { broken: true };
      sessionStorage.setItem(key, corruption === "json" ? "{" : JSON.stringify(saved));
    }, { key, corruption });
    await page.reload();
    await expect(page.getByText("前回の作業を復元できなかったため、新規作業を開始しました。")).toBeVisible();
    await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
    expect((await snapshot(page)).working.project).toBeNull();
  });
}

test("calculation interrupted by reload is not resent and late result cannot replace restored work", async ({ page }) => {
  await calculated(page);
  const previous = await snapshot(page);
  let calculations = 0;
  if (isLocal()) {
    await page.evaluate(() => {
      const Original = Worker.prototype.postMessage;
      Worker.prototype.postMessage = function(message: any, ...rest: any[]) {
        if (message.type === "APPLY" && message.argumentList?.[0]?.value === "calculate") {
          (window as any).heldCalculation = true;
          return;
        }
        return (Original as any).call(this, message, ...rest);
      };
    });
  } else {
    await page.route("**/api/calculation-jobs", async route => {
      calculations++;
      await route.fulfill({ json: { job_id: "held", status: "calculating", progress_percent: 1, progress_message: "held" } });
    });
    await page.route("**/api/calculation-jobs/held", async route => {
      await route.fulfill({ json: { job_id: "held", status: "calculating", progress_percent: 1, progress_message: "held" } });
    });
  }
  await page.getByLabel("TGL", { exact: true }).fill("2");
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect(page.locator(".calculation-progress-backdrop")).toBeVisible();
  if (!isLocal()) await expect.poll(() => calculations).toBe(1);
  else await page.waitForFunction(() => (window as any).heldCalculation);
  const interrupted = await snapshot(page);
  expect(interrupted.working.outcome.summary).toEqual(previous.working.outcome.summary);
  await page.reload();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await expect(page.getByLabel("TGL", { exact: true })).toHaveValue("2");
  await page.waitForTimeout(1200);
  expect((await snapshot(page)).working.outcome).toEqual(interrupted.working.outcome);
  await expect(page.locator(".calculation-progress-backdrop")).toHaveCount(0);
  if (!isLocal()) expect(calculations).toBe(1);
});

test("KMZ document choice resumes after reload with modal closed", async ({ page }) => {
  test.skip(isLocal(), "KMZ remains unsupported in Local (#120)");
  const { execFileSync } = await import("node:child_process");
  const bytes = execFileSync("../.venv/bin/python", ["-c",
    "import io,zipfile,sys; b=io.BytesIO(); z=zipfile.ZipFile(b,'w'); s=open('../tests/fixtures/issue_43_golden.kml').read(); z.writestr('a.kml',s); z.writestr('b.kml',s); z.close(); sys.stdout.buffer.write(b.getvalue())"]);
  await open(page);
  await page.locator('input[type="file"]').setInputFiles({ name: "routes.kmz", mimeType: "application/vnd.google-earth.kmz", buffer: bytes });
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByLabel("KML文書").selectOption("b.kml");
  await page.reload();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "KMZ文書の選択を続ける" }).click();
  await expect(page.getByLabel("KML文書")).toHaveValue("b.kml");
  await page.getByRole("button", { name: "選択KMLを読み込む" }).click();
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await page.getByLabel("FUEL gal", { exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("TGL", { exact: true })).toBeFocused();
});

test("restored Check Point and VOR input remains editable; workflow positions are preserved", async ({ page }) => {
  await calculated(page);
  await page.getByLabel("VOR基準局", { exact: true }).selectOption({ index: 2 });
  const vor = await page.getByLabel("VOR基準局", { exact: true }).inputValue();
  await page.getByRole("button", { name: "チェックポイントを追加", exact: true }).click();
  await page.locator(".checkpoint-form").getByLabel("名称", { exact: true }).fill("未確定地点");
  await page.locator(".checkpoint-form").getByLabel(/緯度/).fill("999");
  await page.reload();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await expect(page.getByLabel("VOR基準局", { exact: true })).toHaveValue(vor);
  await expect(page.locator(".checkpoint-form")).toHaveCount(0);
  await page.getByRole("button", { name: "チェックポイントの編集を続ける" }).click();
  await expect(page.locator(".checkpoint-form").getByLabel("名称", { exact: true })).toHaveValue("未確定地点");
  await expect(page.locator(".checkpoint-form").getByLabel(/緯度/)).toHaveValue("999");
  for (const width of [1100, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    const boxes = await Promise.all([".input-rail", ".route-workspace", ".status-rail", ".nav-log-scroll"]
      .map(selector => page.locator(selector).boundingBox()));
    expect(boxes.every(Boolean)).toBe(true);
    if (width <= 1240) {
      for (let i = 1; i < boxes.length; i++) expect(boxes[i]!.y).toBeGreaterThanOrEqual(boxes[i-1]!.y + boxes[i-1]!.height - 1);
    } else {
      expect(boxes[0]!.x + boxes[0]!.width).toBeLessThanOrEqual(boxes[1]!.x + 1);
      expect(boxes[1]!.x + boxes[1]!.width).toBeLessThanOrEqual(boxes[2]!.x + 1);
      expect(boxes[3]!.y).toBeGreaterThanOrEqual(Math.max(...boxes.slice(0, 3).map(b => b!.y + b!.height)) - 1);
    }
  }
});

test("late autosave after restore cannot canonicalize newer invalid input", async ({ page }) => {
  await confirmed(page);
  await page.reload();
  await expect(page.getByLabel("TGL", { exact: true })).toBeVisible();
  let release!: () => void;
  let held = false;
  if (isLocal()) {
    await page.evaluate(() => {
      const original = Worker.prototype.postMessage;
      Worker.prototype.postMessage = function(message: any, ...rest: any[]) {
        if (message.type === "APPLY" && message.argumentList?.[0]?.value === "updateProject") {
          Worker.prototype.postMessage = original;
          (window as any).releaseAutosave = () => (original as any).call(this, message, ...rest);
          return;
        }
        return (original as any).call(this, message, ...rest);
      };
    });
  } else {
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/project", async route => {
      if (held) return route.continue();
      held = true;
      const response = await route.fetch();
      await gate;
      await route.fulfill({ response });
    });
  }
  await page.getByLabel("TGL", { exact: true }).fill("2");
  if (isLocal()) await page.waitForFunction(() => Boolean((window as any).releaseAutosave));
  else await expect.poll(() => held).toBe(true);
  await page.getByLabel("TGL", { exact: true }).fill("99");
  if (isLocal()) await page.evaluate(() => (window as any).releaseAutosave());
  else release();
  await page.waitForTimeout(700);
  await expect(page.getByLabel("TGL", { exact: true })).toHaveValue("99");
  await page.reload();
  await expect(page.getByLabel("TGL", { exact: true })).toHaveValue("99");
  await page.getByLabel("TGL", { exact: true }).fill("3");
  await expect.poll(async () => (await snapshot(page)).working.project.tgl_count).toBe(3);
});

test("new reports storage refusal before disposing the working runtime", async ({ page }) => {
  await confirmed(page);
  const before = await snapshot(page);
  await page.evaluate(() => {
    const original = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function(key) {
      if (key === "autonavlog.working-session.v1") throw new DOMException("denied", "SecurityError");
      return original.call(this, key);
    };
  });
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "新規", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("作業状態を破棄できませんでした");
  expect((await snapshot(page)).working.project.id).toBe(before.working.project.id);
  await page.getByLabel("TGL", { exact: true }).fill("4");
  await expect.poll(async () => (await snapshot(page)).working.project.tgl_count).toBe(4);
});

test("pasted KML draft resumes without reading a changed clipboard", async ({ page }) => {
  await open(page);
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", {
    configurable: true, value: { readText: async () => { throw new Error("denied"); } },
  }));
  await page.getByRole("button", { name: "KMLを貼り付け", exact: true }).click();
  await page.getByRole("dialog").getByRole("textbox").fill("<kml>入力途中");
  await page.reload();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", {
    configurable: true, value: { readText: async () => "別のクリップボード内容" },
  }));
  await page.getByRole("button", { name: "貼付KMLの編集を続ける" }).click();
  await expect(page.getByRole("dialog").getByRole("textbox")).toHaveValue("<kml>入力途中");
});
