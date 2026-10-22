import { useEffect } from "react";
import type { RouteCandidate } from "../types";
import { useModalFocusTrap } from "../useModalFocusTrap";

export function ImportCandidateDialog({ open, candidates, selected, busy, onSelect, onCancel, onConfirm }: {
  open: boolean; candidates: RouteCandidate[]; selected: string; busy: boolean;
  onSelect: (key: string) => void; onCancel: () => void; onConfirm: () => void;
}) {
  const ref = useModalFocusTrap<HTMLElement>(open);
  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onCancel(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [open, busy, onCancel]);
  if (!open) return null;
  return <div className="modal-backdrop" role="presentation" onMouseDown={() => { if (!busy) onCancel(); }}>
    <section ref={ref} className="modal-panel compact-modal" role="dialog" aria-modal="true"
      aria-labelledby="import-candidate-title" tabIndex={-1} onMouseDown={event => event.stopPropagation()}>
      <h2 id="import-candidate-title">経路選択</h2>
      <p>使用する飛行経路を選択してください。</p>
      <label className="field-group"><span>経路選択</span>
        <select data-modal-autofocus value={selected} disabled={busy} onChange={event => onSelect(event.target.value)}>
          <option value="">使用する飛行経路を選択してください。</option>
          {candidates.map((candidate, index) => <option key={`${candidate.kind}:${candidate.index}`} value={`${candidate.kind}:${candidate.index}`}>
            {index + 1}. {candidate.containerPath?.join(" / ") || candidate.name} · {candidate.vertexCount}点
          </option>)}
        </select>
      </label>
      <div className="modal-actions">
        <button className="secondary-button" disabled={busy} onClick={onCancel}>キャンセル</button>
        <button className="primary-button" disabled={busy || !candidates.some(candidate => `${candidate.kind}:${candidate.index}` === selected)} onClick={onConfirm}>KMLを取り込む</button>
      </div>
    </section>
  </div>;
}
