import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";

const first = { id: "saved-plan", bodyHash: "saved-body", title: "保存した計画を開けない場合があります",
  description: ["以前保存した計画で、内容が表示されないことがあります。"],
  sections: [{ title: "回避方法", items: ["ページを開き直してください。"] }] };
const second = { id: "map", bodyHash: "map-body", title: "地図が表示されない場合があります",
  description: ["一部の環境で地図が表示されません。"], sections: [] };

test("real bundle shows Known Issues first, distinguishes warning updates, and marks everything seen on opening", async ({ page }) => {
  // Replace only generated Information data in the response. Components, styles, storage,
  // App wiring and API requests all remain the production Docker bundle.
  let issues = [first, second];
  const informationId = () => "information:sha256:" + createHash("sha256")
    .update(JSON.stringify(issues.map(({ title, description, sections }) => ({ title, description, sections })))).digest("hex");
  await page.route("**/assets/index-*.js", async (route) => {
    const response = await route.fetch();
    const original = await response.text();
    const pattern = /"knownIssues":\[\],"id":"information:sha256:[a-f0-9]+","knownIssuesId":"information:sha256:[a-f0-9]+"/g;
    expect(original.match(pattern)).toHaveLength(1);
    const fields = JSON.stringify({ knownIssues: issues, id: informationId(), knownIssuesId: informationId() }).slice(1, -1);
    await route.fulfill({ response, body: original.replace(pattern, () => fields) });
  });
  // A pre-existing general read marker does not imply Known Issues have been read.
  await page.addInitScript((id) => {
    const key = "autonavlog.information.lastSeenUpdate";
    if (window.localStorage.getItem(key) === null) window.localStorage.setItem(key, id);
  }, informationId());
  await page.goto("/");
  const button = page.locator(".app-header").getByRole("button", { name: /Information/ });
  const dialog = page.getByRole("dialog", { name: "Information" });
  const acknowledge = async () => {
    await button.click();
    await expect(dialog).toBeVisible();
    await expect(button).toHaveAccessibleName("Information");
    await expect(button.locator(".information-unread-dot")).toHaveCount(0);
    await page.keyboard.press("Escape");
  };
  await expect(button).toHaveAccessibleName("Information（既知の不具合に更新があります）");
  await expect(button).toHaveClass(/information-warning/);
  const warningColor = await button.evaluate((element) => getComputedStyle(element).color);
  await button.click();
  await expect(dialog.getByRole("heading", { name: "既知の不具合", exact: true })).toBeVisible();
  await expect(dialog.getByText(first.sections[0]!.items[0]!, { exact: true })).toBeVisible();
  await expect(dialog).not.toContainText(/saved-plan|saved-body|github-issue|Issue #|配布/);
  const known = await dialog.locator(".information-known-issues").boundingBox();
  const history = await dialog.locator(".information-history-heading").boundingBox();
  expect(known && history && known.y + known.height <= history.y).toBeTruthy();
  await page.screenshot({ path: "/tmp/issue140-known-issues.png" });
  await page.keyboard.press("Escape");
  await expect(button).not.toHaveClass(/information-warning/);
  expect(await button.evaluate((element) => getComputedStyle(element).color)).not.toBe(warningColor);
  await page.reload();
  await expect(button).toHaveAccessibleName("Information");

  issues = [second, first];
  await page.reload();
  await expect(button).toHaveAccessibleName("Information（未読の更新があります）");
  await expect(button).not.toHaveClass(/information-warning/);
  await acknowledge();

  issues = [first];
  await page.reload();
  await expect(button).toHaveAccessibleName("Information（未読の更新があります）");
  await expect(button).not.toHaveClass(/information-warning/);
  await acknowledge();

  issues = [{ ...first, id: "renamed-management-id" }];
  await page.reload();
  await expect(button).toHaveAccessibleName("Information");

  issues = [{ ...first, id: "renamed-management-id", bodyHash: "updated-body", description: ["一部の計画で内容が表示されません。"] }];
  await page.reload();
  await expect(button).toHaveAccessibleName("Information（既知の不具合に更新があります）");
  await acknowledge();

  issues = [...issues, second];
  await page.reload();
  await expect(button).toHaveClass(/information-warning/);
  await acknowledge();

  issues = [];
  await page.reload();
  await expect(button).toHaveAccessibleName("Information（未読の更新があります）");
  await expect(button).not.toHaveClass(/information-warning/);
  await button.click();
  await expect(dialog.locator(".information-known-issues")).toHaveCount(0);
});
