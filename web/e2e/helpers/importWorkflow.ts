import { expect, type Page } from "@playwright/test";

// Existing KML-based regression scenarios explicitly opt into the compatibility input.
export async function enterImportWorkflow(page: Page) {
  const start = page.getByRole("button", { name: "KML/KMZから開始", exact: true });
  const date = page.getByLabel("DATE", { exact: true });
  await expect(start.or(date)).toBeVisible({ timeout: 120_000 });
  if (await start.isVisible()) await start.click();
}
