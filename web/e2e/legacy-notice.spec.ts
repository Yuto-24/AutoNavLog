import { test, expect } from "@playwright/test";

const path = "/e2e/legacy-notice-harness/index.html";
for (const width of [390, 1100, 1440]) {
  test(`startup notice covers viewport and dismissal lasts only this opening at ${width}px`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 800 });
    await context.route("**/api/account-link/config", route => route.fulfill({ status: 404, json: {} }));
    await context.route("**/api/account-link", route => route.fulfill({ json: { state: "UNLINKED", completed: 0, total: 0, navmateUrl: "/navmate" } }));
    await page.goto(path);
    const notice = page.getByRole("dialog", { name: "NavMate", exact: true });
    await expect(notice).toBeVisible();
    expect(await page.locator(".legacy-migration-notice").boundingBox()).toEqual({ x: 0, y: 0, width, height: 800 });
    expect(await page.getByText("Legacy workspace").evaluate(el => !!el.closest("[inert]"))).toBe(true);
    await expect(notice.getByRole("link")).toHaveCount(0);
    await expect(notice.getByRole("button", { name: "Googleアカウントを紐付ける" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(notice).toBeVisible();
    await page.keyboard.press("Tab");
    expect(await notice.evaluate(el => el.contains(document.activeElement))).toBe(true);
    await notice.getByRole("button", { name: "今回は旧画面を使う" }).click();
    await expect(notice).toHaveCount(0);
    expect(await page.getByText("Legacy workspace").evaluate(el => !!el.closest("[inert]"))).toBe(false);
    await page.getByRole("button", { name: "アカウント", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "アカウント", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.reload();
    await expect(notice).toBeVisible();
    const second = await context.newPage();
    await second.goto(path);
    await expect(second.getByRole("dialog", { name: "NavMate", exact: true })).toBeVisible();
  });
}

test("linked accounts get the configured destination, while migration takes precedence", async ({ page }) => {
  let state = "LINKED";
  await page.route("**/api/account-link/config", route => route.fulfill({ status: 404, json: {} }));
  await page.route("**/api/account-link", route => route.fulfill({ json: { state, completed: 1, total: 2, navmateUrl: "/configured-navmate" } }));
  await page.goto(path);
  await expect(page.getByRole("link", { name: "NavMateを開く" })).toHaveAttribute("href", "/configured-navmate");
  await expect(page.getByRole("button", { name: "Googleアカウントを紐付ける" })).toHaveCount(0);
  state = "MIGRATING";
  await expect(page.getByRole("status", { name: "NavMateへ引継ぎ中" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await page.getByText("Legacy workspace").evaluate(el => !!el.closest("[inert]"))).toBe(true);
});
