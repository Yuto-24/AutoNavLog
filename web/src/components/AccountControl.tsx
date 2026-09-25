import { useState, useSyncExternalStore } from "react";
import { UserRound, X } from "lucide-react";
import type { AuthProvider, AuthState } from "../auth";
import { useModalFocusTrap } from "../useModalFocusTrap";

const unavailable: AuthState = { account: null, available: false };
const noSubscribe = () => () => {};
export function AccountControl({ auth, busy, onBeforeChange }: { auth?: AuthProvider; busy: boolean; onBeforeChange?: () => boolean }) {
  const state = useSyncExternalStore(auth?.subscribe ?? noSubscribe, auth?.getState ?? (() => unavailable));
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialog = useModalFocusTrap<HTMLElement>(open);
  const act = async (operation: () => Promise<void>) => {
    if (onBeforeChange && !onBeforeChange()) return;
    setPending(true); setError(null);
    try { await operation(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "認証操作に失敗しました。"); }
    finally { setPending(false); }
  };
  return <>
    <button type="button" className="icon-button header-icon-button" aria-label="アカウント" title={state.account ? `アカウント（${state.account.displayName}）` : "アカウント（未ログイン）"} onClick={() => setOpen(true)}>
      <UserRound aria-hidden="true" size={18} />
    </button>
    {open && <div className="modal-backdrop">
      <section className="modal-panel account-dialog" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="account-title" onKeyDown={event => { if (event.key === "Escape") setOpen(false); }}>
        <div className="modal-heading"><h2 id="account-title">アカウント</h2>
          <button type="button" className="icon-button" aria-label="アカウントを閉じる" onClick={() => setOpen(false)}><X size={20} /></button>
        </div>
        <p>{state.account ? `${state.account.displayName} でログイン中` : "未ログイン・この端末のみで利用中"}</p>
        <p>Projectと最後の計算結果はこの端末に保存され、ログインすると同じアカウントの端末間で同期されます。</p>
        {state.account ? <p>ログアウト後も保存データは残ります。同じアカウントでログインすると再び開けます。</p>
          : <p>未ログイン中の保存データは、ログインしたアカウントへ取り込まれます。</p>}
        {!state.available && !state.notice && <p>この環境ではGoogleログインが設定されていません。未ログインのまま利用できます。</p>}
        {(error || state.notice) && <p role="alert">{error || state.notice}</p>}
        <button type="button" className="primary-button" disabled={!auth || !state.available || pending || (busy && !state.account && !state.signOutPending)}
          onClick={() => auth && void act((state.account || state.signOutPending) ? () => auth.signOut() : () => auth.signIn())}>
          {state.signOutPending ? "ログアウトを再試行" : state.account ? "ログアウト" : "Googleでログイン"}
        </button>
      </section>
    </div>}
  </>;
}


export function AccountNotice({ auth }: { auth?: AuthProvider }) {
  const state = useSyncExternalStore(auth?.subscribe ?? noSubscribe, auth?.getState ?? (() => unavailable));
  if (!state.notice) return null;
  return <div className="message-bar message-error" role="alert">
    <span>{state.notice}</span>
    {state.signOutPending && auth && <button type="button" onClick={() => { void auth.signOut().catch(() => {}); }}>ログアウトを再試行</button>}
  </div>;
}
