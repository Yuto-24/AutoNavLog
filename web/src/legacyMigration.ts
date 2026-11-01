import { getApp } from "firebase/app";
import { getAuth } from "firebase/auth";
import { googleAccount, type FirebaseAuthConfig } from "./firebaseAuthProvider";
import { accountBoundaryClosed } from "./auth";

export interface MigrationStatus {
  state: "UNLINKED" | "LINKED" | "MIGRATING" | "NAVMATE_ACTIVE";
  completed: number;
  total: number;
  error?: string | null;
  navmateUrl: string;
}
export interface MigrationProgress { percent: number; message: string; error?: string; retry?: () => void; signOut?: () => void }
class MigrationRequestError extends Error {}
async function requestJson<T>(url: string, init: RequestInit | undefined, fallback: string, timeoutFallback = fallback): Promise<T> {
  try {
    const response = await fetch(url, { ...init, cache: "no-store" });
    const body = await response.json().catch(() => null);
    init?.signal?.throwIfAborted();
    if (!response.ok || !body) throw new MigrationRequestError(body?.error?.message || fallback);
    return body as T;
  } catch (reason) {
    if (reason instanceof MigrationRequestError) throw reason;
    // Only a request deadline gets timeout wording. Caller cancellation remains
    // an abort, so dismissing the notice never surfaces a late error.
    if (init?.signal?.aborted) {
      if (init.signal.reason?.name !== "TimeoutError") throw reason;
      throw new MigrationRequestError(timeoutFallback);
    }
    throw new MigrationRequestError(fallback);
  }
}
export function migrationRequest(url: string, init?: RequestInit): Promise<MigrationStatus> {
  return requestJson(url, init, "引継ぎ状態を確認できませんでした。通信状態を確認して再試行してください。");
}
export const progressOf = (status: MigrationStatus): MigrationProgress => ({
  percent: status.total ? Math.min(99, Math.floor(status.completed / status.total * 100)) : 0,
  message: `プロジェクトを引継ぎ・検証中 ${status.completed} / ${status.total} 件`,
});

// This runs BEFORE constructing Application, its Worker, local import or sync outbox.
export async function activateLegacyAccount(accountId: string, signal: AbortSignal,
  progress: (value: MigrationProgress) => void) {
  const origin = import.meta.env.VITE_LEGACY_MIGRATION_URL;
  if (!origin) return;
  // NAVMATE_ACTIVE is irreversible; completed accounts no longer depend on Legacy availability.
  const activeKey = `autonavlog.migration.active.${accountId}`;
  try { if (localStorage.getItem(activeKey) === "1") return; } catch { /* storage optional */ }
  const user = getAuth(getApp()).currentUser;
  if (!user || (await googleAccount(user)).account_id !== accountId) throw accountBoundaryClosed();
  const assert = () => { if (signal.aborted || getAuth(getApp()).currentUser !== user) throw accountBoundaryClosed(); };
  const request = async (operation: string) => {
    assert();
    const token = await user.getIdToken();
    assert();
    const result = await migrationRequest(`${origin.replace(/\/$/, "")}/api/navmate-migration/${operation}`, {
      method: operation === "status" ? "GET" : "POST", credentials: "omit", signal: AbortSignal.any([signal, AbortSignal.timeout(120_000)]),
      headers: { Authorization: `Bearer ${token}` },
    });
    assert();
    return result;
  };
  progress({ percent: 0, message: "Legacyの引継ぎ状態を確認中" });
  let status = await request("status");
  if (status.state === "UNLINKED") return;
  if (status.state === "NAVMATE_ACTIVE") {
    try { localStorage.setItem(activeKey, "1"); } catch { /* storage optional */ }
    return;
  }
  status = await request("begin");
  while (status.state === "MIGRATING") {
    progress(progressOf(status));
    status = await request("step");
  }
  if (status.state !== "NAVMATE_ACTIVE") throw new Error(status.error || "引継ぎが中断されました。再試行してください。");
  try { localStorage.setItem(activeKey, "1"); } catch { /* storage optional */ }
}

let legacyProvider: Promise<import("./firebaseAuthProvider").FirebaseAuthProvider> | undefined;
let legacyLinkPending = false;
export async function prepareLegacyLink() {
  // A dismissed Google popup may still resolve later. Do not let another dialog
  // reuse its provider until cleanup completes.
  if (legacyLinkPending) throw new Error("認証の準備ができませんでした。画面を再読み込みしてください。");
  legacyProvider ??= requestJson<FirebaseAuthConfig>("/api/account-link/config", undefined,
    "アカウント設定情報を取得できませんでした。時間をおいて再読み込みしてください。").then(async config => {
    const { createFirebaseAuthProvider } = await import("./firebaseAuthProvider");
    // The Legacy link dialog authenticates Google only. Account generation and
    // Firestore lifecycle belong to the NavMate Local context after linking.
    return createFirebaseAuthProvider(config, async user => ({ accountId: (await googleAccount(user)).account_id, deleting: false }));
  }).catch(error => { legacyProvider = undefined; throw error; });
  let auth: import("./firebaseAuthProvider").FirebaseAuthProvider;
  try {
    auth = await legacyProvider;
    await auth.signOut();
  } catch (reason) {
    if (reason instanceof MigrationRequestError) throw reason;
    throw new Error("認証の準備ができませんでした。画面を再読み込みしてください。");
  }
  return async (signal?: AbortSignal) => {
    signal?.throwIfAborted();
    if (legacyLinkPending) throw new Error("認証の準備ができませんでした。画面を再読み込みしてください。");
    legacyLinkPending = true;
    try {
      // No asynchronous preparation before the popup: retain Safari click activation.
      await auth.signIn();
      signal?.throwIfAborted();
      if (!auth.getState().account) return;
      let token: string;
      try { token = await getAuth(getApp()).currentUser!.getIdToken(true); }
      catch { throw new Error("Google ログインに失敗しました。通信状態を確認して再試行してください。"); }
      signal?.throwIfAborted();
      return await linkLegacyAccount(token, signal);
    } finally {
      await auth.signOut().catch(() => {});
      legacyLinkPending = false;
    }
  };
}
export function linkLegacyAccount(token: string, signal?: AbortSignal): Promise<MigrationStatus> {
  return requestJson<MigrationStatus>("/api/account-link", { method: "POST",
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30_000)]) : AbortSignal.timeout(30_000),
    headers: { Authorization: `Bearer ${token}` } },
    "アカウントの紐付けに失敗しました。もう一度お試しください。",
    "処理結果を確認できませんでした。画面を再読み込みするか、もう一度お試しください。");
}
export function legacyLinkStatus() {
  return requestJson<MigrationStatus>("/api/account-link", undefined,
    "アカウントの紐付け状態を取得できませんでした。画面を再読み込みしてください。");
}
export function observeLegacyMigration(listener: (status: MigrationStatus) => void) {
  let active = true;
  let timer: ReturnType<typeof setTimeout>;
  const poll = async () => {
    try {
      const response = await fetch("/api/account-link", { cache: "no-store" });
      if (response.status === 404) return;
      if (!response.ok) throw new Error("status unavailable");
      const next = await response.json() as MigrationStatus;
      if (!active) return;
      listener(next);
      if (next.state === "NAVMATE_ACTIVE") { window.location.replace(next.navmateUrl); return; }
    } catch { /* A blocked UI remains blocked until the server resolves the lease. */ }
    if (active) timer = setTimeout(() => { void poll(); }, 3000);
  };
  void poll();
  return () => { active = false; clearTimeout(timer); };
}
