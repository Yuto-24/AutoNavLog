import { expect, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

// Planning inputs now follow a real import, rather than an empty compatibility screen.
export async function enterImportWorkflow(page: Page) {
  const map = page.getByRole("heading", { name: "経路作成", exact: true });
  const date = page.getByLabel("DATE", { exact: true });
  await expect(map.or(date)).toBeVisible({ timeout: 120_000 });
  if (await date.isVisible()) return;
  await page.locator('input[type="file"]').setInputFiles(fileURLToPath(new URL("../../../tests/fixtures/issue_43_golden.kml", import.meta.url)));
  await expect(date).toBeVisible({ timeout: 120_000 });
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await expect(page.locator('input[type="file"]')).toBeEnabled();
}
