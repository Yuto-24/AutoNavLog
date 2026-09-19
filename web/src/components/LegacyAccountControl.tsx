import { useEffect, useState, type ReactNode } from "react";
import { UserRound, X } from "lucide-react";
import { prepareLegacyLink, legacyLinkStatus, observeLegacyMigration, progressOf, type MigrationStatus } from "../legacyMigration";
import { CalculationProgressOverlay } from "./CalculationProgressOverlay";
import { useModalFocusTrap } from "../useModalFocusTrap";

export function LegacyAccountControl({ busy }: { busy: boolean }) {
  const [open, setOpen] = useState(false);
  const [linkOperation, setLinkOperation] = useState<() => Promise<MigrationStatus | undefined>>();
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<MigrationStatus>();
  const [error, setError] = useState<string>();
  const dialog = useModalFocusTrap<HTMLElement>(open);
  const prepare = async () => {
    try { const operation = await prepareLegacyLink(); setLinkOperation(() => operation); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "認証を準備できません。"); }
  };
  const link = async () => {
    if (!linkOperation) return;
    setPending(true); setError(undefined);
    try { const result = await linkOperation(); if (result) setStatus(result); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "紐付けに失敗しました。"); }
    finally { setPending(false); }
  };
  return <>
    <button type="button" className="icon-button header-icon-button" aria-label="アカウント" title="アカウント" onClick={() => {
      setOpen(true); setError(undefined); void prepare();
      void legacyLinkStatus().then(setStatus).catch(() => setError("この環境ではアカウント紐付けが設定されていません。"));
    }}><UserRound aria-hidden="true" size={18} /></button>
    {open && <div className="modal-backdrop"><section ref={dialog} className="modal-panel account-dialog" role="dialog" aria-modal="true" aria-labelledby="legacy-account-title" onKeyDown={event => { if (event.key === "Escape" && !pending) setOpen(false); }}>
      <div className="modal-heading"><h2 id="legacy-account-title">アカウント</h2><button type="button" className="icon-button" aria-label="アカウントを閉じる" disabled={pending} onClick={() => setOpen(false)}><X size={20} /></button></div>
      <p>現在のLegacyデータをGoogleアカウントに紐付けます。紐付け後も、この画面で編集・保存・計算を続けられます。</p>
      <p>NavMateへ初めてログインすると、その時点のProjectと計算結果を自動で引き継ぎます。引継ぎ中だけLegacyの操作を停止し、完了後はNavMateへ移動します。</p>
      {status && status.state !== "UNLINKED" ? <><p>紐付け済みです。</p><a href={status.navmateUrl}>NavMateを開く</a></> :
        <button type="button" className="primary-button" disabled={busy || pending || !status || !linkOperation} onClick={() => { void link(); }}>Googleアカウントを紐付ける</button>}
      {error && <p role="alert">{error}</p>}
    </section></div>}
  </>;
}

export function LegacyMigrationBoundary({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<MigrationStatus>();
  useEffect(() => observeLegacyMigration(setStatus), []);
  const blocked = status?.state === "MIGRATING" || status?.state === "NAVMATE_ACTIVE";
  return <><div inert={blocked}>{children}</div>{blocked && <CalculationProgressOverlay title="NavMateへ引継ぎ中" {...progressOf(status!)} />}</>;
}
