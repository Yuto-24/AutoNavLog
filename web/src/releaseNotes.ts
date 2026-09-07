export interface ReleaseTextBlock {
  kind: "paragraph";
  text: string;
  line: number;
}

export interface ReleaseListBlock {
  kind: "list";
  items: Array<{ text: string; line: number }>;
}

export type ReleaseBlock = ReleaseTextBlock | ReleaseListBlock;

export interface ReleaseNote {
  version: string;
  date: string;
  summary: ReleaseBlock[];
  sections: Array<{ title: string; blocks: ReleaseBlock[] }>;
}

const lastSeenKey = "autonavlog.information.lastSeenRelease";
let inMemoryLastSeen: string | null = null;
let storageUnavailable = false;

function readStoredLastSeen(): string | null {
  if (storageUnavailable || inMemoryLastSeen !== null) return inMemoryLastSeen;
  try { return window.localStorage.getItem(lastSeenKey); } catch { storageUnavailable = true; return inMemoryLastSeen; }
}

function writeStoredLastSeen(version: string): void {
  inMemoryLastSeen = version;
  try { window.localStorage.setItem(lastSeenKey, version); } catch { storageUnavailable = true; }
}

export function hasUnreadRelease(releases: ReleaseNote[]): boolean {
  const seen = readStoredLastSeen();
  if (!seen) return releases.length > 0;
  const seenIndex = releases.findIndex((release) => release.version === seen);
  return seenIndex !== 0;
}

export function markLatestReleaseSeen(releases: ReleaseNote[]): void {
  if (releases[0]) writeStoredLastSeen(releases[0].version);
}
