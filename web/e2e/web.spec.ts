import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import type { NavLogDisplayRow, WebState } from "../src/types";

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

const kmlFromRjfk = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document><Placemark><name>RJFK-RJFO</name><LineString><coordinates>
    130.7194444444,31.8033333333,0
    131.0000000000,32.4000000000,0
    131.4000000000,33.1000000000,0
    131.7372222222,33.4794444444,0
  </coordinates></LineString></Placemark></Document>
</kml>`;

const multiDocumentKmz = Buffer.from(
  "UEsDBBQAAAAIAG0kCl36V3yWdAAAAJwAAAAJAAAAZmlyc3Qua21sTY1BCgMhDEWvMsx6MKi7kuYEXRR6ApmmU1HjoAF7/NKu3H14vPcxlbx8SpZ+Xd+q5wVgjGHqyXLEboQVUsngjFsJ7znsXEJLhBIK0yu2rgj/jbco/NAW5SDca23PKEG5k/V283ax3m3eIcwIYZZgyv9O6QtQSwMEFAAAAAgAbSQKXeGCuSN0AAAAnQAAAAoAAABzZWNvbmQua21sTY1BCsMgEEWvErIODuouTOcEXRR6AjFDIuoYVLDHL+3K5efx3seY0/LJSdpjvXq/d4Axhio3yxmaEu4QcwKjzEr4Ss5zdjUSistMjX2RA+E/8BmE370GOQl9KfUI4jo30lZvVi/ams0ahBkhzBJM/d8rfQFQSwECFAMUAAAACABtJApd+ld8lnQAAACcAAAACQAAAAAAAAAAAAAAgAEAAAAAZmlyc3Qua21sUEsBAhQDFAAAAAgAbSQKXeGCuSN0AAAAnQAAAAoAAAAAAAAAAAAAAIABmwAAAHNlY29uZC5rbWxQSwUGAAAAAAIAAgBvAAAANwEAAAAA",
  "base64",
);

const displayCellFields = [
  "pa", "toat", "cas", "tas", "tc", "variation", "mc", "wind", "wca",
  "mh", "distance", "gs", "ete", "eto", "ato", "ate", "fuel",
] as const satisfies ReadonlyArray<keyof NavLogDisplayRow>;

async function expectDisplayProjectionToMatchWebTable(page: Page): Promise<void> {
  const state = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    return await response.json() as WebState;
  });
  const expectedRows = state.outcome?.display_rows;
  if (expectedRows === undefined) throw new Error("calculated display rows are missing");
  const actualRows = await page.locator(".nav-log-table tbody tr[data-row-type]").evaluateAll(
    (rows) => rows.map((row) => ({
      rowType: row.getAttribute("data-row-type"),
      sequence: Number(row.getAttribute("data-row-sequence")),
      values: Array.from(row.querySelectorAll<HTMLElement>("td[data-display-text]"))
        .map((cell) => cell.dataset.displayText ?? ""),
    })),
  );

  expect(actualRows).toHaveLength(expectedRows.length);
  expectedRows.forEach((row, index) => {
    expect(actualRows[index]?.rowType).toBe(row.row_type);
    expect(actualRows[index]?.sequence).toBe(row.sequence);
    if (row.row_type === "LEG_SEPARATOR") {
      expect(actualRows[index]?.values).toEqual([]);
      return;
    }
    expect(actualRows[index]?.values).toEqual([
      row.from_name,
      row.to_name,
      ...displayCellFields.map((field) => {
        const cell = row[field];
        if (typeof cell !== "object" || cell === null || !("text" in cell)) {
          throw new Error(`display field ${field} is not a display cell`);
        }
        return cell.text ?? "";
      }),
    ]);
  });
}

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
  await expect(page.getByLabel("TO")).toHaveValue(/RJFO/);
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  await expect(page.getByText("VREP", { exact: true }).first()).toBeVisible();
  const altitudeInputs = page.locator(".table-number-input");
  await expect(altitudeInputs.first()).toHaveValue("");
  await expect(altitudeInputs.last()).toHaveValue("1500");
  for (let index = 0; index < await altitudeInputs.count(); index += 1) {
    await altitudeInputs.nth(index).fill("4500");
  }
  const altitudeCandidates = page.locator(".altitude-candidate-select");
  const firstAltitudeCandidate = await altitudeCandidates
    .first()
    .locator("option")
    .first()
    .getAttribute("value");
  if (firstAltitudeCandidate === null) throw new Error("Altitude candidate is missing");
  for (let index = 0; index < await altitudeCandidates.count(); index += 1) {
    await altitudeCandidates.nth(index).selectOption({ index: 0 });
  }
  const cruiseAltitude = page.getByLabel(/出発Legの巡航高度候補/).first();
  const variationGuidance = page.locator(".altitude-course");
  await expect(variationGuidance.first()).toContainText("VAR +7°");
  await expect(variationGuidance.nth(1)).toContainText("VAR +8°");
  const cruiseCandidate = await cruiseAltitude.locator("option").first().getAttribute("value");
  if (cruiseCandidate === null) {
    throw new Error("Cruise altitude candidate is missing");
  }
  await cruiseAltitude.selectOption(cruiseCandidate);
  const patternAltitude = page.getByLabel("今回採用する場周経路高度");
  const arrivalRow = page.locator(".destination-row");
  const confirmDestination = page.getByRole("button", {
    name: "目的空港・場周高度を確定",
  });
  await expect(page.locator(".input-rail").getByLabel("今回採用する場周経路高度")).toHaveCount(0);
  await expect(arrivalRow.getByLabel("今回採用する場周経路高度")).toBeVisible();
  await expect(arrivalRow.getByText("飛行場標高", { exact: true })).toBeVisible();
  await expect(arrivalRow.getByText("17 ft MSL", { exact: true })).toBeVisible();
  await expect(arrivalRow).toContainText("master 1,000 ft MSL（標高差 983 ft）");
  await expect(patternAltitude).toHaveValue("1000");
  await patternAltitude.fill("");
  await expect(confirmDestination).toBeDisabled();
  await patternAltitude.fill("1000");
  await expect(confirmDestination).toBeEnabled();
  await patternAltitude.fill("1300");
  await expect(patternAltitude).toHaveValue("1300");
  await confirmDestination.click();
  await expect(patternAltitude).toHaveValue("1300");
  await expect(altitudeInputs.first()).toHaveValue(firstAltitudeCandidate);
  await expect(altitudeInputs.last()).toHaveValue("1800");
  await expect(cruiseAltitude).toHaveValue(cruiseCandidate);
  await expect(page.locator(".altitude-review-row")).toHaveCount(0);
  let releaseCalculationRequest = () => {};
  const calculationRequestReleased = new Promise<void>((resolve) => {
    releaseCalculationRequest = resolve;
  });
  await page.route("**/api/calculation-jobs", async (route) => {
    await calculationRequestReleased;
    await route.continue();
  }, { times: 1 });
  const calculateButton = page.locator(".status-actions .primary-button");
  await expect(calculateButton).toHaveText("NAV LOGを作る");
  await calculateButton.click();
  const calculationProgress = page.getByRole("progressbar", { name: "NAV LOGを計算中" });
  await expect(calculationProgress).toBeVisible();
  await expect(calculateButton).toHaveText("NAV LOGを計算中…");
  releaseCalculationRequest();
  await expect(page.getByLabel("計算済みNAV LOG")).toBeFocused();
  await expect(calculationProgress).toBeHidden();
  const firstRow = page.locator(".nav-log-table .nav-leg-detail-row").first();
  await expect(
    page.locator(".nav-log-table").getByLabel(/計画高度$/).first(),
  ).toHaveValue(firstAltitudeCandidate);
  await expect(page.locator(".nav-log-table .nav-leg-heading-row").first().locator("td").nth(7)).toHaveText("+7");
  const windInputs = firstRow.locator(".nav-log-wind-inputs");
  const windDirectionInput = firstRow.getByLabel(/手動風向$/);
  const windSpeedInput = firstRow.getByLabel(/手動風速$/);
  await expect(windInputs).toBeVisible();
  await expect(windDirectionInput).toBeVisible();
  await expect(windSpeedInput).toBeVisible();
  await expect(windDirectionInput).toHaveAttribute("placeholder", "DIR");
  await expect(windSpeedInput).toHaveAttribute("placeholder", "0");
  await expect.poll(() => windInputs.evaluate((element) => getComputedStyle(element).opacity)).toBe("1");
  await expect(page.getByLabel("NAV LOG高度ポリシー")).toContainText("PA = MSL");
  await expect(page.getByLabel("NAV LOG高度ポリシー")).toContainText(
    "QNH補正はNAV LOG計算に使用しません。",
  );
  await expect(page.getByLabel("目的地空港の風予報")).toContainText(
    "目的地風: 200/8 kt",
  );
  const finalRow = page.locator(".nav-log-table .nav-destination-info-row");
  await expect(finalRow.getByRole("cell", { name: "200/8", exact: true })).toBeVisible();
  await expect(finalRow.getByLabel(/手動風向$/)).toHaveCount(0);
  await expect(finalRow.getByLabel(/手動風速$/)).toHaveCount(0);
  await expect(
    page.locator(".nav-log-table").getByText("自動", { exact: true }),
  ).toHaveCount(0);
  for (const warning of [
    "ESTIMATED_QNH_NOT_OFFICIAL",
    "VERIFY_WITH_OFFICIAL_AERODROME_QNH",
  ]) {
    await expect(page.locator(".qnh-warning").filter({ hasText: warning })).toHaveCount(0);
  }
  await expectDisplayProjectionToMatchWebTable(page);
}

test("desktop workflow renders and stays fail-closed", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await expect(page.locator("body")).not.toBeEmpty();
  await expect(page.getByText("開発用固定気象（出力不可）", { exact: true })).toBeVisible();
  await expect(page.getByLabel("TO")).toHaveValue("");
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0);
  await page.screenshot({
    path: path.join(repositoryRoot, "docs/web-design/implementation-desktop.png"),
    fullPage: false,
  });

  await calculateNavLog(page);

  await expect(
    page.getByText("PATTERN_ALTITUDE_REQUIRED", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("DEVELOPMENT_WEATHER_PROVIDER", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "A4転記補助HTMLを出力" })).toBeDisabled();
  await page.screenshot({
    path: path.join(repositoryRoot, "docs/web-design/implementation-calculated.png"),
    fullPage: true,
  });

  // Render a deliberately failed display cell through the same Web component.
  // This keeps the visual contract explicit: only UNAVAILABLE says 未取得 and
  // receives the bold red treatment; a neighboring BLANK remains truly empty.
  const unavailableState = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    const next = await response.json() as WebState;
    const destination = next.outcome?.display_rows.find(
      (row) => row.row_type === "DESTINATION_INFO",
    );
    if (destination === undefined) throw new Error("destination display row is missing");
    destination.cas = {
      state: "UNAVAILABLE",
      text: "未取得",
      effective_value: null,
      reason_code: "E2E_TRUE_FAILURE",
      manual: false,
    };
    return next;
  });
  await page.route("**/api/state", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(unavailableState),
    });
  }, { times: 1 });
  await page.reload();
  const unavailableCell = page.locator(
    '.nav-destination-info-row [data-cell-state="UNAVAILABLE"]',
  );
  await expect(unavailableCell).toHaveText("未取得");
  const unavailableStyle = await unavailableCell.evaluate((element) => {
    const style = getComputedStyle(element);
    return { fontWeight: style.fontWeight, backgroundColor: style.backgroundColor };
  });
  expect(Number(unavailableStyle.fontWeight)).toBeGreaterThanOrEqual(700);
  expect(unavailableStyle.backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
  const blankCell = page.locator(
    '.nav-destination-info-row [data-cell-state="BLANK"]',
  ).first();
  await expect(blankCell).toHaveText("");
  await expect(blankCell).not.toHaveClass(/unavailable-value/);

  expect(pageErrors).toEqual([]);
});

test("changed ALT appears in PA with lesson display precision", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("/");
  await calculateNavLog(page);

  await expect(
    page.getByLabel("ALT・Phase・FUEL・VAR・TGLを原資料と照合しました"),
  ).toHaveCount(0);
  const calculate = page.getByRole("button", { name: "NAV LOGを再計算" });
  await page.locator(".table-number-input").first().fill("5500");

  await expect(calculate).toBeEnabled();
  await calculate.click();

  const altitudeInput = page.locator(".nav-log-table").getByLabel(/計画高度$/).first();
  await expect(altitudeInput).toHaveValue("5500");
  const firstParent = page.locator(".nav-log-table .nav-leg-heading-row").first();
  await expect(firstParent.locator("td").nth(6)).toHaveText(/^\d{3}$/);
  await expect(firstParent.locator("td").nth(7)).toHaveText("+7");
  await expect(firstParent.locator("td").nth(8)).toHaveText(/^\d{3}$/);
  const course = await firstParent.locator("td").nth(6).textContent();
  const variation = await firstParent.locator("td").nth(7).textContent();
  const magneticCourse = await firstParent.locator("td").nth(8).textContent();
  expect((Number(course) + Number(variation) + 360) % 360).toBe(Number(magneticCourse));
  await expect(firstParent.locator("td").nth(12)).toHaveText(
    /^\d+\.[05] \/ \d+\.[05]$/,
  );
  await expect(firstParent.locator("td").nth(14)).toHaveText(
    /^\d+\.[05] \/ \d+\.[05]$/,
  );
  await expect(firstParent.locator("td").nth(18)).toHaveText(
    /^\d+\.\d \/ \d+\.\d$/,
  );
  const firstDetail = page.locator(".nav-log-table .nav-leg-detail-row").first();
  await expect(firstDetail.locator("td").nth(12)).toHaveText(/^\d+\.[05]$/);
  await expect(firstDetail.locator("td").nth(14)).toHaveText(/^\d+\.[05]$/);
  const displayedWca = await firstDetail.locator("td").nth(10).textContent();
  const displayedHeading = await firstDetail.locator("td").nth(11).textContent();
  expect((Number(magneticCourse) + Number(displayedWca) + 360) % 360).toBe(
    Number(displayedHeading),
  );
  await expect(page.getByText("DESCENT_END", { exact: true })).toHaveCount(0);
  await expect(page.locator(".nav-leg-heading-row")).not.toHaveCount(0);

  const fuelTable = page.locator(".fuel-plan-table");
  await expect(fuelTable.locator("col")).toHaveCount(5);
  await expect(fuelTable.getByText("TAXI・RUN UP", { exact: true })).toBeVisible();
  await expect(fuelTable.getByText("MIN REQUIRED", { exact: true })).toBeVisible();
  await expect(fuelTable.locator("tbody tr")).toHaveCount(10);
  const tableLayout = await page.locator(".nav-log-tables").evaluate((container) => {
    const navTable = container.querySelector<HTMLElement>(".nav-log-table");
    const planTable = container.querySelector<HTMLElement>(".fuel-plan-table");
    if (navTable === null || planTable === null) throw new Error("NAV LOG tables are missing");
    return {
      gap: planTable.offsetLeft - (navTable.offsetLeft + navTable.offsetWidth),
      fuelWidth: planTable.offsetWidth,
      navWidth: navTable.offsetWidth,
      fuelRowHeight: planTable.querySelector<HTMLElement>("tbody tr")?.offsetHeight ?? 0,
      fuelAmountAlignment: getComputedStyle(
        planTable.querySelector<HTMLElement>(".fuel-amount")!,
      ).justifyContent,
    };
  });
  expect(tableLayout.gap).toBeGreaterThanOrEqual(11);
  expect(tableLayout.gap).toBeLessThanOrEqual(13);
  expect(tableLayout.navWidth).toBeLessThanOrEqual(1700);
  expect(tableLayout.fuelWidth).toBeLessThanOrEqual(430);
  expect(tableLayout.fuelWidth).toBeLessThan(tableLayout.navWidth / 2);
  expect(tableLayout.fuelRowHeight).toBeLessThanOrEqual(25);
  expect(tableLayout.fuelAmountAlignment).toBe("center");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.locator(".destination-row").getByLabel("今回採用する場周経路高度"),
  ).toBeVisible();
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(mobileOverflow).toBeLessThanOrEqual(1);

  const unexpectedConsoleErrors = consoleErrors.filter(
    (message) => !message.includes("401 (Unauthorized)"),
  );
  expect(unexpectedConsoleErrors).toEqual([]);
});

test("climb and descent legs show magnetic-course altitude candidates", async ({ page }) => {
  await page.goto("/");

  const openPaste = page.getByRole("button", { name: "KMLを貼り付け" });
  await openPaste.click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(kml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  const phaseSelects = page.getByLabel(/出発LegのPhase/);
  await phaseSelects.nth(0).selectOption("CLIMB");
  await phaseSelects.nth(2).selectOption("DESCENT");

  const climbCandidate = page.getByLabel(/出発Legの上昇先の巡航高度候補/);
  const descentCandidate = page.getByLabel(/出発Legの降下開始時の巡航高度候補/);
  await expect(climbCandidate).toBeVisible();
  await expect(descentCandidate).toBeVisible();
  await expect(climbCandidate.locator("option")).not.toHaveCount(0);
  await expect(descentCandidate.locator("option")).not.toHaveCount(0);
  await expect(
    page.getByText(/上昇のALTは上昇先、降下のALTは降下開始時の巡航高度/),
  ).toBeVisible();

  const climbAltitude = await climbCandidate.locator("option").first().getAttribute("value");
  const descentAltitude = await descentCandidate.locator("option").first().getAttribute("value");
  if (climbAltitude === null || descentAltitude === null) {
    throw new Error("Climb or descent altitude candidate is missing");
  }
  await climbCandidate.selectOption(climbAltitude);
  await descentCandidate.selectOption(descentAltitude);
  await expect(page.locator(".altitude-review-row")).toHaveCount(1);
});

test("NAV LOG safe inputs validate and recalculate automatically", async ({ page }) => {
  await page.goto("/");
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await calculateNavLog(page);

  const recalculationRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/project/recalculate")) {
      recalculationRequests.push(request.url());
    }
  });

  const altitude = page.locator(".nav-log-table").getByLabel(/計画高度$/).first();
  const altitudeResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/project/recalculate") && response.ok(),
  );
  await altitude.fill("5500");
  await altitudeResponse;
  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  const manualRecalculation = page.waitForResponse(
    (response) => response.url().endsWith("/api/calculation-jobs") && response.ok(),
  );
  await page.getByRole("button", { name: "NAV LOGを再計算" }).click();
  await manualRecalculation;
  await expect(page.getByText("NAV LOGを計算しました。準備状況と各値を確認してください。", { exact: true })).toBeVisible();
  await expect(altitude).toHaveValue("5500");

  const windDirection = page.locator(".nav-log-table").getByLabel(/手動風向$/).first();
  const windSpeed = page.locator(".nav-log-table").getByLabel(/手動風速$/).first();
  const requestCount = recalculationRequests.length;
  await windDirection.fill("270");
  await expect(page.getByText(/入力を確認してください。直前の正常な計算結果/)).toBeVisible();
  await expect(windDirection).toHaveAttribute("aria-invalid", "true");
  await page.waitForTimeout(850);
  expect(recalculationRequests).toHaveLength(requestCount);
  const saveResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/projects/save") && response.ok(),
  );
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await saveResponse;
  await expect(windDirection).toHaveValue("270");
  await expect(windDirection).toHaveAttribute("aria-invalid", "true");

  const windResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/project/recalculate") && response.ok(),
  );
  await windSpeed.fill("15");
  await windResponse;
  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  await expect(windDirection).toHaveAttribute("aria-invalid", "false");
  const splitRows = page.locator(".nav-log-table .nav-leg-detail-row");
  await expect(splitRows.first().getByLabel(/手動風向$/)).toHaveAttribute("aria-label", /→RCA 手動風向$/);
  await expect(splitRows.nth(1).getByLabel(/手動風向$/)).toHaveAttribute("aria-label", /^RCA→/);
  const cruiseWindDirection = splitRows.nth(1).getByLabel(/手動風向$/);
  const cruiseWindSpeed = splitRows.nth(1).getByLabel(/手動風速$/);
  await cruiseWindDirection.fill("180");
  await expect(cruiseWindDirection).toHaveAttribute("aria-invalid", "true");
  const cruiseWindResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/project/recalculate") && response.ok(),
  );
  await cruiseWindSpeed.fill("20");
  const cruiseResponse = await cruiseWindResponse;
  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  await expect(windDirection).toHaveValue("270");
  await expect(windSpeed).toHaveValue("15");
  const payload = cruiseResponse.request().postDataJSON() as {
    sections: Array<Record<string, unknown>>;
  };
  expect(payload.sections[0]).toMatchObject({
    manual_wind_direction_deg: 270,
    manual_wind_speed_kt: 15,
    manual_wind_by_phase: { CRUISE: { direction_deg_from: 180, speed_kt: 20 } },
  });
  await expect(page.getByRole("heading", { name: "NAV LOG" })).toBeVisible();
  await expect(page.locator("body")).not.toBeEmpty();
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0);
  const unexpectedConsoleErrors = consoleErrors.filter(
    (message) => !message.includes("401 (Unauthorized)"),
  );
  expect(unexpectedConsoleErrors).toEqual([]);
  await page.screenshot({ path: "/tmp/autonavlog-phase-wind.png", fullPage: false });
  await expect(page.locator(".nav-log-table th").nth(6)).toHaveText("TC");
  await expect(page.locator(".derived-readonly-cell").first()).toHaveAttribute("title", /表示専用セル/);
});

test("stale automatic recalculation cannot overwrite newer planning inputs", async ({ page }) => {
  await page.goto("/");
  await calculateNavLog(page);

  let releaseFirstResponse = () => {};
  let markFirstResponseReady = () => {};
  const firstResponseReady = new Promise<void>((resolve) => {
    markFirstResponseReady = resolve;
  });
  const firstResponseReleased = new Promise<void>((resolve) => {
    releaseFirstResponse = resolve;
  });
  const recalculationBodies: Array<Record<string, unknown>> = [];
  await page.route("**/api/project/recalculate", async (route) => {
    recalculationBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    if (recalculationBodies.length === 1) {
      const response = await route.fetch();
      markFirstResponseReady();
      await firstResponseReleased;
      await route.fulfill({ response });
      return;
    }
    await route.continue();
  });

  const altitude = page.locator(".nav-log-table").getByLabel(/計画高度$/).first();
  await altitude.fill("5500");
  await firstResponseReady;
  const fuel = page.getByLabel("FUEL gal");
  await fuel.fill("77");
  const latestResponse = page.waitForResponse((response) => {
    if (
      !response.url().endsWith("/api/project/recalculate") ||
      !response.ok()
    ) {
      return false;
    }
    const body = response.request().postDataJSON() as Record<string, unknown>;
    return body.total_usable_fuel_gal === 77;
  });
  releaseFirstResponse();
  await latestResponse;

  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  await expect(fuel).toHaveValue("77");
  await expect(altitude).toHaveValue("5500");
  expect(recalculationBodies).toHaveLength(2);
  expect(recalculationBodies[1]?.total_usable_fuel_gal).toBe(77);
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

test("KML start automatically selects FROM and keeps manual override", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();

  await page.getByRole("button", { name: "KMLを貼り付け" }).click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(kmlFromRjfk);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();

  const departure = page.getByLabel("FROM");
  await expect(page.getByLabel("飛行経路にする形状")).toHaveValue("line:0");
  await expect(departure).toHaveValue("RJFK");
  await expect(page.getByLabel("TO")).toHaveValue(/RJFO/);

  await departure.selectOption("RJFM");
  await expect(departure).toHaveValue("RJFM");
  await expect(
    page.getByText("KML始点から5 NM以内に出発空港が見つかりません。"),
  ).toHaveCount(0);
});
