import { enterImportWorkflow } from "./helpers/importWorkflow";
import { test, expect } from "@playwright/test";
import { backend } from "./helpers/sync-auth";

const path = "/e2e/auth-harness/index.html?sync&migration";
test("first login migrates 200 Legacy Projects through Firestore and fresh device restores calculation", async ({ page, context, browser, request }) => {
  const subject = `migration-${crypto.randomUUID()}`;
  const seed = await request.post(`http://127.0.0.1:8186/fixture/seed?subject=${subject}&count=200`);
  expect(seed.ok()).toBeTruthy();
  const { projectId } = await seed.json();
  await backend(context);
  // Preserve the real migration API; only Google identity exchanges are synthetic.
  await context.route("**/api/navmate-migration/**", route => route.continue());
  let release: () => void = () => {};
  const wait = new Promise<void>(resolve => { release = resolve; });
  let steps = 0;
  await context.route("**/api/navmate-migration/step", async route => {
    if (++steps === 1) await wait;
    await route.continue();
  });
  await page.goto(path);
  await enterImportWorkflow(page);
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await page.evaluate(subject => (window as any).authTest.signIn(subject), subject);
  await expect(page.getByRole("status", { name: "NavMateへ引継ぎ中" })).toBeVisible();
  await expect(page.getByText("Projectを引継ぎ・検証中 0 / 200 件")).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "NavMateへ引継ぎ中 0%" })).toBeVisible();
  await expect(page.getByLabel("DATE", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).authTest.contexts.length)).toBe(1);
  release();
  await enterImportWorkflow(page);
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await expect(page.locator(`#saved-project option[value="${projectId}"]`)).toHaveCount(1);
  await expect(page.locator("#saved-project option")).toHaveCount(201);
  expect(steps).toBe(9);
  const second = await browser.newContext();
  try {
    await backend(second);
    await second.route("**/api/navmate-migration/**", route => route.continue());
    const fresh = await second.newPage();
    await fresh.goto(path);
    await enterImportWorkflow(fresh);
  await expect(fresh.getByLabel("DATE", { exact: true })).toBeVisible();
    await fresh.evaluate(subject => (window as any).authTest.signIn(subject), subject);
    await expect(fresh.locator(`#saved-project option[value="${projectId}"]`)).toHaveCount(1);
    await fresh.locator("#saved-project").selectOption(projectId);
    await fresh.getByRole("button", { name: "保存済みProjectを開く", exact: true }).click();
    await expect(fresh.locator(".nav-log-table")).toBeVisible();
    const records = await fresh.evaluate(async () => {
      const id = (window as any).authTest.state().account.account_id;
      return new Promise<any[]>(resolve => {
        const open = indexedDB.open(`autonavlog.projects.${id}`);
        open.onsuccess = () => {
          const read = open.result.transaction("projects").objectStore("projects").getAll();
          read.onsuccess = () => { open.result.close(); resolve(read.result); };
        };
      });
    });
    expect(records.find(row => row.id === projectId)?.lastCalculation).toBeTruthy();
    expect(JSON.stringify(records)).not.toContain("web_owner_id");
  } finally { await second.close(); }
});

test("activation failure never creates account Application; retry progresses automatically", async ({ page, context }) => {
  await backend(context);
  let fail = true, steps = 0;
  await context.route("**/api/navmate-migration/**", async route => {
    const operation = route.request().url().split("/").pop();
    if (operation === "step") {
      steps++;
      if (fail) { await route.fulfill({ status: 503, contentType: "text/html", body: "<h1>Upstream unavailable</h1>" }); return; }
    }
    await route.fulfill({ json: { state: operation === "status" ? "LINKED" : operation === "begin" ? "MIGRATING" : "NAVMATE_ACTIVE", completed: 0, total: 200, navmateUrl: "/" } });
  });
  await page.goto(path);
  await enterImportWorkflow(page);
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  await page.evaluate(() => (window as any).authTest.signIn("migration-retry"));
  await expect(page.getByRole("alert")).toContainText("接続を確認して再試行してください");
  expect(await page.evaluate(() => (window as any).authTest.contexts.length)).toBe(1);
  await expect(page.getByLabel("DATE", { exact: true })).toHaveCount(0);
  fail = false;
  await page.getByRole("button", { name: "再試行", exact: true }).click();
  await enterImportWorkflow(page);
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  expect(steps).toBe(2);
});

test("Legacy account UI preserves 1100/1300 workflow order and locks then redirects during activation", async ({ page, context, request }) => {
  const subject = `migration-${crypto.randomUUID()}`;
  const seed = await request.post(`http://127.0.0.1:8186/fixture/seed?subject=${subject}`);
  expect(seed.ok()).toBeTruthy();
  await context.route("**/api/**", async route => {
    // Vite hosts the actual Legacy entry; forward its relative API to the fixture.
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: `http://127.0.0.1:8186${url.pathname}${url.search}`,
      headers: { ...route.request().headers(), "Cf-Access-Jwt-Assertion": subject } });
    await route.fulfill({ response });
  });
  await page.goto("http://127.0.0.1:5180/");
  await enterImportWorkflow(page);
  await expect(page.getByLabel("DATE", { exact: true })).toBeVisible();
  for (const width of [1100, 1300]) {
    await page.setViewportSize({ width, height: 1100 });
    const regions = await Promise.all([".input-rail", ".route-workspace", ".status-rail"].map(selector => page.locator(selector).boundingBox()));
    expect(regions.every(Boolean)).toBe(true);
    const [input, route, readiness] = regions;
    if (width <= 1240) {
      expect(input!.y + input!.height).toBeLessThanOrEqual(route!.y);
      expect(route!.y + route!.height).toBeLessThanOrEqual(readiness!.y);
    } else {
      expect(input!.x + input!.width).toBeLessThanOrEqual(route!.x);
      expect(route!.x + route!.width).toBeLessThanOrEqual(readiness!.x);
    }
  }
  await page.getByRole("button", { name: "アカウント", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "アカウント", exact: true })).toBeVisible();
  await expect(page.getByText("紐付け済みです。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "アカウントを閉じる", exact: true }).click();
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const token = `${part({ alg: "none" })}.${part({ sub: `firebase-${subject}`, aud: "demo-autonavlog-sync", iss: "https://securetoken.google.com/demo-autonavlog-sync", iat: now, exp: now + 3600, auth_time: now, firebase: { sign_in_provider: "google.com", identities: { "google.com": [subject] } } })}.`;
  const headers = { Authorization: `Bearer ${token}` };
  const begin = await request.post("http://127.0.0.1:8186/api/navmate-migration/begin", { headers });
  expect(begin.ok()).toBeTruthy();
  await expect(page.getByRole("status", { name: "NavMateへ引継ぎ中" })).toBeVisible();
  expect(await page.getByLabel("DATE", { exact: true }).evaluate(element => Boolean(element.closest("[inert]")))).toBe(true);
  for (let index = 0; index < 2; index++) {
    const result = await request.post("http://127.0.0.1:8186/api/navmate-migration/step", { headers });
    expect(result.ok()).toBeTruthy();
  }
  await expect(page).toHaveURL("http://127.0.0.1:5179/");
});
