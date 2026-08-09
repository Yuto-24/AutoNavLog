import { X } from "lucide-react";
import { useEffect } from "react";
import { useModalFocusTrap } from "../useModalFocusTrap";

interface PasteDialogProps {
  open: boolean;
  value: string;
  busy: boolean;
  onChange: (value: string) => void;
  onClose: () => void;
  onImport: () => void;
}

export function PasteDialog({
  open,
  value,
  busy,
  onChange,
  onClose,
  onImport,
}: PasteDialogProps) {
  const dialogRef = useModalFocusTrap<HTMLElement>(open);

  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [busy, onClose, open]);

  if (!open) return null;
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={() => {
        if (!busy) onClose();
      }}
    >
      <section
        ref={dialogRef}
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="paste-dialog-title"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <h2 id="paste-dialog-title">KML/XMLを貼り付け</h2>
            <p>Google EarthからコピーしたKML全文を入力してください。</p>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label="閉じる"
            disabled={busy}
          >
            <X aria-hidden="true" size={20} />
          </button>
        </div>
        <textarea
          data-modal-autofocus
          value={value}
          onChange={(event) => onChange(event.target.value)}
          rows={16}
          spellCheck={false}
          placeholder="<?xml version=...><kml ...>"
        />
        <div className="modal-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={onClose}
            disabled={busy}
          >
            キャンセル
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={onImport}
            disabled={!value.trim() || busy}
          >
            貼付KMLを読み込む
          </button>
        </div>
      </section>
    </div>
  );
}
