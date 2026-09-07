import { X } from "lucide-react";
import { useEffect } from "react";
import type { KnownIssue, ReleaseBlock, ReleaseNote } from "../releaseNotes";
import { useModalFocusTrap } from "../useModalFocusTrap";

function InlineText({ text }: { text: string }) {
  return <>{text.split(/(`[^`]+`)/g).map((part, index) => part.startsWith("`") && part.endsWith("`")
    ? <code key={index}>{part.slice(1, -1)}</code> : part)}</>;
}

function ReleaseBlocks({ blocks }: { blocks: ReleaseBlock[] }) {
  return <>{blocks.map((block, index) => block.kind === "paragraph" ? (
    <p key={index}><InlineText text={block.text} /></p>
  ) : (
    <ul key={index}>{block.items.map((item, itemIndex) => <li key={itemIndex}><InlineText text={item.text} /></li>)}</ul>
  ))}</>;
}

interface InformationDialogProps {
  open: boolean;
  releases: ReleaseNote[];
  knownIssues: KnownIssue[];
  onClose: () => void;
  onOpened: () => void;
}

export function InformationDialog({ open, releases, knownIssues, onClose, onOpened }: InformationDialogProps) {
  const dialogRef = useModalFocusTrap<HTMLElement>(open);
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open]);
  useEffect(() => {
    if (open) onOpened();
  }, [onOpened, open]);
  if (!open) return null;
  return <div
    className="modal-backdrop"
    role="presentation"
    onMouseDown={(event) => {
      event.preventDefault();
      onClose();
    }}
  >
    <section ref={dialogRef} className="modal-panel information-dialog" role="dialog" aria-modal="true" aria-labelledby="information-dialog-title" tabIndex={-1} onMouseDown={(event) => event.stopPropagation()}>
      <div className="modal-heading">
        <div><h2 id="information-dialog-title">Information</h2><p>AutoNavLog のお知らせ</p></div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Informationを閉じる" data-modal-autofocus><X aria-hidden="true" size={20} /></button>
      </div>
      {knownIssues.length > 0 && <section className="information-known-issues" aria-labelledby="known-issues-heading">
        <h3 id="known-issues-heading">既知の不具合</h3>
        {knownIssues.map((issue) => <article key={issue.id} className="information-known-issue">
          <h4>{issue.title}</h4>
          <p>{issue.description.join("\n")}</p>
          {issue.sections.map((section) => <section key={section.title}>
            <h5>{section.title}</h5><ul>{section.items.map((item, index) => <li key={index}>{item}</li>)}</ul>
          </section>)}
        </article>)}
      </section>}
      <h3 className="information-history-heading">更新履歴</h3>
      <div className="information-release-list">
        {releases.map((release) => <article key={release.version} className="information-release">
          <header><h3>v{release.version}</h3><time dateTime={release.dateTime ?? release.date}>{release.date}</time></header>
          <ReleaseBlocks blocks={release.summary} />
          {release.sections.map((section) => <section key={section.title}><h4>{section.title}</h4><ReleaseBlocks blocks={section.blocks} /></section>)}
        </article>)}
      </div>
    </section>
  </div>;
}
