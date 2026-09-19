import { getApp } from "firebase/app";
import { getAuth } from "firebase/auth";
import { googleAccount } from "./firebaseAuthProvider";
import { accountBoundaryClosed } from "./auth";

export interface MigrationStatus {
  state: "UNLINKED" | "LINKED" | "MIGRATING" | "NAVMATE_ACTIVE";
  completed: number;
  total: number;
  error?: string | null;
  navmateUrl: string;
}
export interface MigrationProgress { percent: number; message: string; error?: string; retry?: () => void; signOut?: () => void }
export async function migrationRequest(url: string, init?: RequestInit): Promise<MigrationStatus> {
  const response = await fetch(url, { ...init, cache: "no-store" });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || "引継ぎ状態を確認できません。接続を確認して再試行してください。");
  return body;
}
export const progressOf = (status: MigrationStatus): MigrationProgress => ({
  percent: status.total ? Math.min(99, Math.floor(status.completed / status.total * 100)) : 0,
  message: `Projectを引継ぎ・検証中 ${status.completed} / ${status.total} 件`,
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
  if (status.state !== "NAVMATE_ACTIVE") throw new Error(status.error || "引継ぎが中断されました。再試行してください。Legacyは利用できます。");
  try { localStorage.setItem(activeKey, "1"); } catch { /* storage optional */ }
}

let legacyProvider: Promise<import("./firebaseAuthProvider").FirebaseAuthProvider> | undefined;
export async function prepareLegacyLink() {
  legacyProvider ??= fetch("/api/account-link/config").then(async response => {
    if (!response.ok) throw new Error("この環境ではアカウント紐付けが設定されていません。");
    const { createFirebaseAuthProvider } = await import("./firebaseAuthProvider");
    return createFirebaseAuthProvider(await response.json());
  }).catch(error => { legacyProvider = undefined; throw error; });
  const auth = await legacyProvider;
  await auth.signOut();
  return async () => {
    try {
      // No asynchronous preparation before the popup: retain Safari click activation.
      await auth.signIn();
      if (!auth.getState().account) return;
      const token = await getAuth(getApp()).currentUser!.getIdToken(true);
      return await migrationRequest("/api/account-link", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    } finally { await auth.signOut().catch(() => {}); }
  };
}
export function legacyLinkStatus() { return migrationRequest("/api/account-link"); }
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
