import type { LocalProjectRepositoryFactory } from "./localProjectRepository";

export interface PlatformFile {
  name: string;
  mediaType: string;
  content: Uint8Array<ArrayBuffer>;
}
// A content source, never a filesystem path. Browser File implements this shape.
export interface FileContentSource {
  name: string;
  type: string;
  size: number;
  arrayBuffer(): Promise<ArrayBuffer>;
}
export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
export class PlatformError extends Error {
  constructor(readonly capability: string, readonly code: "UNSUPPORTED" | "FAILED", message: string) {
    super(message);
    this.name = "PlatformError";
  }
}
export interface PlatformCapabilities {
  files: {
    read(source: FileContentSource): Promise<PlatformFile>;
    save(file: PlatformFile): Promise<void>;
  };
  clipboard: { readText(): Promise<string>; writeText(text: string): Promise<void> };
  openExternalUrl(url: string): void;
  // A connectivity hint only: online does not guarantee a reachable weather service.
  networkAvailability(): "online" | "offline" | "unknown";
  session: { storage: KeyValueStorage; isReload(): boolean };
  persistence: {
    values: KeyValueStorage;
    createProjectRepository: LocalProjectRepositoryFactory;
    requestRetention(): void;
  };
}
