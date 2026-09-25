import { FileUp } from "lucide-react";
import type { DragEvent, RefObject } from "react";
import type { FileContentSource } from "./platform";

export interface FileInputProps {
  busy: boolean;
  onFile: (file: FileContentSource) => void;
  inputRef?: RefObject<HTMLInputElement | null>;
}

// Browser-only presentation bridge. The composition root supplies this control;
// feature components and Importer receive content, never paths or DOM events.
export function BrowserFileInput({ busy, onFile, inputRef }: FileInputProps) {
  const acceptDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (busy) return;
    const file = event.dataTransfer.files[0];
    if (file) onFile(file);
  };
  return (
    <div
      className={`drop-zone ${busy ? "is-disabled" : ""}`}
      aria-disabled={busy}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = busy ? "none" : "copy";
      }}
      onDrop={acceptDrop}
    >
      <FileUp aria-hidden="true" size={26} />
      <strong>KML / KMZをドロップ</strong>
      <span>10 MiB以下</span>
      <label className="file-picker" htmlFor="route-file">
        ファイルを選択
      </label>
      <input
        ref={inputRef}
        id="route-file"
        className="visually-hidden"
        type="file"
        accept=".kml,.kmz,application/vnd.google-earth.kml+xml,application/vnd.google-earth.kmz"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
          event.target.value = "";
        }}
        disabled={busy}
      />
    </div>
  );
}
