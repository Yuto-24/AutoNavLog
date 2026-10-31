import { enterImportWorkflow } from "./helpers/importWorkflow";
import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { localGolden, calculationCoreGolden } from "./helpers/localGolden";

const reference = JSON.parse(readFileSync(resolve("../tests/fixtures/issue_117_ftd_golden.json"), "utf8"));

import { compare } from "./helpers/compare";


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
    root.localCalculationCount = 0;
    const Original = window.Worker;
    root.Worker = class extends Original {
      constructor(url: string | URL, options?: WorkerOptions) {
        super(url, options);
        this.addEventListener("error", () => { root.localWorkerFailed = true; });
        this.addEventListener("message", (event) => {
          if (typeof event.data.value === "string") {
            try { const value = JSON.parse(event.data.value); if (value.workingRecovery || value.error) root.localStates.push(value); } catch { /* not a state */ }
          }
        });
      }
      postMessage(message: any, options?: any) {
        if (message.type === "APPLY" && message.argumentList?.[0]?.value === "calculate") root.localCalculationCount += 1;
        if (root.failLocalCalculation && message.type === "APPLY" &&
            message.argumentList?.[0]?.value === "calculate") {
          // Inject a Python validation exception through the real Comlink/Python path.
          message.argumentList[0].value = "updateAndRecalculate";
          message.argumentList[1].value = { weather_mode: "INVALID" };
        }
        super.postMessage(message, options);
      }
    };
  });
});

for (const width of [390, 1100, 1440]) {
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
  await enterImportWorkflow(page);
    await expect(page).toHaveTitle(/NavMate/);
    await expect(page.locator(".brand-subtitle").filter({ hasText: " / Local" })).toBeVisible();
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
    const native = JSON.parse(execFileSync(
      process.env.AUTONAVLOG_REFERENCE_PYTHON ?? resolve("../.venv/bin/python"),
      [resolve("../scripts/local_reference.py")], { encoding: "utf8" },
    ));
    compare(calculationCoreGolden(state), calculationCoreGolden(native));
    expect(state.readiness.calculationIsCurrent).toBe(true);
    const vorSelect = page.getByLabel("VOR基準局", { exact: true });
    await expect(vorSelect.locator("option:checked")).toHaveText("自動 MZE");
    await vorSelect.selectOption("AKE");
    await expect(vorSelect).toHaveValue("AKE");
    // AKE -> 米ノ津 TO point: GeographicLib WGS84 157.5778915705 deg true,
    // 23.6085683845 NM, plus the AIP station declination of 7 deg west.
    await expect(page.locator(".nav-leg-heading-row .vor-reference-cell").first()).toHaveText("165 / 23.6");
    await vorSelect.selectOption("SUC");
    await expect(vorSelect.locator("option:checked")).toHaveText("SUC — SHIMIZU (VORTAC)");
    await expect(vorSelect).toHaveAttribute("title", "AIP 2026-10-01 / 局からTOへのradial・距離");
    // Independent GeographicLib: 254.8629333750 deg true / 140.3544999448 NM.
    await expect(page.locator(".nav-leg-heading-row .vor-reference-cell").first()).toHaveText("262 / 140.4");
    await vorSelect.selectOption("__AUTO__");
    await expect(vorSelect.locator("option:checked")).toHaveText("自動 MZE");
    await expect(vorSelect).toHaveAttribute("title", "AIP 2026-08-06 / 局からTOへのradial・距離");
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
    await expect(page.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
    await page.screenshot({ path: `/tmp/issue117-local-${width}.png`, fullPage: true });
    const mode = page.getByRole("radio", { name: "四捨五入", exact: true });
    const normal = page.getByRole("radio", { name: "通常", exact: true });
    await expect(normal).toBeChecked();
    await expect(mode).not.toBeChecked();
    const beforeToggleCount = await page.evaluate(() => (window as any).localCalculationCount);
    await mode.evaluate((input: HTMLInputElement) => { input.click(); input.click(); });
    await expect(mode).toBeDisabled();
    await expect(mode).toBeChecked();
    await expect(mode).toBeEnabled();
    const rounded = await page.evaluate(() => (window as any).localStates.at(-1));
    expect(rounded.roundingMode).toBe(true);
    expect(await page.evaluate(() => (window as any).localCalculationCount)).toBe(beforeToggleCount + 1);
    expect(rounded.project.sections).toEqual(state.project.sections);
    expect(rounded.outcome.sections.some((zone: any, index: number) =>
      zone.ground_speed_kt.automatic_value !== state.outcome.sections[index].ground_speed_kt.automatic_value)).toBe(true);
    for (const zone of rounded.outcome.sections) {
      const wind = zone.wind_direction_deg_from;
      const direction = wind.adopted_source === "MANUAL" ? wind.manual_override : wind.automatic_value;
      expect(direction === null || direction % 10 === 0).toBe(true);
    }
    for (const row of rounded.outcome.display_rows) {
      if (typeof row.toat.effective_value === "number" && row.toat.text !== null) {
        expect(row.toat.text).toMatch(/^-?\d+$/);
        expect(row.toat.text).not.toBe("-0");
      }
    }
    if (width === 1440) {
      const direction = page.locator('.nav-log-table input[aria-label$="手動風向"]').first();
      const speed = page.locator('.nav-log-table input[aria-label$="手動風速"]').first();
      const temperature = page.locator('.nav-log-table input[aria-label$="手動気温"]').first();
      await direction.fill("5");
      await speed.fill("21");
      await temperature.fill("-1.5");
      await temperature.blur();
      await expect(mode).toBeEnabled();
      await expect(direction).toHaveValue("010");
      await expect(temperature).toHaveValue("-2");
      await direction.focus();
      await expect(direction).toHaveValue("5");
      await direction.blur();
      await temperature.focus();
      await expect(temperature).toHaveValue("-1.5");
      await temperature.blur();
      const manual = await page.evaluate(() => (window as any).localStates.at(-1));
      expect(manual.project.sections[0].manual_wind_direction_deg).toBe(5);
      expect(manual.outcome.sections[0].wind_direction_deg_from.manual_override).toBe(10);
      expect(manual.outcome.sections[0].temperature_c.manual_override).toBe(-1.5);
      await direction.fill("");
      await speed.fill("");
      await temperature.fill("");
      await temperature.blur();
      await expect(mode).toBeEnabled();
    }
    for (const cell of await page.locator(".vor-reference-cell").allTextContents()) {
      const distance = Number(cell.split(" / ")[1]);
      if (Number.isFinite(distance)) expect(distance * 2).toBe(Math.round(distance * 2));
    }
    // Input -> route -> readiness -> NAV LOG placement is already asserted above.
    // Read the total distance/time first, then the selected calculation mode.
    const summaryBox = await page.locator(".nav-log-summary").boundingBox();
    const modeBox = await mode.boundingBox();
    expect(modeBox!.y).toBeGreaterThanOrEqual(summaryBox!.y + summaryBox!.height);
    const tableBox = await page.locator(".nav-log-scroll").boundingBox();
    expect(modeBox!.y + modeBox!.height).toBeLessThanOrEqual(tableBox!.y);
    const controlBox = await page.locator(".nav-log-rounding-control").boundingBox();
    const addColumnBox = await page.getByRole("button", { name: "VOR/DME列を追加", exact: true }).boundingBox();
    const navLogBox = await page.locator(".nav-log-section").boundingBox();
    expect(controlBox!.x + controlBox!.width).toBeLessThanOrEqual(navLogBox!.x + navLogBox!.width);
    if (width >= 1100) {
      expect(Math.abs(controlBox!.y + controlBox!.height / 2 - addColumnBox!.y - addColumnBox!.height / 2)).toBeLessThan(1);
      expect(controlBox!.x).toBeGreaterThanOrEqual(addColumnBox!.x + addColumnBox!.width);
    } else {
      expect(controlBox!.y).toBeGreaterThanOrEqual(addColumnBox!.y + addColumnBox!.height);
    }
    await page.getByRole("button", { name: "メッセージを閉じる" }).click();
    await page.locator(".nav-log-section").screenshot({ path: `/tmp/issue234-on-${width}.png` });
    await page.locator(".nav-log-rounding-control").screenshot({ path: `/tmp/issue234-control-${width}.png` });
    const completedCount = await page.evaluate(() => (window as any).localCalculationCount);
    await mode.click();
    expect(await page.evaluate(() => (window as any).localCalculationCount)).toBe(completedCount);
    await mode.focus();
    await mode.press("ArrowLeft");
    await expect(mode).not.toBeChecked();
    await expect(normal).toBeChecked();
    await expect(mode).toBeEnabled();
    const offAgain = await page.evaluate(() => (window as any).localStates.at(-1));
    compare(calculationCoreGolden(offAgain), calculationCoreGolden(state));
    if (width === 1100) {
      await mode.click();
      await expect(mode).toBeChecked();
      await expect(mode).toBeEnabled();
      await page.getByRole("button", { name: "保存", exact: true }).click();
      await expect(page.getByRole("status").filter({ hasText: "プロジェクトを保存しました" })).toBeVisible();
      await expect(mode).toBeChecked();
      await page.getByLabel("保存済み", { exact: true }).selectOption(state.project.id);
      await page.getByRole("button", { name: "保存済みプロジェクトを開く", exact: true }).click();
      await expect(page.locator(".nav-log-table")).toHaveCount(0);
      await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
      await expect(mode).not.toBeChecked();
      await expect(normal).toBeChecked();
      await expect(mode).toBeEnabled();
      await mode.click();
      await expect(mode).toBeChecked();
      await expect(mode).toBeEnabled();
      await page.reload();
      await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
      await expect(page.locator(".nav-log-table")).toHaveCount(0);
      await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
      await expect(mode).not.toBeChecked();
      await expect(normal).toBeChecked();
      await expect(mode).toBeEnabled();
    }
    const previousTable = await page.locator(".nav-log-table").innerText();
    await page.evaluate(() => { (window as any).failLocalCalculation = true; });
    await mode.click();
    await expect(page.getByRole("alert")).toContainText("入力内容を確認してください。");
    expect(await page.locator(".nav-log-table").innerText()).toBe(previousTable);
    await expect(mode).not.toBeChecked();
    await expect(normal).toBeChecked();
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

for (const corrupt of [false, true]) {
  test(`local asset ${corrupt ? "hash mismatch" : "failure"} is visible without API fallback`, async ({ page }) => {
    const apiRequests: string[] = [];
    page.context().on("request", request => {
      if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url());
    });
    if (corrupt) {
      await page.context().route("**/local/autonavlog-*.whl", async route => {
        const response = await route.fetch();
        const bytes = await response.body();
        bytes[bytes.length - 1] ^= 1;
        await route.fulfill({ response, body: bytes });
      });
    } else {
      await page.context().route("**/local/manifest.json", route => route.fulfill({ status: 503, body: "" }));
    }
    await page.goto("/");
  await enterImportWorkflow(page);
    await expect(page.getByRole("alert")).toContainText("処理を実行できませんでした。画面を再読み込みしてください。");
    expect(apiRequests).toEqual([]);
  });
}


test("strong FTD wind preserves Python Warning and Blocker results", async ({ page }) => {
  const raw = execFileSync(
    process.env.AUTONAVLOG_REFERENCE_PYTHON ?? resolve("../.venv/bin/python"),
    [resolve("../scripts/local_reference.py"), "--strong-wind"],
    { encoding: "utf8" },
  );
  const expected = calculationCoreGolden(JSON.parse(raw));
  expect(expected.readiness.some((issue: any) => issue.severity === "BLOCKER")).toBe(true);
  expect(expected.readiness.some((issue: any) => issue.severity === "WARNING")).toBe(true);
  const apiRequests: string[] = [];
  page.context().on("request", request => {
    if (new URL(request.url()).pathname.startsWith("/api/")) apiRequests.push(request.url());
  });
  await page.context().route("**/api/**", route => route.abort());
  await page.goto("/");
  await enterImportWorkflow(page);
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
  compare(calculationCoreGolden(state), expected);
  await expect(page.locator(".issue-blocker").first()).toBeVisible();
  expect(apiRequests).toEqual([]);
});


test("actual MSM FORECAST reaches NAV LOG with Python provenance and no fallback", async ({ page }) => {
  // #125's historical numerical regression explicitly injects its offline client.
  // Production defaults and #144 tests use the real prepared-data adapter.
  await page.context().route("**/assets/local.worker-*.js", async route => {
    const response = await route.fetch();
    const source = await response.text();
    await route.fulfill({ response, body: source.replace(
      'local_application = LocalApplication(Path("/home/pyodide/data"))',
      'local_application = LocalApplication(Path("/home/pyodide/data"), forecast_fixture=Path("/home/pyodide/data/msm-fixture"))',
    ) });
  });
  const expected = JSON.parse(execFileSync(
    process.env.AUTONAVLOG_REFERENCE_PYTHON ?? resolve("../.venv/bin/python"),
    [resolve("../scripts/local_reference.py"), "--forecast"], { encoding: "utf8" },
  ));
  const requests: string[] = [];
  page.context().on("request", request => {
    if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request.url());
  });
  await page.context().route("**/api/**", route => route.abort());
  await page.goto("/");
  await enterImportWorkflow(page);
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-12");
  await page.getByLabel("ETD JST", { exact: true }).fill("12:00");
  await page.getByLabel("気象モード").selectOption("FORECAST");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    const input = page.getByLabel(`${name}出発Legの計画高度`, { exact: true });
    await input.fill(altitude!); await input.blur();
  }
  await page.getByRole("button", { name: "NAV LOGを作る", exact: true }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  const state = await page.evaluate(() => (window as any).localStates.at(-1));
  compare(calculationCoreGolden(state), calculationCoreGolden(expected));
  expect(state.runtime.weatherLabel).toBe(expected.runtime.weatherLabel);
  expect(state.outcome.selected_forecast_run_id).toBe("20260912030000");
  expect(state.readiness.calculationIsCurrent).toBe(true);
  expect(requests).toEqual([]);
});

test("Pyodide core: saved Run, UMK, OMARU, inbound, overrides and Check Point", async ({ page }) => {
  // Test-only Python execution; normal UI/Worker wiring is exercised above. No debug endpoint.
  const cases = [
    { forecast: true, pinned: true }, { route: "umk" }, { route: "omaru" },
    { route: "inbound" }, { manual: true },
  ];
  const expected = cases.map(options => JSON.parse(execFileSync(
    process.env.AUTONAVLOG_REFERENCE_PYTHON ?? resolve("../.venv/bin/python"),
    [resolve("../scripts/local_reference.py"), ...Object.entries(options).flatMap(([key, value]) =>
      typeof value === "boolean" ? [`--${key}`] : [`--${key}`, value])], { encoding: "utf8" },
  )));
  const files = Object.fromEntries([
    "issue_117_ftd.json", "issue_43_golden.kml", "issue_125_umk.kml",
    "issue_125_omaru.kml", "issue_125_inbound.kml",
  ].map(name => [name, readFileSync(resolve(`../tests/fixtures/${name}`), "utf8")]));
  await page.goto("/favicon-32.png");
  const actual = await page.evaluate(async ({ source, files, cases }) => {
    const workerSource = String.raw`
      importScripts('https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.js');
      onmessage = async ({data}) => {
        try {
          const p = await loadPyodide();
          await p.loadPackage(['pydantic','tzdata','micropip','numpy']);
          await p.runPythonAsync('import micropip\nawait micropip.install(["defusedxml==0.7.1", "geographiclib==2.1"])');
          const manifest = await (await fetch(data.base + '/local/manifest.json')).json();
          for (const name of manifest.wheels) {
            const bytes = new Uint8Array(await (await fetch(data.base + '/local/' + name)).arrayBuffer());
            p.FS.writeFile('/tmp/' + name, bytes);
            await p.runPythonAsync('await micropip.install("emfs:/tmp/' + name + '", deps=False)');
          }
          p.unpackArchive(await (await fetch(data.base + '/local/' + manifest.data)).arrayBuffer(), 'zip', {extractDir:'/home/pyodide'});
          p.FS.mkdirTree('/home/pyodide/tests/fixtures');
          p.FS.symlink('/home/pyodide/data/msm-fixture', '/home/pyodide/tests/fixtures/msm');
          for (const [name, text] of Object.entries(data.files)) p.FS.writeFile('/home/pyodide/tests/fixtures/' + name, text);
          p.runPython('from autonavlog.domain.planning import RjfmRunwayGuidance\nlegacy = RjfmRunwayGuidance.model_validate_json(\'{"runway":"09","status":"VALID","turn_direction":"LEFT","full_left_turns":1}\')\nassert legacy.full_turns == 1');
          p.globals.set('case_source', data.source);
          p.globals.set('case_options', JSON.stringify(data.cases));
          const result = p.runPython('import json\nns={"__name__":"runtime_cases", "__file__":"/home/pyodide/scripts/local_reference.py"}\nexec(case_source, ns)\njson.dumps([ns["reference_state"](local=True, **opts) for opts in json.loads(case_options)], allow_nan=False)');
          postMessage({result});
        } catch (error) { postMessage({error:String(error)}); }
      };
    `;
    const url = URL.createObjectURL(new Blob([workerSource], { type: "text/javascript" }));
    const worker = new Worker(url);
    try {
      return await new Promise<any[]>((resolve, reject) => {
        worker.onerror = error => reject(new Error(error.message));
        worker.onmessage = ({ data }) => data.error ? reject(new Error(data.error)) : resolve(JSON.parse(data.result));
        worker.postMessage({ source, files, cases, base: location.origin });
      });
    } finally { worker.terminate(); URL.revokeObjectURL(url); }
  }, { source: readFileSync(resolve("../scripts/local_reference.py"), "utf8"), files, cases });
  actual.forEach((state, index) => compare(calculationCoreGolden(state), calculationCoreGolden(expected[index]), `core[${index}]`));
  expect(actual[0].project.selected_forecast_run_id).toBe("20260912000000");
  expect(actual[0].outcome.selected_forecast_run_id).toBe("20260912000000");
  expect(actual[0].outcome.issues.some((issue: any) => issue.code === "FORECAST_UPDATE_AVAILABLE")).toBe(true);
  expect(actual[1].outcome.sections.some((zone: any) => zone.to_name === "UMK/RCA")).toBe(true);
  expect(actual[2].outcome.sections.some((zone: any) => zone.to_name === "UMK/RCA")).toBe(true);
  expect(actual[3].outcome.rjfm_inbound_guidance).not.toBeNull();
  expect(actual[4].outcome.check_point_projections).toHaveLength(1);
});
