import { expect, type Page } from "@playwright/test";

export const mapZoom = (page: Page) => page.evaluate(() => JSON.parse(localStorage.getItem("autonavlog.map-viewport.v1.anonymous.last")!).zoom as number);

export async function zoomMapTo(page: Page, target: number) {
  for (let attempt = 0; attempt < 20; attempt++) {
    const zoom = await mapZoom(page);
    if (zoom === target) return;
    await page.getByRole("button", { name: zoom > target ? "Zoom out" : "Zoom in", exact: true }).click();
    await expect.poll(() => mapZoom(page)).not.toBe(zoom);
  }
  throw new Error(`Map did not reach zoom ${target}`);
}

export async function revealAirport(page: Page, icao: string) {
  const marker = page.locator(`.airport-draft-marker[title^="空港 ${icao} "]`);
  const cluster = page.locator(".candidate-cluster").filter({ has: page.locator(`[data-candidates~="airport:${icao}"]`) });
  for (let attempt = 0; attempt < 20; attempt++) {
    await expect(page.locator(".route-map")).not.toHaveClass(/leaflet-zoom-anim/);
    const map = (await page.locator(".route-map").boundingBox())!;
    const target = await marker.count() ? marker : cluster;
    const box = await target.boundingBox();
    const zoom = await mapZoom(page);
    if (box && box.x >= map.x && box.y >= map.y && box.x + box.width <= map.x + map.width && box.y + box.height <= map.y + map.height) {
      if (target === marker) return marker;
      // Zoom completion replaces cluster DOM; retry lookup if the prior render disappears.
      try { await cluster.click({ timeout: 1500 }); }
      catch { continue; }
    } else {
      await page.getByRole("button", { name: "Zoom out", exact: true }).click();
    }
    await expect.poll(() => mapZoom(page)).not.toBe(zoom);
  }
  throw new Error(`Could not reveal airport ${icao}`);
}

export async function selectAirport(page: Page, icao: string) {
  await (await revealAirport(page, icao)).click();
}
