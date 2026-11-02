import { expect, test, type Locator, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

async function importRoute(page: Page, fixture: string) {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(fileURLToPath(new URL(`../../tests/fixtures/${fixture}`, import.meta.url)));
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await expect(page.locator('input[type="file"]')).toBeEnabled();
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByLabel("地上風速 kt").fill("15");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("270");
  await page.getByLabel("5,000 ft風速 kt").fill("30");
  await expect(page.getByLabel("飛行経路候補")).not.toHaveValue("");
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.locator(".route-table .destination-row")).toBeVisible();
}

async function assertIntervalRows(page: Page) {
  const points = page.locator(".route-table .route-point-content");
  const legs = page.locator(".route-table .route-leg-row");
  expect(await points.count()).toBeGreaterThan(2);
  await expect(legs).toHaveCount(await points.count() - 1);
  for (const row of await legs.all()) {
    const geometry = await row.evaluate(element => {
      const read = (node: Element | null) => {
        if (!node) return null;
        const box = node.getBoundingClientRect();
        return { x: box.x, y: box.y, right: box.right, bottom: box.bottom, center: box.y + box.height / 2 };
      };
      const cell = (selector: string) => read(element.querySelector(selector));
      const from = document.getElementById(`route-point-${element.getAttribute("data-from-node-id")}`);
      const to = document.getElementById(`route-point-${element.getAttribute("data-to-node-id")}`);
      return {
        row: read(element)!, from: read(from), to: read(to),
        previousId: element.previousElementSibling?.getAttribute("data-node-id"),
        nextId: element.nextElementSibling?.getAttribute("data-node-id"),
        fromId: element.getAttribute("data-from-node-id"), toId: element.getAttribute("data-to-node-id"),
        phase: cell(".route-phase-primary"), input: cell(".table-number-input"),
        candidate: cell(".altitude-candidate-select"), detail: cell(".altitude-course"),
        fixed: cell(".fixed-altitude-control"), descent: cell(".descent-rate-result"),
      };
    });
    expect(geometry.previousId).toBe(geometry.fromId);
    expect(geometry.nextId).toBe(geometry.toId);
    expect(geometry.from).not.toBeNull();
    expect(geometry.to).not.toBeNull();
    expect(geometry.phase).not.toBeNull();
    // The entire leg, not just the center of its input, belongs between endpoints.
    expect(geometry.row.y).toBeGreaterThanOrEqual(geometry.from!.bottom);
    expect(geometry.row.bottom).toBeLessThanOrEqual(geometry.to!.y);
    for (const content of [geometry.phase, geometry.input, geometry.candidate, geometry.detail, geometry.fixed, geometry.descent]) {
      if (content) {
        expect(content.y).toBeGreaterThanOrEqual(geometry.row.y);
        expect(content.bottom).toBeLessThanOrEqual(geometry.row.bottom);
      }
    }
    if (geometry.input && geometry.candidate) {
      expect(Math.abs(geometry.input.center - geometry.candidate.center)).toBeLessThanOrEqual(1);
      expect(geometry.candidate.right).toBeLessThanOrEqual(geometry.input.x);
    }
    if (geometry.detail && geometry.input) expect(geometry.detail.y).toBeGreaterThanOrEqual(geometry.input.bottom);
    if (geometry.descent) expect(geometry.descent.y).toBeGreaterThanOrEqual(geometry.phase!.bottom);
  }
  const arrival = page.locator(".destination-row");
  await expect(arrival.locator(".route-point-content")).toHaveCount(1);
  await expect(arrival.locator(".arrival-altitude-control")).toHaveCount(1);
  await expect(arrival.locator(".phase-arrival-value")).toHaveCount(1);
  await expect(arrival).not.toHaveAttribute("data-section-id");
  const bounds = await arrival.evaluate(element => {
    const row = element.getBoundingClientRect();
    const content = element.querySelector(".arrival-altitude-control")!.getBoundingClientRect();
    return { top: row.top, bottom: row.bottom, contentTop: content.top, contentBottom: content.bottom };
  });
  expect(bounds.contentTop).toBeGreaterThanOrEqual(bounds.top);
  expect(bounds.contentBottom).toBeLessThanOrEqual(bounds.bottom);
}

async function assertWorkflowOrder(page: Page, width: number) {
  // A smooth scroll can advance between separate boundingBox RPCs. Measure
  // every workflow/guidance region in one frame without changing the geometry.
  const { boxes, guidanceBoxes } = await page.evaluate(() => {
    const read = (element: Element | null) => {
      if (!element) return null;
      const { x, y, width, height } = element.getBoundingClientRect();
      return width > 0 && height > 0 ? { x, y, width, height } : null;
    };
    return {
      boxes: [".input-rail", ".route-workspace", ".status-rail", ".nav-log-focus-target"]
        .map(selector => read(document.querySelector(selector))),
      guidanceBoxes: Array.from(document.querySelectorAll(".rjfm-inbound-guidance"), read),
    };
  });
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
  // Any applicable guidance must stay below the NAV LOG, including on narrow screens.
  for (const box of guidanceBoxes) {
    expect(box).not.toBeNull();
    expect(box!.y).toBeGreaterThanOrEqual(boxes[3]!.y + boxes[3]!.height - 1);
  }
}

async function selectFirstCandidate(row: Locator) {
  const select = row.locator(".altitude-candidate-select");
  const value = await select.locator("option").first().getAttribute("value");
  expect(value).not.toBeNull();
  await select.selectOption(value!);
  await expect(row.locator(".table-number-input")).toHaveValue(value!);
}

test("inbound route has compact spacing and staggered POINT versus leg separators", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 772, height: 1000 });
  await importRoute(page, "altitude_inbound_compact.kml");
  await page.getByRole("button", { name: "メッセージを閉じる" }).click();
  await expect(page.locator(".route-point-content strong")).toHaveText(["RJFO", "鶴御崎", "OMARU", "UMK", "有田", "RJFM"]);
  await expect(page.getByLabel("OMARU出発Legの固定高度")).toContainText("4,500 ft 固定");
  await expect(page.getByLabel("有田出発Legの計画高度", { exact: true })).toHaveValue("1500");
  await expect(page.locator(".arrival-airport-reference")).toContainText("19 ft MSL");
  await expect(page.getByLabel("今回採用する場周経路高度", { exact: true })).toHaveValue("1000");
  await assertIntervalRows(page);
  const layout = await page.locator(".route-table").evaluate(table => {
    const points = [...table.querySelectorAll(".route-point-content")].map(point => point.getBoundingClientRect().y);
    const rows = [...table.querySelectorAll(".route-point-row:not(.destination-row), .route-leg-row")];
    return {
      height: table.getBoundingClientRect().height,
      pointGaps: points.slice(1).map((y, index) => y - points[index]),
      rows: rows.map(row => ({
        isPoint: row.classList.contains("route-point-row"),
        cells: [...row.children].map(cell => {
          const style = getComputedStyle(cell);
          const divider = getComputedStyle(cell, "::after");
          return { bottom: style.borderBottomWidth, divider: divider.content !== "none", dividerWidth: divider.borderTopWidth,
            dividerTop: Number.parseFloat(divider.top), height: cell.getBoundingClientRect().height };
        }),
      })),
    };
  });
  // The rejected layout used ~120px per point interval and ~800px overall.
  // Bound default density separately from the variable-height editing tests.
  expect(layout.height).toBeLessThan(670);
  for (const gap of layout.pointGaps) expect(gap).toBeLessThan(95);
  for (const row of layout.rows) {
    expect(row.cells).toHaveLength(3);
    for (const [index, cell] of row.cells.entries()) {
      expect(cell.bottom).toBe("0px");
      const hasDivider = row.isPoint ? index > 0 : index === 0;
      expect(cell.divider).toBe(hasDivider);
      if (hasDivider) {
        expect(cell.dividerWidth).toBe("1px");
        expect(Math.abs(cell.dividerTop - cell.height / 2)).toBeLessThanOrEqual(1);
      }
    }
  }
  await page.locator(".route-table").screenshot({ path: testInfo.outputPath("inbound-compact-staggered-borders.png") });
});

for (const width of [390, 1100, 1440]) {
  test(`altitude controls stay between endpoints with warnings and calculated descent at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await importRoute(page, "issue_43_golden.kml");
    await assertIntervalRows(page);
    const candidateRow = page.locator(".route-table tbody tr").filter({ has: page.locator(".altitude-candidate-select") }).first();
    await candidateRow.locator(".table-number-input").fill("4400");
    await expect(candidateRow).toHaveClass(/altitude-warning-row/);
    await expect(candidateRow.locator(".altitude-course")).toContainText("候補外（警告）");
    await assertIntervalRows(page);
    // Repeated raw edits keep the same section and neighbouring input values.
    const neighbours = page.locator(".route-leg-row .table-number-input");
    const before = await neighbours.evaluateAll(inputs => inputs.map(input => (input as HTMLInputElement).value));
    for (const value of ["", "4600", "4400"]) {
      await candidateRow.locator(".table-number-input").fill(value);
      await expect(candidateRow.locator(".table-number-input")).toHaveValue(value);
      await assertIntervalRows(page);
    }
    expect((await neighbours.evaluateAll(inputs => inputs.map(input => (input as HTMLInputElement).value))).slice(1)).toEqual(before.slice(1));
    await selectFirstCandidate(candidateRow);
    await expect(candidateRow).not.toHaveClass(/altitude-warning-row/);
    await assertIntervalRows(page);

    const editablePoint = page.locator(".route-point-row").filter({ has: page.locator(".route-name-edit, .route-name-editor") }).first();
    await editablePoint.locator(".route-name-edit").click();
    await editablePoint.locator(".route-name-editor input").fill(" ");
    await editablePoint.locator(".route-name-save").click();
    await expect(editablePoint.locator(".field-error")).toBeVisible();
    await assertIntervalRows(page);
    await editablePoint.locator(".route-name-cancel").click();
    // A saved long name changes natural row height without changing leg ownership.
    const originalName = await editablePoint.locator("strong").innerText();
    await editablePoint.locator(".route-name-edit").click();
    await editablePoint.locator(".route-name-editor input").fill("長い経路点名称を折り返して区間高度の位置を確認する地点");
    await editablePoint.locator(".route-name-save").click();
    await expect(editablePoint.locator(".route-name-editor")).toHaveCount(0);
    await assertIntervalRows(page);

    await editablePoint.locator(".route-name-edit").click();
    await editablePoint.locator(".route-name-editor input").fill(originalName);
    await editablePoint.locator(".route-name-save").click();
    await expect(editablePoint.locator("strong")).toHaveText(originalName);

    for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
      await page.getByLabel(`${name}出発Legの計画高度`, { exact: true }).fill(altitude);
    }
    await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
    await expect(page.locator(".nav-log-table")).toBeVisible();
    await expect(page.locator(".descent-rate-result")).toBeVisible();
    await assertIntervalRows(page);
    await assertWorkflowOrder(page, width);
    await page.locator(".route-table").screenshot({ path: testInfo.outputPath(`altitude-interval-${width}.png`) });
  });

  test(`fixed and arrival altitude details stay within their rows at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await importRoute(page, "issue_129_omaru_point.kml");
    await expect(page.locator(".fixed-altitude-control").first()).toBeVisible();
    await expect(page.locator(".route-table .vrep-row")).toHaveCount(1);
    await assertIntervalRows(page);
    const arrival = page.locator(".arrival-altitude-control");
    await expect(arrival.locator(".arrival-airport-reference")).toBeVisible();
    await page.getByLabel("今回採用する場周経路高度", { exact: true }).fill("50");
    await expect(arrival.locator(".field-error")).toBeVisible();
    await assertIntervalRows(page);
    await page.getByLabel("今回採用する場周経路高度", { exact: true }).fill("1300");
    await expect(page.getByLabel("今回採用する場周経路高度", { exact: true })).toHaveValue("1300");
    await expect(arrival.locator(".arrival-altitude-help")).toBeVisible();
    await assertIntervalRows(page);
    await page.locator(".route-table").screenshot({ path: testInfo.outputPath(`altitude-fixed-arrival-${width}.png`) });
  });
}
