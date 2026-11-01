import { useEffect, useRef, useState, type ReactNode } from "react";
import { UserRound, X } from "lucide-react";
import { prepareLegacyLink, legacyLinkStatus, observeLegacyMigration, progressOf, type MigrationStatus } from "../legacyMigration";
import { CalculationProgressOverlay } from "./CalculationProgressOverlay";
import { useModalFocusTrap } from "../useModalFocusTrap";

function LegacyAccountDialog({ busy, notice = false, onClose }: { busy: boolean; notice?: boolean; onClose: () => void }) {
  const [linkOperation, setLinkOperation] = useState<(signal?: AbortSignal) => Promise<MigrationStatus | undefined>>();
  const [pending, setPending] = useState(false);
  const [status, setStatus] = useState<MigrationStatus>();
  const [error, setError] = useState<string>();
  const operation = useRef<AbortController | null>(null);
  const dialog = useModalFocusTrap<HTMLElement>(true);
  useEffect(() => {
    let active = true;
    void prepareLegacyLink().then(operation => { if (active) setLinkOperation(() => operation); })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "認証を準備できません。"); });
    void legacyLinkStatus().then(result => { if (active) setStatus(result); })
      .catch(() => { if (active) setError("この環境ではアカウント紐付けが設定されていません。"); });
    return () => { active = false; operation.current?.abort(); };
  }, []);
  const link = async () => {
    if (!linkOperation || pending) return;
    const controller = new AbortController();
    operation.current = controller;
    setPending(true); setError(undefined);
    try { const result = await linkOperation(controller.signal); if (!controller.signal.aborted && result) setStatus(result); }
    catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "紐付けに失敗しました。"); }
    finally { if (!controller.signal.aborted) setPending(false); }
  };
  return <div className={notice ? "modal-backdrop legacy-migration-notice" : "modal-backdrop"}>
    <section ref={dialog} tabIndex={-1} className="modal-panel account-dialog" role="dialog" aria-modal="true" aria-labelledby="legacy-account-title" onKeyDown={event => { if (event.key === "Escape" && !pending && !notice) onClose(); }}>
      <div className="modal-heading"><h2 id="legacy-account-title">{notice ? "NavMate" : "アカウント"}</h2>{!notice && <button type="button" className="icon-button" aria-label="アカウントを閉じる" disabled={pending} onClick={onClose}><X size={20} /></button>}</div>
      {notice && <p>旧画面は12月を目途に停止する</p>}
      <p>現在のデータをGoogleアカウントに紐付けます。紐付け後も、引き続きこの画面で編集・保存・計算が可能です。</p>
      <p>NavMateへの初回ログイン時に、その時点のプロジェクトと計算結果を自動で引き継ぎます。引継ぎ中は現在の画面操作を一時停止し、完了後にNavMateへ移動します。</p>
      {status && status.state !== "UNLINKED" ? <><p>紐付け済みです。</p><a className={notice ? "primary-button" : undefined} href={status.navmateUrl}>NavMateを開く</a></> :
        <button type="button" className="primary-button" disabled={busy || pending || !status || !linkOperation} onClick={() => { void link(); }}>Googleアカウントを紐付ける</button>}
      {error && <p role="alert">{error}</p>}
      {notice && <button type="button" className="legacy-migration-dismiss" onClick={onClose}>今回は旧画面を使う</button>}
    </section>
  </div>;
}

export function LegacyAccountControl({ busy }: { busy: boolean }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className="icon-button header-icon-button" aria-label="アカウント" title="アカウント" onClick={() => setOpen(true)}><UserRound aria-hidden="true" size={18} /></button>
    {open && <LegacyAccountDialog busy={busy} onClose={() => setOpen(false)} />}
  </>;
}

export function LegacyMigrationBoundary({ children }: { children: ReactNode }) {
  // Deliberately memory-only: each new page load offers migration again.
  const [dismissed, setDismissed] = useState(false);
  const [status, setStatus] = useState<MigrationStatus>();
  useEffect(() => observeLegacyMigration(setStatus), []);
  const blocked = status?.state === "MIGRATING" || status?.state === "NAVMATE_ACTIVE";
  return <><div inert={blocked || !dismissed}>{children}</div>{!blocked && !dismissed && <LegacyAccountDialog notice busy={false} onClose={() => setDismissed(true)} />}{blocked && <CalculationProgressOverlay title="NavMateへ引継ぎ中" {...progressOf(status!)} />}</>;
}
