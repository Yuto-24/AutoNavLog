import { expect, test, type Page } from "@playwright/test";

test.use({ actionTimeout: 15_000 });

const strip = (page: Page) => page.getByRole("list", { name: "経路リスト" });
const airport = (page: Page) => page.locator('.airport-draft-marker[title^="空港 RJFM "]');
const vrep = (page: Page) => page.locator('.vrep-draft-marker[title^="VREP HITOTSUBA "]');
const snapshot = (page: Page) => page.evaluate(() => JSON.parse(sessionStorage.getItem("autonavlog.working-session.v1")!));

for (const width of [1100, 1440]) {
  test(`VREP mixed clusters, repeated selections, arbitrary point and saved snapshots at ${width}px`, async ({ page }, info) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width, height: 1100 });
    await page.addInitScript(() => {
      if (!localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last")) {
        localStorage.setItem("autonavlog.map-viewport.v1.anonymous.last", JSON.stringify({ latitude: 31.9, longitude: 131.44, zoom: 9 }));
      }
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "経路作成", exact: true })).toBeVisible({ timeout: 90_000 });
    // A shared cluster contains an airport AND VREP. Its click is only navigation.
    const mixed = page.locator('.candidate-cluster').filter({ has: page.locator('[data-candidates~="airport:RJFM"][data-candidates~="RJFM-VREP-HITOTSUBA"]') });
    await expect(mixed).toHaveCount(1);
    await mixed.click();
    await expect(strip(page)).toHaveCount(0);
    await expect(vrep(page)).toBeVisible();
    const vrepSymbol = () => vrep(page).locator(".map-candidate-symbol");
    const airportSymbol = () => airport(page).locator(".map-candidate-symbol");
    await expect(vrepSymbol()).toHaveCSS("width", "16px");
    await expect(vrepSymbol()).toHaveCSS("height", "16px");
    await expect(airportSymbol()).toHaveCSS("width", "28px");
    await expect(airportSymbol()).toHaveCSS("height", "28px");
    await expect(vrepSymbol()).toHaveCSS("background-color", "rgb(255, 255, 255)");
    await expect(airportSymbol()).toHaveCSS("background-color", "rgb(255, 255, 255)");
    await vrep(page).click();
    await expect(strip(page)).toHaveCount(0); // VREP cannot be FROM.
    await airport(page).click();
    await expect(airportSymbol()).toHaveCSS("background-color", "rgb(23, 59, 94)");
    await vrep(page).click();
    // The transparent target outside the visible 16 px symbol still selects it.
    const hitArea = (await vrep(page).boundingBox())!;
    expect(hitArea.width).toBe(28);
    await page.mouse.click(hitArea.x + 2, hitArea.y + hitArea.height / 2);
    await expect(strip(page).locator("li")).toHaveCount(3);
    await expect(vrepSymbol()).toHaveCSS("background-color", "rgb(12, 124, 120)");
    await expect(vrep(page).locator(".vrep-inner-ring")).toHaveCSS("border-top-color", "rgb(255, 255, 255)");
    // Removing one occurrence keeps the symbol filled; removing the last resets it.
    await strip(page).locator("li").last().getByRole("button").click();
    await expect(vrepSymbol()).toHaveCSS("background-color", "rgb(12, 124, 120)");
    await strip(page).locator("li").last().getByRole("button").click();
    await expect(vrepSymbol()).toHaveCSS("background-color", "rgb(255, 255, 255)");
    await vrep(page).click();
    await vrep(page).click();
    await expect(page.getByRole("button", { name: "経路を確定", exact: true })).toBeDisabled();
    const marker = (await vrep(page).boundingBox())!;
    await page.mouse.click(marker.x + marker.width / 2 + 22, marker.y + marker.height / 2 + 22);
    await expect(strip(page).locator("li")).toHaveCount(4);
    await expect(strip(page)).toContainText("WP1");
    // Background clicks near a registered point remain exact arbitrary coordinates.
    await expect.poll(async () => (await snapshot(page)).mapRouteDraft.length).toBe(4);
    const draft = (await snapshot(page)).mapRouteDraft;
    expect(draft[3].reference_snapshot).toBeUndefined();
    expect(draft[3].latitude_deg).not.toBe(draft[1].latitude_deg);
    expect(draft[1].reference_snapshot).toEqual(draft[2].reference_snapshot);
    expect(draft[1].id).not.toBe(draft[2].id);
    // Selected airport and VREP stay individual even when unselected points cluster.
    for (let i = 0; i < 3; i++) await page.getByRole("button", { name: "Zoom out", exact: true }).click();
    await expect(vrep(page)).toHaveCount(1);
    await expect(airport(page)).toHaveCount(1);
    await expect(page.locator('[data-candidates~="RJFM-VREP-HITOTSUBA"]')).toHaveCount(0);
    for (let i = 0; i < 3; i++) await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await vrep(page).click();
    await expect(strip(page).locator("li")).toHaveCount(5);
    await page.reload();
    await expect(strip(page).locator("li")).toHaveCount(5, { timeout: 90_000 });
    await expect(vrepSymbol()).toHaveCSS("background-color", "rgb(12, 124, 120)");
    await expect(airportSymbol()).toHaveCSS("background-color", "rgb(23, 59, 94)");
    await airport(page).click();
    await expect(strip(page).locator("li")).toHaveCount(6);
    await page.locator(".route-workspace").screenshot({ path: info.outputPath(`vrep-draft-${width}.png`) });
    await page.getByRole("button", { name: "経路を確定", exact: true }).click();
    await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
    await expect(page.locator(".candidate-cluster, .vrep-draft-marker, .airport-draft-marker")).toHaveCount(0);
    const input = (await page.locator(".input-rail").boundingBox())!;
    const route = (await page.locator(".route-workspace").boundingBox())!;
    const readiness = (await page.locator(".status-rail").boundingBox())!;
    if (width <= 1240) {
      expect(input.y + input.height).toBeLessThanOrEqual(route.y + 2);
      expect(route.y + route.height).toBeLessThanOrEqual(readiness.y + 2);
    } else {
      expect(input.x + input.width).toBeLessThanOrEqual(route.x + 2);
      expect(route.x + route.width).toBeLessThanOrEqual(readiness.x + 2);
    }
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "プロジェクトを保存しました" })).toBeVisible();
    await expect.poll(async () => (await snapshot(page)).working.project.route_nodes.length).toBe(6);
    const project = (await snapshot(page)).working.project;
    const selected = project.route_nodes.filter((node: any) => node.reference_snapshot);
    expect(selected).toHaveLength(3);
    expect(new Set(selected.map((node: any) => node.id)).size).toBe(3);
    expect(selected.every((node: any) => JSON.stringify(node.reference_snapshot) === JSON.stringify(selected[0].reference_snapshot))).toBe(true);
    expect(selected[0].reference_snapshot.source.url).toContain("6415872164");
    expect(selected[0].reference_snapshot.derivation).toContain("WGS84");
    const savedId = project.id;
    await page.reload();
    await expect(page.getByLabel("DATE", { exact: true })).toBeVisible({ timeout: 90_000 });
    expect((await snapshot(page)).working.project.route_nodes).toEqual(project.route_nodes);
    page.once("dialog", dialog => dialog.accept());
    await page.getByRole("button", { name: "新規", exact: true }).click();
    await expect(page.getByRole("heading", { name: "経路作成", exact: true })).toBeVisible();
    await expect(page.locator(".airport-draft-marker.is-selected, .vrep-draft-marker.is-selected")).toHaveCount(0);
    await page.getByLabel("保存済み", { exact: true }).selectOption(savedId);
    await page.getByRole("button", { name: "保存済みプロジェクトを開く", exact: true }).click();
    await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
    await expect(page.locator(".candidate-cluster, .vrep-draft-marker")).toHaveCount(0);
    expect((await snapshot(page)).working.project.route_nodes).toEqual(project.route_nodes);
    await page.screenshot({ path: info.outputPath(`vrep-${width}.png`), fullPage: true });
  });
}
