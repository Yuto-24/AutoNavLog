import { IndexedDbProjectRepository } from "./localProjectRepository";
import { browserKeyValueStorage, requestPersistentStorage } from "./browserStorage";
import { PlatformError, type PlatformCapabilities } from "./platform";

export const browserPlatform: PlatformCapabilities = {
  files: {
    async read(source) {
      if (typeof source.arrayBuffer !== "function") {
        throw new PlatformError("file-read", "UNSUPPORTED", "この環境ではファイルを読み取れません。");
      }
      if (source.size > 10 * 1024 * 1024) {
        throw new PlatformError("file-read", "FAILED", "KML/KMZは10 MiB以下にしてください。");
      }
      return { name: source.name, mediaType: source.type, content: new Uint8Array(await source.arrayBuffer()) };
    },
    async save(file) {
      if (typeof document === "undefined" || typeof URL.createObjectURL !== "function") {
        throw new PlatformError("file-save", "UNSUPPORTED", "この環境ではファイルを保存できません。");
      }
      const link = document.createElement("a");
      if (!("download" in link)) throw new PlatformError("file-save", "UNSUPPORTED", "この環境ではファイルを保存できません。");
      const url = URL.createObjectURL(new Blob([file.content], { type: file.mediaType }));
      link.href = url;
      link.download = file.name;
      document.body.append(link);
      try { link.click(); }
      finally {
        link.remove();
        // Give the browser time to consume the download URL before releasing it.
        setTimeout(() => URL.revokeObjectURL(url), 30_000);
      }
    },
  },
  clipboard: {
    async readText() {
      if (!globalThis.navigator?.clipboard?.readText) {
        throw new PlatformError("clipboard-read", "UNSUPPORTED", "この環境ではクリップボードを読み取れません。");
      }
      try { return await navigator.clipboard.readText(); }
      catch { throw new PlatformError("clipboard-read", "FAILED", "クリップボードを読み取れませんでした。"); }
    },
    async writeText(text) {
      if (!globalThis.navigator?.clipboard?.writeText) {
        throw new PlatformError("clipboard-write", "UNSUPPORTED", "この環境ではクリップボードへコピーできません。");
      }
      try { await navigator.clipboard.writeText(text); }
      catch { throw new PlatformError("clipboard-write", "FAILED", "クリップボードへコピーできませんでした。"); }
    },
  },
  openExternalUrl(url) {
    const parsed = new URL(url);
    if (!["https:", "http:"].includes(parsed.protocol)) {
      throw new PlatformError("external-url", "FAILED", "このリンクを開けません。");
    }
    if (typeof document === "undefined") {
      throw new PlatformError("external-url", "UNSUPPORTED", "この環境では外部リンクを開けません。");
    }
    const link = document.createElement("a");
    link.href = parsed.href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.click();
  },
  networkAvailability() {
    const online = globalThis.navigator?.onLine;
    return typeof online === "boolean" ? online ? "online" : "offline" : "unknown";
  },
  session: {
    storage: browserKeyValueStorage("sessionStorage"),
    isReload: () => (globalThis.performance?.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined)?.type === "reload",
  },
  persistence: {
    values: browserKeyValueStorage("localStorage"),
    createProjectRepository: (validate, context) => new IndexedDbProjectRepository(validate, undefined, undefined, context),
    requestRetention: requestPersistentStorage,
  },
};
