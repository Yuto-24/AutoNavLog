import { expect, type Page } from "@playwright/test";

// KML and MAP now share the new-route draft, before Planning inputs exist.
export async function enterImportWorkflow(page: Page) {
  await expect(page.getByRole("heading", { name: "経路作成", exact: true })
    .or(page.getByLabel("DATE", { exact: true }))).toBeVisible({ timeout: 120_000 });
}

export async function expectImportedDraft(page: Page, candidate = "line:0") {
  const choice = page.getByRole("dialog", { name: "経路選択", exact: true });
  const strip = page.getByRole("list", { name: "経路リスト", exact: true });
  await expect(choice.or(strip)).toBeVisible();
  if (await choice.isVisible()) {
    await choice.getByRole("combobox").selectOption(candidate);
    await choice.getByRole("button", { name: "KMLを取り込む", exact: true }).click();
  }
  await expect(strip).toBeVisible();
  await expect(page.getByLabel("DATE", { exact: true })).toHaveCount(0);
}

export async function confirmImportedDraft(page: Page) {
  await expectImportedDraft(page);
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
}

// Imported Draft names are generated; retain every other Golden output field.
export const importedMapNames = (value: unknown) => JSON.parse(JSON.stringify(value)
  .replaceAll("米ノ津", "WP1").replaceAll("玉名", "WP2").replaceAll("大牟田", "WP3"));
