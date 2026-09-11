import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { localGolden } from "./helpers/localGolden";

const reference = JSON.parse(readFileSync(resolve("../tests/fixtures/issue_117_ftd_golden.json"), "utf8"));

function compare(actual: any, expected: any, path = "golden"): void {
  if (typeof expected === "number") {
    expect(typeof actual, path).toBe("number");
    expect(Math.abs(actual - expected), path).toBeLessThanOrEqual(1e-8);
  } else if (Array.isArray(expected)) {
    expect(actual.length, path).toBe(expected.length);
    expected.forEach((value, index) => compare(actual[index], value, `${path}[${index}]`));
  } else if (expected !== null && typeof expected === "object") {
    expect(Object.keys(actual).sort(), path).toEqual(Object.keys(expected).sort());
    for (const key of Object.keys(expected)) compare(actual[key], expected[key], `${path}.${key}`);
  } else {
    expect(actual, path).toEqual(expected);
  }
}

test("current Python Reference matches the checked-in FTD Golden", () => {
  const raw = execFileSync(
    process.env.AUTONAVLOG_REFERENCE_PYTHON ?? resolve("../.venv/bin/python"),
    [resolve("../scripts/local_reference.py")],
    { encoding: "utf8" },
  );
  compare(localGolden(JSON.parse(raw)), reference);
});

test.beforeEach(async ({ page }) => {
  // Observe actual Comlink replies without adding a production debug API or changing results.
  await page.addInitScript(() => {
    const root = window as any;
    root.localStates = [];
    const Original = window.Worker;
    root.Worker = class extends Original {
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options);
        this.addEventListener("error", () => { root.localWorkerFailed = true; });
        this.addEventListener("message", (event) => {
          if (typeof event.data.value === "string") {
            try { root.localStates.push(JSON.parse(event.data.value)); } catch { /* not a state */ }
          }
        });
      }
      postMessage(message: any, options?: any) {
        if (root.failLocalCalculation && message.type === "APPLY" &&
            message.argumentList?.[0]?.value === "/api/calculate") {
          // Inject a Python validation exception through the real Comlink/Python path.
          message.argumentList[0].value = "/api/project/recalculate";
          message.argumentList[1].value = { weather_mode: "FORECAST" };
        }
        super.postMessage(message, options);
      }
    };
  });
});

for (const width of [1100, 1440]) {
  test(`static FTD Golden, no API fallback, ${width}px`, async ({ page }) => {
    const apiRequests: string[] = [];
    const errors: string[] = [];
    page.context().on("request", (request) => {
      if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url());
    });
    page.on("pageerror", error => errors.push(error.message));
    // Abort any accidental API request, including requests made by a worker.
    await page.context().route("**/api/**", route => route.abort());
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/");
    await expect(page).toHaveTitle(/AutoNavLog/);
    await expect(page.getByText("Pyodide Local PoC", { exact: false })).toBeVisible();
    await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
    await page.getByLabel("気象モード").selectOption("FTD");
    await page.getByLabel("地上風向 ° FROM").fill("360");
    await page.getByLabel("地上風速 kt").fill("15");
    await page.getByLabel("5,000 ft風向 ° FROM").fill("270");
    await page.getByLabel("5,000 ft風速 kt").fill("30");
    await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
    await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
    await page.getByLabel("地図とKML記載順を確認しました").check();
    await page.getByRole("button", { name: "経路を確定", exact: true }).click();
    for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
      const input = page.getByLabel(`${name}出発Legの計画高度`, { exact: true });
      await input.fill(altitude!);
      await input.blur();
    }
    await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
    await expect(page.locator(".nav-log-table")).toBeVisible();
    const state = await page.evaluate(() => (window as any).localStates.at(-1));
    compare(localGolden(state), reference);
    expect(state.readiness.calculationIsCurrent).toBe(true);
    const boxes = await Promise.all(
      [".input-rail", ".route-workspace", ".status-rail", ".nav-log-scroll"]
        .map(selector => page.locator(selector).boundingBox()),
    );
    expect(boxes.every(Boolean)).toBe(true);
    if (width <= 1240) {
      for (let index = 1; index < boxes.length; index++) {
        expect(boxes[index]!.y).toBeGreaterThanOrEqual(boxes[index - 1]!.y + boxes[index - 1]!.height - 1);
      }
    } else {
      expect(boxes[0]!.x + boxes[0]!.width).toBeLessThanOrEqual(boxes[1]!.x + 1);
      expect(boxes[1]!.x + boxes[1]!.width).toBeLessThanOrEqual(boxes[2]!.x + 1);
      expect(boxes[3]!.y).toBeGreaterThanOrEqual(Math.max(...boxes.slice(0, 3).map(box => box!.y + box!.height)) - 1);
    }
    expect(apiRequests).toEqual([]);
    expect(errors).toEqual([]);
    await expect(page.locator("vite-error-overlay")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
    await page.screenshot({ path: `/tmp/issue117-local-${width}.png`, fullPage: true });
    const previousTable = await page.locator(".nav-log-table").innerText();
    await page.evaluate(() => { (window as any).failLocalCalculation = true; });
    await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
    await expect(page.getByRole("alert")).toContainText("FTD気象を選択");
    expect(await page.locator(".nav-log-table").innerText()).toBe(previousTable);
    await page.evaluate(() => { (window as any).failLocalCalculation = false; });

    // A real worker crash must reject calculation and preserve the displayed last-good result.
    const worker = page.workers()[0]!;
    await worker.evaluate(() => { setTimeout(() => { throw new Error("Test Local Worker failure"); }, 0); });
    await page.waitForFunction(() => (window as any).localWorkerFailed);
    await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
    await expect(page.getByRole("alert")).toContainText("自動保存できませんでした");
    expect(await page.locator(".nav-log-table").innerText()).toBe(previousTable);
    expect(apiRequests).toEqual([]);
    await page.screenshot({ path: `/tmp/issue117-local-error-${width}.png`, fullPage: true });
  });
}

test("local unsupported weather is a visible failure", async ({ page }) => {
  await page.context().route("**/api/**", route => route.abort());
  await page.goto("/");
  await page.getByLabel("気象モード").selectOption("FORECAST");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("FTD気象を選択");
  await expect(page.locator(".nav-log-table")).toHaveCount(0);
});

test("local asset failure is visible without API fallback", async ({ page }) => {
  const apiRequests: string[] = [];
  page.context().on("request", request => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url());
  });
  await page.context().route("**/local/autonavlog.whl", route => route.fulfill({ status: 503, body: "" }));
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Local asset");
  expect(apiRequests).toEqual([]);
});

test("strong FTD wind preserves Python Warning and Blocker results", async ({ page }) => {
  const raw = execFileSync(
    process.env.AUTONAVLOG_REFERENCE_PYTHON ?? resolve("../.venv/bin/python"),
    [resolve("../scripts/local_reference.py"), "--strong-wind"],
    { encoding: "utf8" },
  );
  const expected = localGolden(JSON.parse(raw));
  expect(expected.readiness.some((issue: any) => issue.severity === "BLOCKER")).toBe(true);
  expect(expected.readiness.some((issue: any) => issue.severity === "WARNING")).toBe(true);
  const apiRequests: string[] = [];
  page.context().on("request", request => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url());
  });
  await page.context().route("**/api/**", route => route.abort());
  await page.goto("/");
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByLabel("地上風速 kt").fill("200");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("360");
  await page.getByLabel("5,000 ft風速 kt").fill("200");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    const input = page.getByLabel(`${name}出発Legの計画高度`, { exact: true });
    await input.fill(altitude!);
    await input.blur();
  }
  await page.getByRole("button", { name: "NAV LOGを作る", exact: true }).click();
  await page.waitForFunction(() => (window as any).localStates.at(-1)?.outcome !== null);
  const state = await page.evaluate(() => (window as any).localStates.at(-1));
  compare(localGolden(state), expected);
  await expect(page.locator(".issue-blocker").first()).toBeVisible();
  expect(apiRequests).toEqual([]);
});
