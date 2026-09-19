import { AccountProjectRepository } from "./accountProjectRepository";
import { AccountSyncController } from "./accountSyncController";
import type { AccountSyncRepository } from "./accountSync";
import type { LocalAccountContext } from "./localProjectRepository";
import { accountBoundaryClosed, type AuthProvider } from "./auth";
import { LocalApplication } from "./localApplication";
import { SESSION_KEY } from "./applicationSession";
import type { PlatformCapabilities } from "./platform";

// One immutable context per authentication lifetime. Never retarget an existing
// Worker or queued save to another account's Repository.
export function createAccountContext(platform: PlatformCapabilities, auth?: AuthProvider, allowRecovery = true,
  createSync?: (context: LocalAccountContext) => Promise<AccountSyncRepository>) {
  const account_id = auth?.getState().account?.account_id ?? null;
  let active = true;
  const assertActive = () => { if (!active) throw accountBoundaryClosed(); };
  const closeListeners = new Set<() => void>();
  const context = { account_id, assertActive, onClose(listener: () => void) {
    assertActive(); closeListeners.add(listener); return () => { closeListeners.delete(listener); };
  } };
  const storage = platform.session.storage;
  const key = account_id ? `${SESSION_KEY}.${account_id}` : SESSION_KEY;
  const scopedPlatform: PlatformCapabilities = { ...platform, session: {
    isReload: () => allowRecovery && platform.session.isReload(),
    storage: {
      getItem: () => { assertActive(); return storage.getItem(key); },
      setItem: (_key, value) => { assertActive(); storage.setItem(key, value); },
      removeItem: () => { assertActive(); storage.removeItem(key); },
    },
  } };
  let sync: AccountSyncController | undefined;
  const application = new LocalApplication((validate, _context, cleanCalculation) => {
    if (!account_id || !createSync) return platform.persistence.createProjectRepository(validate, context);
    const deviceKey = "autonavlog.sync.device";
    let deviceId = platform.persistence.values.getItem(deviceKey);
    if (!deviceId) { deviceId = crypto.randomUUID(); platform.persistence.values.setItem(deviceKey, deviceId); }
    const repository = new AccountProjectRepository(validate, context, deviceId, cleanCalculation!);
    sync = new AccountSyncController(repository, context, () => createSync(context), async () => {
      const anonymous = platform.persistence.createProjectRepository(validate, { ...context, account_id: null });
      return await anonymous.claimAnonymous?.(account_id) ?? [];
    });
    return repository;
  }, undefined, auth);
  application.sync = sync;
  return { application, platform: scopedPlatform, dispose() {
    active = false;
    sync?.dispose();
    for (const listener of closeListeners) listener();
    closeListeners.clear();
    application.dispose();
    // Ephemeral input must not be restored after logout or account switching.
    // The Repository and its Last Calculation remain intact.
    try { storage.removeItem(key); } catch { /* inactive storage cannot be read through this context */ }
  } };
}

export function observeAccountContexts(platform: PlatformCapabilities, auth: AuthProvider | undefined,
  present: (context: ReturnType<typeof createAccountContext>) => void,
  createSync?: (context: LocalAccountContext) => Promise<AccountSyncRepository>,
  prepare?: (accountId: string, signal: AbortSignal) => Promise<void>,
  pending?: (error?: string, retry?: () => void) => void) {
  let context: ReturnType<typeof createAccountContext> | undefined;
  let accountId = auth?.getState().account?.account_id ?? null;
  let boundary = new AbortController();
  let initial = true;
  const start = () => {
    boundary.abort();
    boundary = new AbortController();
    const signal = boundary.signal;
    context?.dispose(); context = undefined;
    const show = () => {
      if (signal.aborted) return;
      context = createAccountContext(platform, auth, initial, createSync);
      initial = false;
      present(context);
    };
    if (accountId && prepare) {
      pending?.();
      void prepare(accountId, signal).then(show).catch(error => {
        if (!signal.aborted) pending?.(error instanceof Error ? error.message : "引継ぎを確認できません。", start);
      });
    } else show();
  };
  start();
  const unsubscribe = auth?.subscribe(() => {
    const next = auth.getState().account?.account_id ?? null;
    if (next === accountId) return;
    accountId = next;
    initial = false;
    start();
  });
  return () => { boundary.abort(); unsubscribe?.(); context?.dispose(); };
}
