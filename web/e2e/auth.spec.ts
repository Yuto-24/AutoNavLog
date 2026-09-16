import { expect, test, chromium, type BrowserContext, type Page } from "@playwright/test";
import { resolve, join } from "node:path";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";

const pageErrors: string[] = [];
test.beforeEach(async ({ page, context }) => {
  pageErrors.length = 0;
  const observe = (page: Page) => page.on("pageerror", error => pageErrors.push(error.message));
  observe(page); context.on("page", observe);
});
test.afterEach(() => { expect(pageErrors).toEqual([]); });

const path = "/e2e/auth-harness/index.html";
const authState = (page: Page) => page.evaluate(() => ((window as any).authTest?.state() ?? {}));
const signIn = async (page: Page, subject: string) => {
  await page.evaluate(subject => (window as any).authTest.signIn(subject), subject);
  await expect.poll(async () => (await authState(page)).account?.displayName).toBe(subject);
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
};
const start = async (page: Page) => {
  await page.goto(path);
  await expect.poll(() => page.evaluate(() => Boolean((window as any).authTest))).toBe(true);
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
};
function jwt(subject: string) {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  return `${part({ alg: "RS256" })}.${part({ sub: `firebase-${subject}`, iat: now, exp: now + 3600, auth_time: now, firebase: { sign_in_provider: "google.com" } })}.signature`;
}
async function backend(context: BrowserContext) {
  const state = { failure: "", lookupCount: 0 };
  await context.route("**/api/**", route => route.abort());
  await context.route(/https:\/\/(identitytoolkit|securetoken|www)\.googleapis\.com\//, async route => {
    if (state.failure === "offline") { await route.abort("internetdisconnected"); return; }
    if (state.failure) {
      await route.fulfill({ status: state.failure === "INTERNAL_ERROR" ? 503 : 400, json: { error: { message: state.failure } } });
      return;
    }
    const request = route.request();
    const body = request.postDataJSON();
    if (request.url().includes("accounts:signInWithIdp")) {
      const subject = new URLSearchParams(body.postBody).get("id_token")!;
      await route.fulfill({ json: { localId: `firebase-${subject}`, displayName: subject, email: `${subject}@example.test`,
        providerId: "google.com", federatedId: subject, idToken: jwt(subject), refreshToken: `refresh-${subject}`, expiresIn: "3600", rawUserInfo: JSON.stringify({ sub: subject }) } });
    } else if (request.url().includes("accounts:lookup")) {
      state.lookupCount++;
      const claims = JSON.parse(Buffer.from(body.idToken.split(".")[1], "base64url").toString());
      const subject = claims.sub.replace("firebase-", "");
      await route.fulfill({ json: { users: [{ localId: claims.sub, displayName: subject, email: `${subject}@example.test`, emailVerified: true,
        providerUserInfo: [{ providerId: "google.com", rawId: subject, displayName: subject, email: `${subject}@example.test` }] }] } });
    } else if (request.url().includes("/token")) {
      const subject = new URLSearchParams(request.postData()!).get("refresh_token")!.replace("refresh-", "");
      await route.fulfill({ json: { user_id: `firebase-${subject}`, id_token: jwt(subject), access_token: jwt(subject), refresh_token: `refresh-${subject}`, expires_in: "3600" } });
    } else await route.fulfill({ json: { authorizedDomains: ["127.0.0.1", "localhost"] } });
  });
  return state;
}
async function saveRoute(page: Page, name: string) {
  await page.getByLabel("DATE", { exact: true }).fill("2026-09-11");
  await page.locator('input[type="file"]').setInputFiles(resolve("../tests/fixtures/issue_43_golden.kml"));
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定", exact: true }).click();
  await expect(page.getByLabel("RJFM出発Legの計画高度", { exact: true })).toBeVisible();
  for (const [node, altitude] of [["RJFM", "6500"], ["米ノ津", "7500"], ["玉名", "6500"]]) {
    await page.getByLabel(`${node}出発Legの計画高度`, { exact: true }).fill(altitude);
  }
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect(page.locator(".nav-log-table")).toBeVisible();
  await page.getByLabel("プロジェクト", { exact: true }).fill(name);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator("#saved-project option").filter({ hasText: name })).toHaveCount(1);
}
async function accountRecords(page: Page) {
  return page.evaluate(async () => {
    const id = (window as any).authTest.state().account.account_id;
    return new Promise<any[]>((resolve, reject) => {
      const open = indexedDB.open(`autonavlog.projects.${id}`, 1);
      open.onsuccess = () => {
        const tx = open.result.transaction("projects"); const read = tx.objectStore("projects").getAll();
        tx.oncomplete = () => { open.result.close(); resolve(read.result); };
        tx.onabort = () => reject(tx.error);
      };
    });
  });
}
const savedNames = (page: Page) => page.locator("#saved-project option").allTextContents();

// Real Firebase SDK REST exchanges are intercepted; no real Google credentials or remote Projects.
test("anonymous workflow, account switch, logout, reauthentication and cross-tab closure", async ({ page, context }) => {
  await backend(context); await start(page);
  await saveRoute(page, "Anonymous route");
  await signIn(page, "A");
  expect(await savedNames(page)).not.toContain("Anonymous route");
  await saveRoute(page, "Account A route");
  const last = (await accountRecords(page))[0].lastCalculation;
  expect(last.outcome).toBeTruthy();
  await page.getByLabel("FUEL gal", { exact: true }).fill("77");
  await expect.poll(async () => (await accountRecords(page))[0].draft.total_usable_fuel_gal).toBe(77);
  await page.reload();
  await expect(page.getByLabel("FUEL gal", { exact: true })).toHaveValue("77");
  const other = await context.newPage(); await start(other);
  await expect(other.getByLabel("プロジェクト", { exact: true })).toBeDisabled();
  expect(await savedNames(other)).toContain("Account A route");
  await page.evaluate(() => (window as any).authTest.signOut());
  await expect.poll(async () => (await authState(other)).account).toBeNull();
  await expect(page.getByLabel("プロジェクト", { exact: true })).toBeDisabled();
  expect(await savedNames(page)).toContain("Anonymous route");
  expect(await savedNames(page)).not.toContain("Account A route");
  await page.reload(); await expect.poll(async () => (await authState(page)).account).toBeNull();
  await signIn(page, "B");
  expect(await savedNames(page)).not.toContain("Account A route");
  expect(await savedNames(page)).not.toContain("Anonymous route");
  await signIn(page, "A");
  expect(await savedNames(page)).toContain("Account A route");
  await expect(page.getByLabel("プロジェクト", { exact: true })).toBeDisabled();
  await page.getByLabel("保存済み", { exact: true }).selectOption({ label: "Account A route" });
  await page.getByRole("button", { name: "保存済みProjectを開く" }).click();
  await expect(page.getByLabel("FUEL gal", { exact: true })).toHaveValue("77");
  await expect(page.locator(".nav-log-table")).toBeVisible();
  expect((await accountRecords(page))[0].lastCalculation).toEqual(last);
});

test("cached authentication survives offline/provider failure at startup, terminal revocation hides retained copies", async ({ page, context }) => {
  const service = await backend(context); await start(page); await signIn(page, "A"); await saveRoute(page, "Retained A");
  const accountId = (await authState(page)).account.account_id;
  for (const failure of ["offline", "INTERNAL_ERROR", "TOO_MANY_ATTEMPTS_TRY_LATER"]) {
    service.failure = failure;
    await page.reload();
    await expect.poll(async () => (await authState(page)).account?.account_id).toBe(accountId);
    await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
    expect(await savedNames(page)).toContain("Retained A");
    await page.evaluate(() => (window as any).authTest.refresh());
    expect((await authState(page)).account.account_id).toBe(accountId);
  }
  service.failure = "TOKEN_EXPIRED";
  await page.evaluate(() => (window as any).authTest.refresh());
  await expect.poll(async () => (await authState(page)).account).toBeNull();
  await expect(page.getByLabel("プロジェクト", { exact: true })).toBeDisabled();
  expect(await savedNames(page)).not.toContain("Retained A");
  service.failure = "";
  await signIn(page, "A");
  expect(await savedNames(page)).toContain("Retained A");
});

test("Account dialog remains compact and workflow regions retain their order", async ({ page, context }) => {
  await backend(context); await start(page);
  for (const width of [320, 390, 629, 630, 631, 820, 996, 997, 1100, 1242, 1273, 1440, 1600]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.getByRole("button", { name: "アカウント", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "アカウント" });
    await expect(dialog).toBeVisible();
    const box = await dialog.boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await page.keyboard.press("Escape"); await expect(dialog).not.toBeVisible();
    await expect(page.getByRole("button", { name: "アカウント", exact: true })).toBeFocused();
    for (const control of await page.locator(".header-actions > *:visible").all()) {
      const controlBox = await control.boundingBox();
      expect(controlBox!.x).toBeGreaterThanOrEqual(0);
      expect(controlBox!.x + controlBox!.width).toBeLessThanOrEqual(width);
    }
    const headerBoxes = await page.locator(".header-actions button:visible, .header-actions select:visible").evaluateAll(nodes =>
      nodes.map(node => { const r = node.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }; }));
    for (let i = 0; i < headerBoxes.length; i++) for (let j = i + 1; j < headerBoxes.length; j++) {
      const a = headerBoxes[i]!, b = headerBoxes[j]!;
      expect(a.right <= b.left + 1 || b.right <= a.left + 1 || a.bottom <= b.top + 1 || b.bottom <= a.top + 1).toBe(true);
    }
    if (width === 390 || width >= 1100) await page.screenshot({ path: `/tmp/issue184-account-${width}.png` });
    const bounds = await page.locator(".input-rail, .route-workspace, .status-rail").evaluateAll(nodes => nodes.map(node => ({ className: node.className, x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y })));
    expect(bounds).toHaveLength(3);
    if (width <= 1240) { expect(bounds[0]!.y).toBeLessThan(bounds[1]!.y); expect(bounds[1]!.y).toBeLessThan(bounds[2]!.y); }
    else { expect(bounds[0]!.x).toBeLessThan(bounds[1]!.x); expect(bounds[1]!.x).toBeLessThan(bounds[2]!.x); }
  }
});


test("browser process restart keeps account ownership offline; startup revocation closes it", async ({ baseURL }) => {
  test.setTimeout(180_000);
  const profile = mkdtempSync(join(tmpdir(), "autonavlog-184-"));
  let context = await chromium.launchPersistentContext(profile, { baseURL });
  try {
    await backend(context);
    let page = context.pages()[0]!; await start(page); await signIn(page, "Restart");
    await saveRoute(page, "Restart account route");
    const id = (await authState(page)).account.account_id;
    await context.close();
    context = await chromium.launchPersistentContext(profile, { baseURL });
    const service = await backend(context); service.failure = "offline";
    page = context.pages()[0]!; await start(page);
    expect((await authState(page)).account.account_id).toBe(id);
    expect(await savedNames(page)).toContain("Restart account route");
    await expect(page.getByLabel("プロジェクト", { exact: true })).toBeDisabled();
    service.failure = "USER_DISABLED";
    await page.reload();
    await expect.poll(async () => (await authState(page)).account).toBeNull();
    await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
    expect(await savedNames(page)).not.toContain("Restart account route");
    service.failure = ""; await signIn(page, "Restart");
    expect(await savedNames(page)).toContain("Restart account route");
  } finally { await context.close(); }
});


test("different identity closes the prior Application before async mapping, token refresh preserves it", async ({ page, context }) => {
  await backend(context); await start(page); await signIn(page, "A");
  const generation = await page.evaluate(() => (window as any).authTest.generation());
  await page.evaluate(() => (window as any).authTest.refresh());
  expect(await page.evaluate(() => (window as any).authTest.generation())).toBe(generation);
  await page.evaluate(() => {
    const original = crypto.subtle.digest.bind(crypto.subtle);
    crypto.subtle.digest = (algorithm, data) => {
      const text = new TextDecoder().decode(data as ArrayBuffer);
      if (text.includes("autonavlog.account.v1") && text.includes('"B"')) {
        return new Promise(resolve => { (window as any).releaseAccountMapping = () => original(algorithm, data).then(resolve); });
      }
      return original(algorithm, data);
    };
  });
  await page.evaluate(() => (window as any).authTest.signIn("B"));
  expect((await authState(page)).account).toBeNull();
  const code = await page.evaluate(async generation => {
    try { await (window as any).authTest.contexts[generation - 1].saveProject("late A"); return "accepted"; }
    catch (error) { return (error as any).code; }
  }, generation);
  expect(code).toBe("ACCOUNT_CONTEXT_CLOSED");
  await page.evaluate(() => (window as any).releaseAccountMapping());
  await expect.poll(async () => (await authState(page)).account?.displayName).toBe("B");
});

test("failed persistent logout closes access and offers a visible retry in the new context", async ({ page, context }) => {
  await backend(context); await start(page); await signIn(page, "A");
  await page.evaluate(() => {
    const original = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function (key) {
      if (key.startsWith("firebase:authUser:")) throw new DOMException("denied", "SecurityError");
      original.call(this, key);
    };
    (window as any).restoreStorage = () => { Storage.prototype.removeItem = original; };
  });
  await page.getByRole("button", { name: "アカウント", exact: true }).click();
  await page.getByRole("button", { name: "ログアウト", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("ログアウト状態を保存できませんでした");
  expect((await authState(page)).account).toBeNull();
  expect((await authState(page)).signOutPending).toBe(true);
  await page.evaluate(() => (window as any).restoreStorage());
  await page.getByRole("button", { name: "ログアウトを再試行", exact: true }).click();
  await expect.poll(async () => (await authState(page)).signOutPending).toBeUndefined();
  await page.reload();
  await expect.poll(async () => (await authState(page)).account).toBeNull();
});


test("logout from calculation overlay terminates in-flight work without modifying the retained snapshot", async ({ page, context }) => {
  await backend(context); await start(page); await signIn(page, "A"); await saveRoute(page, "A calculation");
  const before = (await accountRecords(page))[0];
  await page.evaluate(() => {
    const original = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function (message: any, ...rest: any[]) {
      if (message.type === "APPLY" && message.argumentList?.[0]?.value === "calculate") {
        (window as any).heldCalculation = true; return;
      }
      return (original as any).call(this, message, ...rest);
    };
    (window as any).restoreWorker = () => { Worker.prototype.postMessage = original; };
  });
  await page.getByRole("button", { name: /NAV LOGを(?:作る|再計算)$/ }).click();
  await expect.poll(() => page.evaluate(() => Boolean((window as any).heldCalculation))).toBe(true);
  await page.locator(".calculation-progress-dialog").getByRole("button", { name: "ログアウト", exact: true }).click();
  expect((await authState(page)).account).toBeNull();
  await expect(page.locator(".calculation-progress-backdrop")).not.toBeVisible();
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  expect(await savedNames(page)).not.toContain("A calculation");
  await page.evaluate(() => (window as any).restoreWorker());
  await signIn(page, "A");
  expect((await accountRecords(page))[0].lastCalculation).toEqual(before.lastCalculation);
});

for (const rejection of ["TOKEN_EXPIRED", "INVALID_ID_TOKEN"]) test(`revocation ${rejection} still closes access when sign-out cannot persist`, async ({ page, context }) => {
  const service = await backend(context); await start(page); await signIn(page, "A");
  await page.evaluate(() => {
    const original = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function (key) {
      if (key.startsWith("firebase:authUser:")) throw new DOMException("denied", "SecurityError");
      original.call(this, key);
    };
  });
  service.failure = rejection;
  await page.evaluate(() => (window as any).authTest.refresh());
  expect((await authState(page)).account).toBeNull();
  await expect(page.getByRole("alert")).toContainText("ログアウト状態を保存できませんでした");
});


test("blocked Google popup reports an actionable error without resetting anonymous input", async ({ page, context }) => {
  await backend(context); await start(page);
  await page.getByLabel("FUEL gal", { exact: true }).fill("81");
  await page.evaluate(() => { window.open = () => null; });
  await page.getByRole("button", { name: "アカウント", exact: true }).click();
  await page.getByRole("button", { name: "Googleでログイン", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("ポップアップがブロックされました");
  expect((await authState(page)).account).toBeNull();
  await page.keyboard.press("Escape");
  await expect(page.getByLabel("FUEL gal", { exact: true })).toHaveValue("81");
});
