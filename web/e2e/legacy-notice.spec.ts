import { test, expect } from "@playwright/test";

const path = "/e2e/legacy-notice-harness/index.html";
for (const width of [390, 1100, 1440]) {
  test(`startup notice covers viewport and dismissal lasts only this opening at ${width}px`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 800 });
    await context.route("**/api/account-link/config", route => route.fulfill({ status: 404, json: {} }));
    await context.route("**/api/account-link", route => route.fulfill({ json: { state: "UNLINKED", completed: 0, total: 0, navmateUrl: "/navmate" } }));
    await page.goto(path);
    const notice = page.getByRole("dialog", { name: "NavMateへの移行", exact: true });
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("旧画面は2026年12月を目途に提供終了を予定しています（確定日は未定です）。");
    await expect(notice).toContainText("紐付けたGoogleアカウントでNavMateに初めてログインすると");
    await expect(notice.getByRole("alert")).toHaveText("アカウント設定情報を取得できませんでした。時間をおいて再読み込みしてください。");
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
    await expect(second.getByRole("dialog", { name: "NavMateへの移行", exact: true })).toBeVisible();
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

test("a hanging link operation can be dismissed and is aborted without reopening the notice", async ({ page }) => {
  // Replace only link preparation for this UI cancellation scenario, not the dialog,
  // boundary, focus trap or status polling. Live Google popup consent is separate QA.
  await page.route("**/src/legacyMigration.ts", async route => {
    const response = await route.fetch();
    const source = await response.text();
    const start = source.indexOf("async function prepareLegacyLink()");
    const end = source.indexOf("export function legacyLinkStatus()", start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    await route.fulfill({ response, body: source.slice(0, start) + `async function prepareLegacyLink() {
      return async (signal) => {
        window.__noticeLinkStarted = true;
        await new Promise(resolve => signal.addEventListener('abort', () => {
          window.__noticeLinkAborted = true;
          resolve();
        }, { once: true }));
      };
    }\n` + source.slice(end) });
  });
  await page.route("**/api/account-link", route => route.fulfill({ json: { state: "UNLINKED", completed: 0, total: 0, navmateUrl: "/navmate" } }));
  await page.goto(path);
  await page.getByRole("button", { name: "Googleアカウントを紐付ける" }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__noticeLinkStarted)).toBe(true);
  await page.getByRole("button", { name: "今回は旧画面を使う" }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__noticeLinkAborted)).toBe(true);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await page.getByText("Legacy workspace").evaluate(el => !!el.closest("[inert]"))).toBe(false);
});
