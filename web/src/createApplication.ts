import type { PlatformCapabilities } from "./platform";
import type { AutoNavLogApplication } from "./application";
import type { MigrationProgress } from "./legacyMigration";
import type { AuthProvider } from "./auth";
import { LegacyApplication } from "./legacyApplication";

export interface ApplicationContext { application: AutoNavLogApplication; platform: PlatformCapabilities; dispose(): void }
export async function startApplication(platform: PlatformCapabilities, present: (context: ApplicationContext) => void,
  migrationProgress?: (progress: MigrationProgress) => void) {
  if (import.meta.env.VITE_CALCULATION_MODE !== "local") {
    present({ application: new LegacyApplication(), platform, dispose() {} });
    return;
  }
  const { observeAccountContexts } = await import("./accountContext");
  platform.persistence.requestRetention();
  let auth: AuthProvider | undefined;
  const { VITE_FIREBASE_API_KEY: apiKey, VITE_FIREBASE_AUTH_DOMAIN: authDomain,
    VITE_FIREBASE_PROJECT_ID: projectId, VITE_FIREBASE_APP_ID: appId } = import.meta.env;
  if (apiKey && authDomain && projectId && appId) {
    try {
      const { createFirebaseAuthProvider } = await import("./firebaseAuthProvider");
      auth = await createFirebaseAuthProvider({ apiKey, authDomain, projectId, appId });
    } catch {
      const state = { account: null, available: false, notice: "認証を初期化できませんでした。未ログインの端末内作業は利用できます。ログインするには設定を確認して再読み込みしてください。" };
      auth = { getState: () => state, subscribe: () => () => {},
        signIn: async () => { throw new Error(state.notice); }, signOut: async () => {} };
    }
  }
  const { createFirestoreSyncRepository } = await import("./firestoreSyncRepository");
  const report = (value: MigrationProgress) => migrationProgress?.({ ...value,
    signOut: () => { void auth?.signOut().catch(() => {}); } });
  const { activateLegacyAccount } = await import("./legacyMigration");
  observeAccountContexts(platform, auth, present, createFirestoreSyncRepository,
    import.meta.env.VITE_LEGACY_MIGRATION_URL
      ? (accountId, signal) => accountId.startsWith("account_v2_")
        ? Promise.resolve() // A newly registered generation cannot inherit the old Legacy link.
        : activateLegacyAccount(accountId, signal, report)
      : undefined,
    (error, retry) => report({ percent: 0, message: "Legacyの引継ぎ状態を確認中", error, retry }));
}
