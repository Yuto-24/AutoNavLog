import { test, expect, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { backend } from "./helpers/sync-auth";
async function start(page: Page, subject: string) {
  await page.goto("/e2e/auth-harness/index.html?sync");
  await expect.poll(() => page.evaluate(() => Boolean((window as any).authTest))).toBe(true);
  await page.evaluate(subject => (window as any).authTest.signIn(subject), subject);
  await expect.poll(() => page.evaluate(() => (window as any).authTest.state().account?.displayName)).toBe(subject);
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();

}
async function state(page: Page) { return page.evaluate(() => (window as any).authTest.contexts.at(-1).sync.getState()); }
async function create(page: Page, name: string) {
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByLabel("RJFM出発Legの計画高度", { exact: true })).toBeVisible();
  for (const [node, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(`${node}出発Legの計画高度`, { exact: true }).fill(altitude!);
  }
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await page.getByLabel("プロジェクト", { exact: true }).fill(name);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator("#saved-project option").filter({ hasText: name })).toHaveCount(1);
}
async function open(page: Page, name: string) {
  await expect(page.locator("#saved-project option").filter({ hasText: name })).toHaveCount(1);
  await page.getByLabel("保存済み", { exact: true }).selectOption({ label: name });
  await page.getByRole("button", { name: "保存済みProjectを開く" }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
}
async function blockLocalDeletion(page: Page, accountId: string) {
  await page.evaluate(oldId => new Promise<void>((resolve, reject) => {
    const request = indexedDB.open(`autonavlog.projects.${oldId}`);
    request.onsuccess = () => {
      (window as any).blockedAccountDb = request.result;
      request.result.onversionchange = () => {};
      resolve();
    };
    request.onerror = () => reject(request.error);
  }), accountId);
}
test("real SDK: fresh device Last Calculation, offline edit, blocking conflict both, delete Undo and account separation", async ({ browser, page, context }) => {
  await backend(context);
  const subject = `sync-${crypto.randomUUID()}`;
  await start(page, subject); await create(page, "Sync route");
  const other = await browser.newContext({ viewport: page.viewportSize()! }); await backend(other); const ipad = await other.newPage();
  try {
    await start(ipad, subject); await open(ipad, "Sync route");
    await expect(ipad.getByLabel("FUEL gal", { exact: true })).toHaveValue("90");
    await other.setOffline(true);
    await ipad.getByLabel("FUEL gal", { exact: true }).fill("78");
    await expect.poll(async () => ipad.evaluate(async () => {
      const app = (window as any).authTest.contexts.at(-1); return (await app.refreshProjects()).project?.total_usable_fuel_gal;
    })).toBe(78);
    await page.getByLabel("FUEL gal", { exact: true }).fill("76");
    await expect.poll(async () => page.evaluate(async () => {
      const app = (window as any).authTest.contexts.at(-1);
      const rows = await app.repository.rows();
      const row = rows.find((row: any) => row.draft.name === "Sync route");
      return row?.draft.total_usable_fuel_gal === 76 && !row.sync.dirty;
    })).toBe(true);
    await other.setOffline(false);
    await expect(ipad.getByRole("dialog", { name: "他の端末の変更と競合しています" })).toBeVisible({ timeout: 90000 });
    await ipad.keyboard.press("Escape");
    await expect(ipad.getByRole("dialog")).toBeVisible();
    await ipad.getByRole("button", { name: "両方残す", exact: true }).click();
    await expect(ipad.getByRole("dialog")).not.toBeVisible();
    await expect.poll(async () => (await state(ipad)).conflicts.length).toBe(0);
    await expect.poll(() => ipad.locator("#saved-project option").count()).toBe(3);
    await expect(ipad.getByLabel("FUEL gal", { exact: true })).toHaveValue("78");
    await expect(ipad.locator(".nav-log-table")).not.toBeVisible(); // edited fingerprint is stale
    ipad.once("dialog", dialog => dialog.accept());
    await ipad.getByRole("button", { name: "保存済みProjectを削除" }).click();
    await expect(ipad.getByRole("button", { name: "元に戻す", exact: true })).toBeVisible();
    await ipad.getByRole("button", { name: "元に戻す", exact: true }).click();
    await expect.poll(() => ipad.locator("#saved-project option").count()).toBe(3);
    await ipad.evaluate(() => (window as any).authTest.signOut());
    await expect.poll(() => ipad.locator("#saved-project option").count()).toBe(1);
    await start(ipad, `other-${subject}`);
    await expect.poll(() => ipad.locator("#saved-project option").count()).toBe(1);
  } finally { await other.close(); }
});

test("account deletion drains Remote and Local, closes an offline device, and re-registers with a new owner", async ({ browser, page, context }) => {
  await backend(context);
  const subject = `delete-${crypto.randomUUID()}`;
  await start(page, subject); await create(page, "Old route");
  const oldId = await page.evaluate(() => (window as any).authTest.state().account.account_id);
  await expect.poll(async () => page.evaluate(async () => {
    const rows = await (window as any).authTest.contexts.at(-1).repository.rows();
    return rows.some((row: any) => row.draft.name === "Old route" && !row.sync.dirty);
  })).toBe(true);
  const staleContext = await browser.newContext(); await backend(staleContext);
  const stalePage = await staleContext.newPage();
  const otherContext = await browser.newContext(); await backend(otherContext);
  const otherPage = await otherContext.newPage();
  try {
    await start(stalePage, subject); await open(stalePage, "Old route");
    await staleContext.setOffline(true);
    await start(otherPage, `other-${subject}`); await create(otherPage, "Other route");
    await page.getByRole("button", { name: "アカウント", exact: true }).click();
    await page.getByRole("button", { name: "アカウントを削除", exact: true }).click();
    await blockLocalDeletion(page, oldId);
    await blockLocalDeletion(stalePage, oldId);
    await page.getByRole("button", { name: "NavMateアカウントを削除する", exact: true }).click();
    await expect.poll(() => page.evaluate(() => (window as any).authTest.state().deletionPending)).toBe(true);
    await expect(page.getByRole("alert").filter({ hasText: "削除が途中" })).toBeVisible();
    expect(await page.evaluate(subject => localStorage.getItem(`autonavlog.account.current.${subject}`), subject)).toBe(`retired:${oldId}`);
    await expect(stalePage.locator("#saved-project option").filter({ hasText: "Old route" })).toHaveCount(1);
    await staleContext.setOffline(false);
    await expect(stalePage.getByRole("alert").filter({ hasText: "削除が途中" })).toBeVisible();
    await expect.poll(() => stalePage.evaluate(() => (window as any).authTest.state().account)).toBeNull();
    await expect(stalePage.locator("#saved-project option").filter({ hasText: "Old route" })).toHaveCount(0);
    expect(await stalePage.evaluate(subject => localStorage.getItem(`autonavlog.account.current.${subject}`)?.startsWith("retired:"), subject)).toBe(true);
    await stalePage.getByRole("button", { name: "アカウント", exact: true }).click();
    await expect(stalePage.getByRole("button", { name: "削除を再試行", exact: true })).toBeVisible();
    await stalePage.getByRole("button", { name: "削除を再試行", exact: true }).click();
    await expect(stalePage.getByRole("alert").filter({ hasText: "端末内のデータを削除できませんでした" })).toBeVisible();
    await stalePage.getByRole("button", { name: "アカウントを閉じる" }).click();
    await stalePage.evaluate(() => { (window as any).blockedAccountDb.close(); });
    await page.evaluate(() => { (window as any).blockedAccountDb.close(); });
    await page.getByRole("button", { name: "アカウント", exact: true }).click();
    await page.getByRole("button", { name: "削除を再試行", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "NavMateアカウントを削除しました" }).first()).toBeVisible();
    await stalePage.evaluate(() => (window as any).authTest.refresh());
    await expect.poll(async () => stalePage.evaluate(oldId => indexedDB.databases().then(rows => rows.some(row => row.name === `autonavlog.projects.${oldId}`)), oldId)).toBe(false);
    expect(await page.evaluate(oldId => indexedDB.databases().then(rows => rows.some(row => row.name === `autonavlog.projects.${oldId}`)), oldId)).toBe(false);
    await expect(otherPage.locator("#saved-project option").filter({ hasText: "Other route" })).toHaveCount(1);
    const rejectRegistration = /documents:commit\?/;
    await context.route(rejectRegistration, async route => {
      const writes = route.request().postDataJSON()?.writes ?? [];
      if (writes.some((write: { update?: { fields?: { state?: { stringValue?: string } } } }) =>
        write.update?.fields?.state?.stringValue === "ACTIVE")) {
        await route.fulfill({ status: 403, json: { error: { code: 403, status: "PERMISSION_DENIED", message: "injected registration failure" } } });
      } else await route.continue();
    });
    await page.evaluate(([subject, oldId]) => (window as any).authTest.register(subject, oldId), [subject, oldId]);
    expect(await page.evaluate(() => (window as any).authTest.state().account)).toBeNull();
    await context.unroute(rejectRegistration);
    await page.evaluate(subject => (window as any).authTest.register(subject), subject);
    await expect.poll(() => page.evaluate(() => (window as any).authTest.state().account?.account_id)).not.toBeNull();
    const newId = await page.evaluate(() => (window as any).authTest.state().account.account_id);
    expect(newId).not.toBe(oldId);
    await expect(page.locator("#saved-project option").filter({ hasText: "Old route" })).toHaveCount(0);
    await create(page, "New route");
    await expect.poll(async () => page.evaluate(async () => {
      const rows = await (window as any).authTest.contexts.at(-1).repository.rows();
      return rows.some((row: any) => row.draft.name === "New route" && !row.sync.dirty);
    })).toBe(true);
  } finally { await staleContext.close(); await otherContext.close(); }
});

test("offline account deletion is rejected without a Local-only deletion queue", async ({ page, context }) => {
  await backend(context);
  const subject = `offline-delete-${crypto.randomUUID()}`;
  await start(page, subject); await create(page, "Keep route");
  const oldId = await page.evaluate(() => (window as any).authTest.state().account.account_id);
  await page.getByRole("button", { name: "アカウント", exact: true }).click();
  await page.getByRole("button", { name: "アカウントを削除", exact: true }).click();
  await context.setOffline(true);
  await page.getByRole("button", { name: "NavMateアカウントを削除する", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "オンライン接続と再認証" })).toBeVisible();
  expect(await page.evaluate(() => (window as any).authTest.state().account?.account_id)).toBe(oldId);
  await expect(page.locator("#saved-project option").filter({ hasText: "Keep route" })).toHaveCount(1);
  expect(await page.evaluate(oldId => indexedDB.databases().then(rows => rows.some(row => row.name === `autonavlog.projects.${oldId}`)), oldId)).toBe(true);
});

test("anonymous Latest collision is durable and blocks editing until named save", async ({ page, context }) => {
  await backend(context);
  const subject = `import-${crypto.randomUUID()}`;
  await start(page, subject); await create(page, "Account saved");
  // Start an autosave-only Account Latest from the same known valid Project.
  await page.evaluate(async () => {
    const app = (window as any).authTest.contexts.at(-1);
    const listing = await app.repository.list();
    const existing = await app.repository.read(listing.projects.find((r: any) => r.kind === "SAVED").id);
    const id = crypto.randomUUID();
    const copy = await app.repository.cleanCalculation(existing, id);
    copy.checkpoint = null; copy.token = crypto.randomUUID();
    await app.repository.write(copy, null, true);
  });
  await page.evaluate(() => (window as any).authTest.signOut());
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await create(page, "Anonymous saved");
  await page.evaluate(async () => {
    const app = (window as any).authTest.contexts.at(-1);
    const listing = await app.repository.list(), value = await app.repository.read(listing.projects[0].id);
    value.checkpoint = null;
    await app.repository.write(value, value.token, true);
  });
  await page.evaluate(subject => (window as any).authTest.signIn(subject), subject);
  const modal = page.getByRole("dialog", { name: "端末内の作業に名前を付けて保存しますか？" });
  await expect(modal).toBeVisible();
  await page.reload(); await expect(modal).toBeVisible();
  await page.keyboard.press("Escape"); await expect(modal).toBeVisible();
  await modal.getByLabel("Project名", { exact: true }).fill("Imported work");
  await modal.getByRole("button", { name: "名前を付けて保存", exact: true }).click();
  await expect(modal).not.toBeVisible();
  await expect(page.locator("#saved-project option").filter({ hasText: "Imported work" })).toHaveCount(1);
  await expect(page.locator("#saved-project option").filter({ hasText: "Latest" })).toHaveCount(1);
});

for (const operation of ["resolve", "import"] as const) test("post-" + operation + " refresh failure remains visible without Undo", async ({ page }) => {
  await page.goto("/e2e/auth-harness/index.html");
  await page.evaluate(async operation => {
    const modulePath = "/e2e/auth-harness/sync-control.tsx";
    const { mount } = await import(/* @vite-ignore */ modulePath);
    mount(operation);
  }, operation);
  if (operation === "resolve") await page.getByRole("button", { name: "同期先の内容を採用", exact: true }).click();
  else await page.getByRole("button", { name: "破棄", exact: true }).click();
  await expect(page.locator(".account-sync-dialog")).not.toBeVisible();
  await expect(page.getByRole("button", { name: "元に戻す", exact: true })).toHaveCount(0);
  await expect(page.getByRole("alert").filter({ hasText: "Projectの再読み込みに失敗しました" })).toBeVisible();
});
