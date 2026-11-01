import { test, expect } from "@playwright/test";
import { migrationRequest, legacyLinkStatus, prepareLegacyLink, linkLegacyAccount } from "../src/legacyMigration";

const originalFetch = globalThis.fetch;
test.afterEach(() => { globalThis.fetch = originalFetch; });

test("network and non-JSON failures use operation-specific approved copy", async () => {
  for (const fetcher of [
    async () => { throw new TypeError("Failed to fetch"); },
    async () => new Response("gateway unavailable", { status: 502 }),
  ]) {
    globalThis.fetch = fetcher;
    await expect(migrationRequest("/api/navmate-migration/status")).rejects.toThrow("引継ぎ状態を確認できませんでした。通信状態を確認して再試行してください。");
    await expect(legacyLinkStatus()).rejects.toThrow("アカウントの紐付け状態を取得できませんでした。画面を再読み込みしてください。");
    await expect(prepareLegacyLink()).rejects.toThrow("アカウント設定情報を取得できませんでした。時間をおいて再読み込みしてください。");
    await expect(linkLegacyAccount("synthetic-test-token")).rejects.toThrow("アカウントの紐付けに失敗しました。もう一度お試しください。");
  }
});

test("server-specific details are preserved for configuration, status, migration and link", async () => {
  globalThis.fetch = async () => Response.json({ error: { message: "server-specific reason" } }, { status: 409 });
  for (const operation of [() => prepareLegacyLink(), () => legacyLinkStatus(), () => migrationRequest("/api/navmate-migration/status"), () => linkLegacyAccount("synthetic-test-token")]) {
    await expect(operation()).rejects.toThrow("server-specific reason");
  }
});

test("only the link POST deadline gets result-unknown wording; cancellation stays aborted", async () => {
  const originalTimeout = AbortSignal.timeout;
  const deadlines: number[] = [];
  AbortSignal.timeout = milliseconds => {
    deadlines.push(milliseconds);
    return AbortSignal.abort(new DOMException("test deadline", "TimeoutError"));
  };
  globalThis.fetch = async (_url, init) => { init?.signal?.throwIfAborted(); throw new Error("expected abort"); };
  try {
    await expect(linkLegacyAccount("synthetic-test-token")).rejects.toThrow("処理結果を確認できませんでした。画面を再読み込みするか、もう一度お試しください。");
    expect(deadlines).toEqual([30_000]);
    await expect(migrationRequest("/api/navmate-migration/status", { signal: AbortSignal.abort(new DOMException("test deadline", "TimeoutError")) })).rejects.toThrow("引継ぎ状態を確認できませんでした。通信状態を確認して再試行してください。");
    const cancelled = AbortSignal.abort(new DOMException("test dismissal", "AbortError"));
    await expect(linkLegacyAccount("synthetic-test-token", cancelled)).rejects.toMatchObject({ name: "AbortError", message: "test dismissal" });
  } finally { AbortSignal.timeout = originalTimeout; }
});

test("accepted link response is returned without making an undo request", async () => {
  const linked = { state: "LINKED", completed: 0, total: 0, navmateUrl: "/navmate" };
  const calls: string[] = [];
  globalThis.fetch = async (url, init) => {
    calls.push(`${init?.method} ${url}`);
    return Response.json(linked);
  };
  await expect(linkLegacyAccount("synthetic-test-token")).resolves.toEqual(linked);
  expect(calls).toEqual(["POST /api/account-link"]);
});
