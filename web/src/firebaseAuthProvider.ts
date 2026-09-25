import { initializeApp } from "firebase/app";
import {
  browserLocalPersistence, browserPopupRedirectResolver, GoogleAuthProvider, initializeAuth,
  onIdTokenChanged, signInWithPopup, signOut, type Auth, type User,
} from "firebase/auth";
import { accountBoundaryClosed, type Account, type AuthProvider, type AuthState } from "./auth";
import { currentAccount, deleteCurrentAccount } from "./accountLifecycle";

export interface FirebaseAuthConfig { apiKey: string; authDomain: string; projectId: string; appId: string }

// Versioned, deterministic mapping from Google's immutable subject, not email or
// a device-generated ID. The same identity maps to the same internal ID on every device.
function googleSubject(user: User): string {
  const identity = user.providerData.find(provider => provider.providerId === "google.com");
  if (!identity?.uid || user.providerData.length !== 1) throw new Error("Google アカウントでログインしてください。");
  return identity.uid;
}
export async function googleAccount(user: User): Promise<Account> {
  const subject = googleSubject(user);
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(
    JSON.stringify(["autonavlog.account.v1", "https://accounts.google.com", subject])));
  const id = Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, "0")).join("");
  return { account_id: `account_v1_${id}`, displayName: user.displayName || "Google アカウント" };
}

const revokedCodes = new Set(["auth/user-disabled", "auth/user-token-expired", "auth/invalid-user-token", "auth/user-not-found"]);
const codeOf = (error: unknown) => (error as { code?: string })?.code;

export class FirebaseAuthProvider implements AuthProvider {
  private state: AuthState = { account: null, available: true };
  private listeners = new Set<() => void>();
  private generation = 0;
  private unsubscribe: () => void = () => {};
  private refreshing = false;
  private closing = false;
  private registrationSubject: string | null = null;
  private subject: string | null = null;
  private timer: ReturnType<typeof setInterval> | undefined;
  constructor(private readonly auth: Auth,
    private readonly resolveAccount: typeof currentAccount = currentAccount) {}

  async start() {
    await this.auth.authStateReady();
    await this.accept(this.auth.currentUser);
    this.unsubscribe = onIdTokenChanged(this.auth, user => {
      if (this.registrationMatches(user)) return;
      void this.accept(user);
    });
    // SDK token events alone do not actively check revocation. Revalidate on
    // reconnect/focus and periodically; network errors retain the cached account.
    window.addEventListener("online", this.refresh);
    window.addEventListener("focus", this.refresh);
    this.timer = setInterval(this.refresh, 60_000);
    return this;
  }
  getState = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish(state: AuthState) {
    this.state = state;
    for (const listener of this.listeners) listener();
  }
  private registrationMatches(user: User | null) {
    if (!user || !this.registrationSubject) return false;
    try { return googleSubject(user) === this.registrationSubject; }
    catch { return false; }
  }
  private async accept(user: User | null, register = false) {
    const generation = ++this.generation;
    if (this.closing) return;
    if (!user) { this.subject = null; this.publish({ account: null, available: true }); return; }
    if (user !== this.auth.currentUser) return;
    let subject: string;
    try { subject = googleSubject(user); }
    catch {
      this.subject = null;
      this.publish({ account: null, available: true, notice: "Google アカウントで再ログインしてください。" });
      return;
    }
    // Close the old context synchronously, before asynchronous ID derivation.
    if (subject !== this.subject) {
      this.subject = subject;
      this.publish({ account: null, available: true });
    }
    let account: Account;
    try {
      const closeOldContext = () => {
        if (user !== this.auth.currentUser) throw accountBoundaryClosed();
        if (this.state.account) this.publish({ account: null, available: true });
      };
      const identity = await this.resolveAccount(user, register, closeOldContext);
      if (identity.deleting) {
        if (generation === this.generation) this.publish({ account: null, available: true,
          notice: "アカウント削除が途中です。オンラインで削除を再試行してください。", deletionPending: true });
        return;
      }
      account = { account_id: identity.accountId, displayName: user.displayName || "Google アカウント" };
    }
    catch (error) {
      if ((error as Error)?.message === "ACCOUNT_DELETION_PENDING") {
        if (generation === this.generation) this.publish({ account: null, available: true,
          notice: "アカウント削除が途中です。オンラインで削除を再試行してください。", deletionPending: true });
        return;
      }
      if ((error as Error)?.message === "ACCOUNT_DELETED") {
        if (generation === this.generation) {
          this.publish({ account: null, available: true, notice: "このNavMateアカウントは削除されました。再利用する場合はGoogleでログインしてください。" });
        }
        return;
      }
      if (generation === this.generation) this.publish({ account: null, available: true, notice: "アカウントを確認できませんでした。再読み込みしてください。" });
      return;
    }
    // Subscriber/render errors are not authentication failures.
    if (generation === this.generation && user === this.auth.currentUser && !this.closing) this.publish({ account, available: true });
  }
  refresh = async () => {
    const user = this.auth.currentUser;
    if (!user || this.refreshing) return;
    if (this.registrationMatches(user)) return;
    this.refreshing = true;
    try { await user.getIdToken(true); if (!this.registrationMatches(user)) await this.accept(user); }
    catch (error) {
      if (this.auth.currentUser !== user) {
        // SDK revocation handling can clear currentUser before a persistence
        // failure masks the original token error. Close our context regardless.
        if (!this.auth.currentUser) await this.signOut().catch(() => undefined);
        return;
      }
      if (revokedCodes.has(codeOf(error) ?? "")) {
        await this.signOut().then(() => {
          this.publish({ account: null, available: true, notice: "認証が失効しました。同じアカウントで再ログインすると保存データに戻れます。" });
        }).catch(() => undefined);
      } else {
        this.publish({ ...this.state, notice: "認証サービスへ接続できません。端末内の作業は継続できます。" });
      }
    } finally { this.refreshing = false; }
  };
  async signIn() {
    const provider = new GoogleAuthProvider();
    provider.setCustomParameters({ prompt: "select_account" });
    try {
      await this.signInWith(async () => (await signInWithPopup(this.auth, provider)).user);
    } catch (error) {
      if (codeOf(error) === "auth/popup-closed-by-user" || codeOf(error) === "auth/cancelled-popup-request") return;
      throw new Error(codeOf(error) === "auth/popup-blocked"
        ? "ポップアップがブロックされました。このサイトのポップアップを許可して再試行してください。"
        : "Google ログインに失敗しました。通信状態を確認して再試行してください。");
    }
  }
  // The production popup and synthetic browser test credential enter the same
  // explicit registration window. Token refresh never opens a new generation.
  async signInWith(operation: () => Promise<User>) {
    if (this.closing) await this.signOut();
    const user = await operation();
    this.registrationSubject = googleSubject(user);
    try { await this.accept(user, true); }
    finally {
      this.registrationSubject = null;
      if (this.auth.currentUser !== user) await this.accept(this.auth.currentUser);
    }
  }
  async signOut() {
    // Close access immediately; durable Project copies are never deleted.
    this.closing = true;
    ++this.generation;
    this.publish({ account: null, available: true });
    try {
      await signOut(this.auth);
      this.closing = false;
      this.subject = null;
      this.publish({ account: null, available: true });
    } catch {
      const notice = "ログアウト状態を保存できませんでした。再読み込みするとログイン状態に戻る可能性があります。ログアウトを再試行してください。";
      this.publish({ account: null, available: true, signOutPending: true, notice });
      throw new Error(notice);
    }
  }
  async deleteAccount() {
    const user = this.auth.currentUser;
    if (!user || !navigator.onLine) throw new Error("アカウント削除にはオンライン接続と再認証が必要です。");
    try {
      const oldId = this.state.account?.account_id;
      const identity = await currentAccount(user, false, () => this.publish({ account: null, available: true }));
      const accountId = oldId ?? (identity.deleting ? identity.accountId : null);
      if (!accountId || accountId !== identity.accountId) throw new Error("アカウントを確認できません。再ログインしてください。");
      await deleteCurrentAccount(user, accountId, () => this.publish({ account: null, available: true,
        notice: "アカウントのデータを削除中です。完了までこの画面を閉じないでください。", deletionPending: true }));
      await this.signOut();
      this.publish({ account: null, available: true, notice: "NavMateアカウントを削除しました。Googleアカウントは変更していません。" });
    } catch (error) {
      if ((error as Error)?.message === "ACCOUNT_DELETED") {
        this.publish({ account: null, available: true,
          notice: "このNavMateアカウントは既に削除されています。再利用する場合はGoogleでログインしてください。" });
        return;
      }
      let deletionPending = this.state.deletionPending || (error as Error)?.message === "ACCOUNT_DELETION_PENDING";
      try { deletionPending ||= (await currentAccount(user)).deleting; }
      catch { /* Retain the state set after the durable DELETING transition. */ }
      if (deletionPending) this.publish({ account: null, available: true,
        notice: "アカウント削除が途中です。オンラインで削除を再試行してください。", deletionPending: true });
      if ((error as Error)?.message === "ACCOUNT_DELETION_PENDING")
        throw new Error("端末内のデータを削除できませんでした。オンラインで削除を再試行してください。");
      throw error;
    }
  }
  dispose() {
    this.unsubscribe();
    clearInterval(this.timer);
    window.removeEventListener("online", this.refresh);
    window.removeEventListener("focus", this.refresh);
  }
}

// Firebase 12.19 clears persisted users at startup on every error except
// network-request-failed (reloadAndSetCurrentUserOrClear). Normalize HTTP outages
// during initialization so a structured 503/429 cannot masquerade as revocation.
// This narrow, temporary transport guard leaves SDK persistence/token handling intact.
export async function retainAuthDuringOutage<T>(initialize: () => Promise<T>): Promise<T> {
  const original = globalThis.fetch;
  const guarded: typeof fetch = async (input, init) => {
    const response = await original(input, init);
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, globalThis.location?.href ?? "https://localhost");
    if (["identitytoolkit.googleapis.com", "securetoken.googleapis.com"].includes(url.hostname)) {
      let temporary = response.status === 429 || response.status >= 500;
      if (response.status === 400) {
        const body = await response.clone().json().catch(() => null);
        const message = body?.error?.message;
        temporary = typeof message === "string" &&
          ["TOO_MANY_ATTEMPTS_TRY_LATER", "QUOTA_EXCEEDED", "INTERNAL_ERROR"].includes(message.split(" : ")[0]!);
      }
      if (temporary) throw new TypeError("Authentication service temporarily unavailable");
    }
    return response;
  };
  globalThis.fetch = guarded;
  try { return await initialize(); }
  finally { if (globalThis.fetch === guarded) globalThis.fetch = original; }
}

export async function createFirebaseAuthProvider(config: FirebaseAuthConfig,
  resolveAccount?: typeof currentAccount) {
  return retainAuthDuringOutage(async () => {
    const auth = initializeAuth(initializeApp(config), {
      persistence: browserLocalPersistence, popupRedirectResolver: browserPopupRedirectResolver,
    });
    return new FirebaseAuthProvider(auth, resolveAccount).start();
  });
}
