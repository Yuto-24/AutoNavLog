import { expect, test, type Page } from "@playwright/test";

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
  await page.locator('input[type="file"]').setInputFiles({ name: "map-interaction.kml", mimeType: "application/vnd.google-earth.kml+xml", buffer: Buffer.from(routeKml) });
  await expect(page.locator('input[type="file"]')).toBeEnabled();
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.getByLabel("気象モード").selectOption("FTD");
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByRole("button", { name: "チェックポイントを追加" })).toBeEnabled();
}

test.beforeEach(async ({ page, context }) => {
  await context.route("**/api/**", route => route.abort());
  await mockAirspace(page);
});

// Each overlay gets the same initial view: one successful drag can move the
// other synthetic overlay outside the viewport through Leaflet inertia.
for (const [geometry, selector] of [
  ["polygons", ".rjfm-training-airspace"],
  ["boundaries", ".rjfm-training-airspace-boundary"],
] as const) {
  test(`KS4 display-only ${geometry} pass hover and drag through to the map`, async ({ page, isMobile }) => {
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
  });
}

test("PCA notice uses converted feet while operational limits, geometry and acquisition feedback remain distinct", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/*.geojson", async route => {
    await pending;
    await route.fallback();
  });
  await importLocalRoute(page);
  const legend = page.getByRole("group", { name: "RJFMガイダンス凡例" });
  await expect(legend).toContainText("空域情報を取得中…");
  release();
  await expect(page.locator(".rjfm-training-airspace")).toHaveCount(12);
  await expect(page.locator(".rjfm-training-airspace-boundary")).toHaveCount(12);
  await expect(legend).not.toContainText("空域情報を取得中…");
  await expect(legend).not.toContainText("区画を表示");
  await expect(legend).not.toContainText("民間訓練試験空域 KS4（GSI）");
  await expect(legend).toContainText("PCA：約656–2,625 ft（200–800 m）");
  const boundary = page.locator(".rjfm-pca-boundary");
  await expect(boundary).toHaveAttribute("stroke", "#a45b13");
  await boundary.dispatchEvent("mouseover");
  await expect(page.getByRole("tooltip").filter({ hasText: "宮崎特別管制区（PCA）" })).toContainText("原告示高度 約656–2,625 ft（200–800 m）");
  await expect(page.getByRole("tooltip").filter({ hasText: "宮崎特別管制区（PCA）" })).toContainText("運用判定 656–2,700 ft MSL（利用者承認値）");
  await boundary.dispatchEvent("mouseout");
  for (const width of [390, 1100, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect(page).toHaveTitle(/NavMate/);
    await expect(page.locator("vite-error-overlay")).toHaveCount(0);
    await expect(page.locator(".brand-subtitle")).not.toContainText(" / Local");
    await expect(page.locator(".rjfm-airspace-credit")).toHaveText("国土交通省・国土地理院データを加工");
    const boxes = await page.evaluate(() => [".input-rail", ".route-workspace", ".status-rail"].map(selector => {
      const r = document.querySelector(selector)!.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height };
    }));
    for (const box of boxes) { expect(box.width).toBeGreaterThan(0); expect(box.height).toBeGreaterThan(0); }
    for (let index = 1; index < boxes.length; index++) {
      if (width <= 1240) expect(boxes[index]!.y).toBeGreaterThanOrEqual(boxes[index - 1]!.y + boxes[index - 1]!.height - 1);
      else expect(boxes[index]!.x).toBeGreaterThanOrEqual(boxes[index - 1]!.x + boxes[index - 1]!.width - 1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await page.locator(".map-frame").screenshot({ path: info.outputPath(`pca-${width}.png`) });
  }
  await page.route("https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/*.geojson", route => route.abort());
  await page.reload();
  await expect(legend).toContainText("空域情報を取得できず非表示");
  await expect(page.locator(".rjfm-training-airspace")).toHaveCount(0);
  await expect(boundary).toHaveCount(1);
  expect(errors).toEqual([]);
});
