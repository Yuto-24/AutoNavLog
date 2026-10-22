import { expect, test, type Page } from "@playwright/test";
import { enterImportWorkflow, confirmImportedDraft } from "./helpers/importWorkflow";

const routeKml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>RJFM-RJFO</name>
<LineString><coordinates>131.4486111111,31.8772222222,0 131.5,32.4,0 131.65,33.1,0 131.7,33.4,0 131.7372222222,33.4794444444,0</coordinates></LineString>
</Placemark></Document></kml>`;

// Small deterministic responses obey the production parser's approved tile/name
// contracts. Geometry is synthetic: this test checks interaction, not source data.
async function mockAirspace(page: Page) {
  await page.route("https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/*.geojson", async route => {
    const north = route.request().url().endsWith("/103.geojson");
    const names = north ? ["KS4-1/4", "KS4-1", "KS4-3", "KS4-5"]
      : ["KS4-2", "KS4-7", "KS4-6", "KS4-1/4", "KS4-1", "KS4-3", "KS4-5", "KS4-8"];
    const south = north ? 32.12 : 31.88;
    const ring = [[131.25, south], [131.48, south], [131.48, south + 0.06], [131.25, south + 0.06], [131.25, south]];
    const features = names.flatMap(name => {
      const properties = { "空域名称": name, "下限": "SFC", "上限": "5000FT", "管轄機関": "テスト" };
      return [
        { type: "Feature", properties, geometry: { type: "Polygon", coordinates: [ring] } },
        { type: "Feature", properties, geometry: { type: "LineString", coordinates: ring } },
      ];
    });
    await route.fulfill({ contentType: "application/geo+json", headers: { "Access-Control-Allow-Origin": "*" }, body: JSON.stringify({ type: "FeatureCollection", features }) });
  });
}

async function importLocalRoute(page: Page) {
  await page.goto("/");
  await enterImportWorkflow(page);
  await page.locator('input[type="file"]').setInputFiles({ name: "map-interaction.kml", mimeType: "application/vnd.google-earth.kml+xml", buffer: Buffer.from(routeKml) });
  await confirmImportedDraft(page);
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("ETD JST", { exact: true }).fill("09:00");
  await expect(page.getByRole("button", { name: "チェックポイントを追加" })).toBeEnabled();
}

test.beforeEach(async ({ page, context }) => {
  await context.route("**/api/**", route => route.abort());
  await mockAirspace(page);
});

for (const width of [390, 1100, 1440]) {
  test(`map-picked checkpoint focuses its name once and reveals it at ${width}px`, async ({ page, isMobile }) => {
    test.skip(isMobile && width !== 390, "Mobile focus is covered at the device-sized layout");
    await page.setViewportSize({ width, height: 900 });
    await importLocalRoute(page);
    const route = page.getByRole("region", { name: "経路地図とLeg設定" });
    const editor = route.getByRole("region", { name: "チェックポイント設定" });
    // Required-field validation contributes to the label's accessible name.
    const name = editor.getByRole("textbox", { name: /^名称(?:\s|$)/ });
    const map = route.locator(".route-map");
    await editor.getByRole("button", { name: "チェックポイントを追加" }).click();
    // Merely opening an editor must not steal focus from its initiating control.
    await expect(name).not.toBeFocused();
    for (const suffix of ["first", "second"]) {
      if (suffix === "second") {
        await editor.getByRole("button", { name: "地図から座標を選択" }).click();
      }
      await map.click({ position: { x: 150, y: 150 } });
      await expect(name).toBeFocused();
      await expect(name).toBeInViewport({ ratio: 1 });
      await expect(editor.getByLabel("緯度")).not.toHaveValue("");
      await expect(editor.getByLabel("経度")).not.toHaveValue("");
      await expect(route.locator("#route-map-frame")).not.toHaveClass(/is-picking-checkpoint/);
      // No locator.fill/click: input must continue from the actual MAP click focus.
      await page.keyboard.type(suffix);
      await expect(name).toHaveValue(suffix === "first" ? "first" : "firstsecond");
      await page.keyboard.press("Tab");
      await expect(name).not.toBeFocused();
      const latitude = editor.getByLabel("緯度");
      await latitude.focus();
      if (suffix === "second") await latitude.fill("32.482176");
      await expect(latitude).toBeFocused();
      await expect(name).not.toBeFocused();
      const placement = await route.evaluate(element => ({
        mapBottom: element.querySelector("#route-map-frame")!.getBoundingClientRect().bottom,
        editorTop: element.querySelector(".checkpoint-editor")!.getBoundingClientRect().top,
        editorBottom: element.querySelector(".checkpoint-editor")!.getBoundingClientRect().bottom,
        tableTop: element.querySelector(".route-table-scroll")!.getBoundingClientRect().top,
      }));
      expect(placement.editorTop).toBeGreaterThanOrEqual(placement.mapBottom);
      expect(placement.tableTop).toBeGreaterThanOrEqual(placement.editorBottom);
    }
    await editor.getByRole("button", { name: "キャンセル", exact: true }).click();
    await expect(name).toHaveCount(0);
    await expect(route.locator(".checkpoint-draft-marker")).toHaveCount(0);
    await editor.getByRole("button", { name: "チェックポイントを追加" }).click();
    await map.click({ position: { x: 150, y: 150 } });
    await expect(name).toBeFocused();
    await page.keyboard.type("new");
    await expect(name).toHaveValue("new");
  });
}

test("KS4 display-only polygons and boundaries pass hover and drag through to the map", async ({ page, isMobile }) => {
  test.skip(isMobile, "Mouse hover/pan is covered in both desktop engines");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await importLocalRoute(page);
  await expect(page.locator(".rjfm-training-airspace")).toHaveCount(12);
  await expect(page.locator(".rjfm-training-airspace-boundary")).toHaveCount(12);
  await expect(page.locator(".rjfm-training-airspace").first()).toHaveAttribute("stroke", "none");
  await expect(page.locator(".rjfm-training-airspace-boundary").first()).toHaveAttribute("stroke", "#315caa");
  const trainingPaths = page.locator(".rjfm-training-airspace, .rjfm-training-airspace-boundary");
  expect(await trainingPaths.evaluateAll(paths => paths.every(path =>
    !path.classList.contains("leaflet-interactive") && getComputedStyle(path).pointerEvents === "none",
  ))).toBe(true);
  await expect(page.locator(".rjfm-pca-boundary")).toHaveClass(/leaflet-interactive/);
  await expect(page.locator(".rjfm-pca-exclusion")).toHaveClass(/leaflet-interactive/);

  const map = page.locator(".route-map");
  await map.scrollIntoViewIfNeeded();
  for (const selector of [".rjfm-training-airspace", ".rjfm-training-airspace-boundary"]) {
    // Use real SVG geometry, including clipping, rather than dragging an arbitrary map point.
    const point = await map.evaluate((element, selector) => {
      const bounds = element.getBoundingClientRect();
      for (const path of element.querySelectorAll<SVGPathElement>(selector)) {
        const matrix = path.getScreenCTM();
        if (!matrix) continue;
        if (selector.endsWith("-boundary")) {
          // A coarse screen grid can miss a thin stroke after a fractional pan.
          // Sample the actual path centerline, still checking stroke and clipping.
          const length = path.getTotalLength();
          for (let distance = 0; distance <= length; distance += 1) {
            const local = path.getPointAtLength(distance);
            const point = local.matrixTransform(matrix);
            if (point.x < bounds.left + 50 || point.x >= bounds.right - 60
              || point.y < bounds.top + 50 || point.y >= bounds.bottom - 60) continue;
            if (path.isPointInStroke(local)
              && !document.elementFromPoint(point.x, point.y)?.closest(".leaflet-control, .leaflet-marker-icon")) {
              return { x: point.x, y: point.y };
            }
          }
          continue;
        }
        for (let x = bounds.left + 50; x < bounds.right - 60; x += 3) {
          for (let y = bounds.top + 50; y < bounds.bottom - 60; y += 3) {
            const local = new DOMPoint(x, y).matrixTransform(matrix.inverse());
            const inside = path.isPointInFill(local);
            if (inside && !document.elementFromPoint(x, y)?.closest(".leaflet-control, .leaflet-marker-icon")) {
              return { x, y };
            }
          }
        }
      }
      return null;
    }, selector);
    expect(point, `visible geometry for ${selector}`).not.toBeNull();
    await page.mouse.move(point!.x, point!.y);
    await expect(page.locator(".leaflet-tooltip").filter({ hasText: /KS4-/ })).toHaveCount(0);
    const pane = map.locator(".leaflet-map-pane");
    const before = await pane.getAttribute("style");
    await page.mouse.down();
    await page.mouse.move(point!.x + 35, point!.y + 25, { steps: 6 });
    await page.mouse.up();
    await expect.poll(() => pane.getAttribute("style")).not.toBe(before);
    await expect(pane).not.toHaveClass(/leaflet-pan-anim/);
    expect(await page.evaluate(() => window.getSelection()?.toString() ?? "")).toBe("");
    await expect(trainingPaths).toHaveCount(24);
  }

});
