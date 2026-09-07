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
  date: string;
  summary: ReleaseBlock[];
  sections: Array<{ title: string; blocks: ReleaseBlock[] }>;
}

export interface InformationSnapshot {
  id: string;
  releases: ReleaseNote[];
  // Future Information entries (for example, notices) participate in the update ID.
  [entryType: string]: unknown;
}
export interface InformationData {
  information: InformationSnapshot;
  compatibility: { legacyReleaseInformationIds: Record<string, string> };
}

const lastSeenUpdateKey = "autonavlog.information.lastSeenUpdate";
const legacyLastSeenReleaseKey = "autonavlog.information.lastSeenRelease";
let inMemoryLastSeenUpdate: string | null = null;
let storageUnavailable = false;

function readStored(key: string): string | null {
  if (storageUnavailable) return null;
  try { return window.localStorage.getItem(key); } catch { storageUnavailable = true; return null; }
}

function writeCurrentUpdate(updateId: string): void {
  inMemoryLastSeenUpdate = updateId;
  try { window.localStorage.setItem(lastSeenUpdateKey, updateId); } catch { storageUnavailable = true; }
}

export function hasUnreadInformation(data: InformationData): boolean {
  const updateId = inMemoryLastSeenUpdate ?? readStored(lastSeenUpdateKey);
  if (updateId !== null) return updateId !== data.information.id;
  const legacyRelease = readStored(legacyLastSeenReleaseKey);
  if (canMigrateLegacyRelease(legacyRelease, data)) {
    writeCurrentUpdate(data.information.id);
    return false;
  }
  return true;
}

export function canMigrateLegacyRelease(
  legacyRelease: string | null,
  data: InformationData,
): boolean {
  return legacyRelease !== null
    && data.compatibility.legacyReleaseInformationIds[legacyRelease] === data.information.id;
}

export function markInformationSeen(data: InformationData): void {
  writeCurrentUpdate(data.information.id);
}
