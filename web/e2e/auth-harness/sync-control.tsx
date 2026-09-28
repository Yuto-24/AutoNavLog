import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { AccountSyncControl } from "../../src/components/AccountSyncControl";
import type { SyncStatus } from "../../src/accountSync";

// Exercise the production component with an immediately failing post-sync refresh.
export function mount(operation: "resolve" | "import", guard = false) {
  let state: SyncStatus = { conflicts: operation === "resolve" ? [{ id: "project", name: "Route" }] : [],
    imports: operation === "import" ? [{ id: "project", name: "Route" }] : [], undo: [], generation: 0 };
  const listeners = new Set<() => void>();
  const finish = async () => {
    state = { conflicts: [], imports: [], undo: [], generation: 1 };
    listeners.forEach(listener => listener());
    return "project";
  };
  const sync = { getState: () => state, subscribe: (listener: () => void) => { listeners.add(listener); return () => listeners.delete(listener); },
    resolve: finish, importLatest: finish, undo: async () => {} };
  const host = document.createElement("div"); document.body.append(host);
  createRoot(host).render(createElement(AccountSyncControl, { sync, onChange() {},
    onBeforeResolve: guard ? () => window.confirm("Route Draftを破棄しますか？") : undefined,
    onResolved: async () => { throw new Error("Projectの再読み込みに失敗しました"); } }));
}
