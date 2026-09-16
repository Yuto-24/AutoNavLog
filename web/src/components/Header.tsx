import { AccountControl } from "./AccountControl";
import type { AuthProvider } from "../auth";
import { localMode } from "../executionMode";
import { CheckCircle2, Info, FolderOpen, Plus, Save, Trash2 } from "lucide-react";
import type { SavedProject } from "../types";

interface HeaderProps {
  auth?: AuthProvider;
  appVersion: string;
  projectName: string;
  revision: number | null;
  savedProjects: SavedProject[];
  selectedProjectId: string;
  busy: boolean;
  informationUnread: boolean;
  knownIssuesUnread: boolean;
  onInformation: () => void;
  onProjectNameChange: (value: string) => void;
  onSelectedProjectIdChange: (value: string) => void;
  onLoad: () => void;
  onDelete: () => void;
  onSave: (name: string) => void;
  onNew: () => void;
}

export function Header({
  auth,
  appVersion,
  projectName,
  revision,
  savedProjects,
  selectedProjectId,
  busy,
  informationUnread,
  knownIssuesUnread,
  onInformation,
  onProjectNameChange,
  onSelectedProjectIdChange,
  onLoad,
  onDelete,
  onSave,
  onNew,
}: HeaderProps) {
  const normalizedProjectName = projectName.trim();

  return (
    <header className="app-header">
      <div className="brand-block">
        <div className="brand-name">AutoNavLog</div>
        <div className="brand-subtitle">NAV2 地上準備 / v{appVersion}{localMode ? " / Pyodide Local PoC" : ""}</div>
      </div>
      <div className="header-project">
        <label className="header-project-label" htmlFor="project-name">
          プロジェクト
        </label>
        <input
          id="project-name"
          className="header-project-name"
          type="text"
          value={projectName}
          maxLength={60}
          disabled={revision === null || busy}
          aria-invalid={revision !== null && normalizedProjectName.length === 0}
          onChange={(event) => onProjectNameChange(event.target.value)}
        />
        {revision !== null && <span className="header-revision">rev.{revision}</span>}
      </div>
      <div className="header-actions">
        {localMode && <AccountControl auth={auth} busy={busy} />}
        <button
          className={`header-button information-button${knownIssuesUnread ? " information-warning" : ""}`}
          type="button"
          onClick={onInformation}
          aria-label={knownIssuesUnread ? "Information（既知の不具合に更新があります）" : informationUnread ? "Information（未読の更新があります）" : "Information"}
        >
          <span className="information-icon-wrap"><Info aria-hidden="true" size={18} />{informationUnread && <span className="information-unread-dot" />}</span>
          <span className="information-label">Information</span>
        </button>
        <div className="storage-state" aria-label={localMode ? "保存先はこの端末のブラウザです" : "保存先はローカルです"}>
          <CheckCircle2 aria-hidden="true" size={17} />
          <span>{localMode ? "端末内に保存" : "ローカル保存"}</span>
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
                {project.kind === "LATEST" ? "Latest" : project.name}
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
          className="header-button header-button-primary header-save-button"
          aria-label="保存"
          title="保存"
          type="button"
          onClick={() => onSave(normalizedProjectName)}
          disabled={revision === null || busy || normalizedProjectName.length === 0}
        >
          <Save aria-hidden="true" size={18} />
          <span className="header-action-label">保存</span>
        </button>
        <button className="header-button header-new-button" aria-label="新規" title="新規" type="button" onClick={onNew} disabled={busy}>
          <Plus aria-hidden="true" size={18} />
          <span className="header-action-label">新規</span>
        </button>
      </div>
    </header>
  );
}
