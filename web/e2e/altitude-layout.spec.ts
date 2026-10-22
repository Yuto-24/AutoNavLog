import { expect, test, type Locator, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { enterImportWorkflow } from "./helpers/importWorkflow";

async function importRoute(page: Page, fixture: string) {
  await page.goto("/");
  await enterImportWorkflow(page);
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByLabel("地上風速 kt").fill("15");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("270");
  await page.getByLabel("5,000 ft風速 kt").fill("30");
  await page.locator('input[type="file"]').setInputFiles(resolve(`../tests/fixtures/${fixture}`));
  await expect(page.getByLabel("飛行経路候補")).not.toHaveValue("");
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.locator(".route-table .destination-row")).toBeVisible();
}

async function assertCenteredRows(page: Page) {
  const rows = page.locator(".route-table tbody tr");
  expect(await rows.count()).toBeGreaterThan(2);
  for (const row of await rows.all()) {
    const geometry = await row.evaluate(element => {
      const rect = element.getBoundingClientRect();
      const read = (selector: string) => {
        const node = element.querySelector(selector);
        if (!node) return null;
        const box = node.getBoundingClientRect();
        return { x: box.x, y: box.y, right: box.right, bottom: box.bottom, center: box.y + box.height / 2 };
      };
      return {
        center: rect.y + rect.height / 2, top: rect.top, bottom: rect.bottom,
        point: read(".route-point-content"), phase: read(".route-phase-primary"),
        input: read(".table-number-input"), candidate: read(".altitude-candidate-select"),
        detail: read(".altitude-course"), fixed: read(".fixed-altitude-control"),
        descent: read(".descent-rate-result"), arrival: read(".arrival-altitude-control"),
      };
    });
    expect(geometry.point).not.toBeNull();
    expect(geometry.phase).not.toBeNull();
    for (const primary of [geometry.point, geometry.phase, geometry.input, geometry.candidate]) {
      if (primary) expect(Math.abs(primary.center - geometry.center)).toBeLessThanOrEqual(1);
    }
    if (geometry.input && geometry.candidate) {
      expect(geometry.candidate.right).toBeLessThanOrEqual(geometry.input.x);
    }
    if (geometry.detail && geometry.input) {
      expect(geometry.detail.y).toBeGreaterThanOrEqual(geometry.input.bottom);
    }
    if (geometry.descent && geometry.phase) {
      expect(geometry.descent.y).toBeGreaterThanOrEqual(geometry.phase.bottom);
    }
    for (const content of [geometry.detail, geometry.fixed, geometry.arrival, geometry.descent]) {
      if (content) {
        expect(content.y).toBeGreaterThanOrEqual(geometry.top);
        expect(content.bottom).toBeLessThanOrEqual(geometry.bottom);
      }
    }
  }
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

for (const width of [1100, 1440]) {
  test(`altitude controls stay centered with warnings and calculated descent at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await importRoute(page, "issue_43_golden.kml");
    await assertCenteredRows(page);
    const candidateRow = page.locator(".route-table tbody tr").filter({ has: page.locator(".altitude-candidate-select") }).first();
    await candidateRow.locator(".table-number-input").fill("4400");
    await expect(candidateRow).toHaveClass(/altitude-warning-row/);
    await expect(candidateRow.locator(".altitude-course")).toContainText("候補外（警告）");
    await assertCenteredRows(page);
    await selectFirstCandidate(candidateRow);
    await expect(candidateRow).not.toHaveClass(/altitude-warning-row/);
    await assertCenteredRows(page);

    for (const [name, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
      await page.getByLabel(`${name}出発Legの計画高度`, { exact: true }).fill(altitude);
    }
    await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
    await expect(page.locator(".nav-log-table")).toBeVisible();
    await expect(page.locator(".descent-rate-result")).toBeVisible();
    await assertCenteredRows(page);
    await assertWorkflowOrder(page, width);
    await page.locator(".route-table").screenshot({ path: testInfo.outputPath(`altitude-centered-${width}.png`) });
  });

  test(`fixed and arrival altitude details stay within their rows at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    await importRoute(page, "issue_129_omaru_point.kml");
    await expect(page.locator(".fixed-altitude-control").first()).toBeVisible();
    await expect(page.locator(".route-table .vrep-row")).toHaveCount(1);
    await assertCenteredRows(page);
    const arrival = page.locator(".arrival-altitude-control");
    await expect(arrival.locator(".arrival-airport-reference")).toBeVisible();
    await page.getByLabel("今回採用する場周経路高度", { exact: true }).fill("1300");
    await expect(page.getByLabel("今回採用する場周経路高度", { exact: true })).toHaveValue("1300");
    await expect(arrival.locator(".arrival-altitude-help")).toBeVisible();
    await assertCenteredRows(page);
    await page.locator(".route-table").screenshot({ path: testInfo.outputPath(`altitude-fixed-arrival-${width}.png`) });
  });
}
