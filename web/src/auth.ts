import { ApplicationError } from "./application";

export interface Account { account_id: string; displayName: string }
export interface AuthState { account: Account | null; available: boolean; notice?: string; signOutPending?: boolean; deletionPending?: boolean }
// Authentication is independent of Project storage and future Account Sync.
export interface AuthProvider {
  getState(): AuthState;
  subscribe(listener: () => void): () => void;
  signIn(): Promise<void>;
  signOut(): Promise<void>;
  deleteAccount?(): Promise<void>;
}
export const accountBoundaryClosed = () => new ApplicationError(
  "ログイン状態が変わりました。現在のアカウントで開き直してください。", "ACCOUNT_CONTEXT_CLOSED");
