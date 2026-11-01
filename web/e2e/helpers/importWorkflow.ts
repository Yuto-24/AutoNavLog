import { expect, type Page } from "@playwright/test";

// Existing KML-based regression scenarios explicitly opt into the compatibility input.
export async function enterImportWorkflow(page: Page) {
  const start = page.getByRole("button", { name: "KML/KMZから開始", exact: true });
  const date = page.getByLabel("DATE", { exact: true });
  const legacyNotice = page.getByRole("button", { name: "今回は旧画面を使う", exact: true });
  await expect(start.or(date).or(legacyNotice).first()).toBeVisible({ timeout: 120_000 });
  if (await legacyNotice.isVisible()) await legacyNotice.click();
  await expect(start.or(date)).toBeVisible({ timeout: 120_000 });
  if (await start.isVisible()) await start.click();
}
