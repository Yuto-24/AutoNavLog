import { CheckCircle2, FolderOpen, Plus, Save, Trash2 } from "lucide-react";
import type { SavedProject } from "../types";

interface HeaderProps {
  appVersion: string;
  projectName: string;
  revision: number | null;
  savedProjects: SavedProject[];
  selectedProjectId: string;
  busy: boolean;
  onSelectedProjectIdChange: (value: string) => void;
  onLoad: () => void;
  onDelete: () => void;
  onSave: () => void;
  onNew: () => void;
}

export function Header({
  appVersion,
  projectName,
  revision,
  savedProjects,
  selectedProjectId,
  busy,
  onSelectedProjectIdChange,
  onLoad,
  onDelete,
  onSave,
  onNew,
}: HeaderProps) {
  return (
    <header className="app-header">
      <div className="brand-block">
        <div className="brand-name">AutoNavLog</div>
        <div className="brand-subtitle">NAV2 地上準備 / v{appVersion}</div>
      </div>
      <div className="header-project">
        <span className="header-project-label">プロジェクト</span>
        <strong>{projectName}</strong>
        {revision !== null && <span className="header-revision">rev.{revision}</span>}
      </div>
      <div className="header-spacer" />
      <div className="storage-state" aria-label="保存先はローカルです">
        <CheckCircle2 aria-hidden="true" size={17} />
        <span>ローカル保存</span>
      </div>
      <div className="saved-project-control">
        <label htmlFor="saved-project">保存済み</label>
        <select
          id="saved-project"
          value={selectedProjectId}
          onChange={(event) => onSelectedProjectIdChange(event.target.value)}
        >
          <option value="">選択</option>
          {savedProjects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
        <button
          className="icon-button header-icon-button"
          type="button"
          onClick={onLoad}
          disabled={!selectedProjectId || busy}
          aria-label="保存済みProjectを開く"
          title="保存済みProjectを開く"
        >
          <FolderOpen aria-hidden="true" size={18} />
        </button>
        <button
          className="icon-button header-icon-button"
          type="button"
          onClick={onDelete}
          disabled={!selectedProjectId || busy}
          aria-label="保存済みProjectを削除"
          title="保存済みProjectを削除"
        >
          <Trash2 aria-hidden="true" size={18} />
        </button>
      </div>
      <button
        className="header-button header-button-primary"
        type="button"
        onClick={onSave}
        disabled={revision === null || busy}
      >
        <Save aria-hidden="true" size={18} />
        保存
      </button>
      <button className="header-button" type="button" onClick={onNew} disabled={busy}>
        <Plus aria-hidden="true" size={18} />
        新規
      </button>
    </header>
  );
}
