import type { KeyValueStorage } from "./platform";
export interface ReleaseTextBlock {
  kind: "paragraph";
  text: string;
}

export interface ReleaseListBlock {
  kind: "list";
  items: Array<{ text: string }>;
}

export type ReleaseBlock = ReleaseTextBlock | ReleaseListBlock;

export interface ReleaseNote {
  version: string;
  summary: ReleaseBlock[];
  sections: Array<{ title: string; blocks: ReleaseBlock[] }>;
}

export interface KnownIssue {
  id: string;
  bodyHash: string;
  title: string;
  description: string[];
  sections: Array<{ title: string; items: string[] }>;
}

export interface InformationSnapshot {
  id: string;
  releases: ReleaseNote[];
  knownIssues?: KnownIssue[];
  knownIssuesId?: string;
  // Future Information entries (for example, notices) participate in the update ID.
  [entryType: string]: unknown;
}
export interface InformationData {
  information: InformationSnapshot;
  compatibility: { legacyReleaseInformationIds: Record<string, string> };
}

export function createInformationState(storage: KeyValueStorage) {
  const lastSeenUpdateKey = "autonavlog.information.lastSeenUpdate";
  const legacyLastSeenReleaseKey = "autonavlog.information.lastSeenRelease";
  let inMemoryLastSeenUpdate: string | null = null;
  let storageUnavailable = false;
  const knownSeenKey = "autonavlog.information.knownIssuesSeen";
  interface KnownSeen { knownIssuesId: string; issues: Array<{ id: string; bodyHash: string }> }
  let inMemoryKnownSeen: KnownSeen | null = null;

  function knownSeen(): KnownSeen | null {
    if (inMemoryKnownSeen) return inMemoryKnownSeen;
    try {
      const value: unknown = JSON.parse(readStored(knownSeenKey) ?? "null");
      if (!value || typeof value !== "object" || !("knownIssuesId" in value)
        || typeof value.knownIssuesId !== "string" || !("issues" in value) || !Array.isArray(value.issues)
        || !value.issues.every((issue: unknown) => issue && typeof issue === "object"
          && "id" in issue && typeof issue.id === "string"
          && "bodyHash" in issue && typeof issue.bodyHash === "string")) return null;
      return value as KnownSeen;
    } catch { return null; }
  }

  function hasUnreadKnownIssues(data: InformationData): boolean {
    const current = data.information.knownIssues ?? [];
    if (!current.length) return false;
    const seen = knownSeen();
    if (!seen) return true;
    // Management-only ID changes do not notify when the visible body is unchanged.
    // Match exact IDs first so swapping IDs cannot hide changes to an existing issue.
    const used = new Set<number>();
    const unmatched = current.filter((issue) => {
      const index = seen.issues.findIndex((old) => old.id === issue.id);
      if (index < 0) return true;
      used.add(index);
      return seen.issues[index]?.bodyHash !== issue.bodyHash;
    });
    return unmatched.some((issue) => {
      if (seen.issues.some((old) => old.id === issue.id)) return true;
      const index = seen.issues.findIndex((old, i) => !used.has(i) && old.bodyHash === issue.bodyHash);
      if (index < 0) return true;
      used.add(index);
      return false;
    });
  }

  function readStored(key: string): string | null {
    if (storageUnavailable) return null;
    try { return storage.getItem(key); } catch { storageUnavailable = true; return null; }
  }

  function writeCurrentUpdate(updateId: string): void {
    inMemoryLastSeenUpdate = updateId;
    try { storage.setItem(lastSeenUpdateKey, updateId); } catch { storageUnavailable = true; }
  }

  function hasUnreadInformation(data: InformationData): boolean {
    if (hasUnreadKnownIssues(data)) return true;
    const updateId = inMemoryLastSeenUpdate ?? readStored(lastSeenUpdateKey);
    if (updateId !== null) return updateId !== data.information.id;
    const legacyRelease = readStored(legacyLastSeenReleaseKey);
    if (canMigrateLegacyRelease(legacyRelease, data)) {
      writeCurrentUpdate(data.information.id);
      return false;
    }
    return true;
  }

  function markInformationSeen(data: InformationData): void {
    writeCurrentUpdate(data.information.id);
    inMemoryKnownSeen = {
      knownIssuesId: data.information.knownIssuesId ?? "",
      issues: (data.information.knownIssues ?? []).map(({ id, bodyHash }) => ({ id, bodyHash })),
    };
    try { storage.setItem(knownSeenKey, JSON.stringify(inMemoryKnownSeen)); }
    catch { storageUnavailable = true; }
  }

  return { hasUnreadInformation, hasUnreadKnownIssues, markInformationSeen };
}

export function canMigrateLegacyRelease(
  legacyRelease: string | null,
  data: InformationData,
): boolean {
  return legacyRelease !== null
    && data.compatibility.legacyReleaseInformationIds[legacyRelease] === data.information.id;
}
