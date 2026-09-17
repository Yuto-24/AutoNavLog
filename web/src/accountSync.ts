import type { LocalProjectRecord } from "./localProjectRepository";

export type DeletionState = "ACTIVE" | "PENDING_DELETE" | "DELETED";
export interface SyncProject {
  id: string;
  version: string;
  revision: number; // Monotonic sync revision; independent of Project Save revision.
  record: LocalProjectRecord;
  latestDeviceId: string | null;
  deletion: DeletionState;
  undoUntil: number | null;
}
export interface SyncMutation { expectedVersion: string | null; value: SyncProject }
export type SyncCommit = { committed: true } | { committed: false; conflicts: SyncProject[] };
// One commit is atomic, including copy + original during conflict resolution.
// Versions are opaque CAS identities, independent of explicit Save revisions.
export interface AccountSyncRepository {
  list(): Promise<SyncProject[]>;
  commit(mutations: SyncMutation[]): Promise<SyncCommit>;
  subscribe(changed: () => void, failed: (error: unknown) => void): () => void;
  dispose(): void;
}
export type ConflictChoice = "local" | "remote" | "both";
export interface SyncStatus {
  conflicts: { id: string; name: string }[];
  imports: { id: string; name: string }[];
  undo: { id: string; name: string; until: number }[];
  notice?: string;
  generation: number;
}
export interface AccountSyncControl {
  getState(): SyncStatus;
  subscribe(listener: () => void): () => void;
  resolve(id: string, choice: ConflictChoice): Promise<string>;
  undo(id: string): Promise<void>;
  importLatest(id: string, name: string | null): Promise<void>;
}
export function deletionState(value: SyncProject, now = Date.now()): DeletionState {
  return value.deletion === "PENDING_DELETE" && value.undoUntil !== null && now >= value.undoUntil
    ? "DELETED" : value.deletion;
}
