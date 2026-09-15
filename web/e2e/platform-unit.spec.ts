import { expect, test } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { browserPlatform } from "../src/browserPlatform";
import { routeImportInput } from "../src/fileInput";
import { clearSession, readSession, writeSession, SESSION_KEY, type ApplicationSession } from "../src/applicationSession";
import { createInformationState } from "../src/releaseNotes";
import { IndexedDbProjectRepository } from "../src/localProjectRepository";
import { exportNavLog } from "../src/navLogExport";
import type { WebState } from "../src/types";

const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
test.afterEach(() => {
  if (navigatorDescriptor) Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
  else Reflect.deleteProperty(globalThis, "navigator");
});
const navigatorValue = (value: unknown) => Object.defineProperty(globalThis, "navigator", { configurable: true, value });

test("binary content and metadata reach Importer without paths or UTF-8 conversion", async () => {
  const bytes = Uint8Array.from({ length: 100_000 }, (_, i) => i % 256);
  const file = await browserPlatform.files.read({ name: "日本語.kmz", type: "application/vnd.google-earth.kmz", size: bytes.length,
    arrayBuffer: async () => bytes.buffer });
  const input = routeImportInput(file);
  expect(input.filename).toBe("日本語.kmz");
  expect(Buffer.from(input.content_base64, "base64")).toEqual(Buffer.from(bytes));
  expect(Object.keys(input).sort()).toEqual(["content_base64", "filename"]);
  let read = false;
  await expect(browserPlatform.files.read({ name: "large.kml", type: "", size: 10 * 1024 * 1024 + 1,
    arrayBuffer: async () => { read = true; return bytes.buffer; } })).rejects.toMatchObject({ code: "FAILED", capability: "file-read" });
  expect(read).toBe(false);
  await expect(browserPlatform.files.read({ name: "file", type: "", size: 0 } as any)).rejects.toMatchObject({ code: "UNSUPPORTED" });
});

test("Clipboard has explicit unavailable/denied results and does not replace content", async () => {
  navigatorValue({});
  await expect(browserPlatform.clipboard.readText()).rejects.toMatchObject({ code: "UNSUPPORTED", capability: "clipboard-read" });
  await expect(browserPlatform.clipboard.writeText("text")).rejects.toMatchObject({ code: "UNSUPPORTED", capability: "clipboard-write" });
  navigatorValue({ clipboard: { readText: () => Promise.reject(new Error("denied")), writeText: () => Promise.reject(new Error("denied")) } });
  await expect(browserPlatform.clipboard.readText()).rejects.toMatchObject({ code: "FAILED" });
  await expect(browserPlatform.clipboard.writeText("text")).rejects.toMatchObject({ code: "FAILED" });
  let written = "";
  navigatorValue({ clipboard: { readText: async () => "", writeText: async (text: string) => { written = text; } } });
  expect(await browserPlatform.clipboard.readText()).toBe("");
  await browserPlatform.clipboard.writeText("日本語\nKML");
  expect(written).toBe("日本語\nKML");
});

test("network is a hint and persistence retention is best effort", async () => {
  for (const [value, expected] of [[true, "online"], [false, "offline"], [undefined, "unknown"]]) {
    navigatorValue({ onLine: value });
    expect(browserPlatform.networkAvailability()).toBe(expected);
    expect(() => browserPlatform.persistence.requestRetention()).not.toThrow();
  }
  navigatorValue({ storage: { persist: () => Promise.reject(new Error("denied")) } });
  browserPlatform.persistence.requestRetention();
  await Promise.resolve();
  expect(browserPlatform.persistence.createProjectRepository(async record => record as any)).toBeInstanceOf(IndexedDbProjectRepository);
});

test("Session policy and Information state operate on injected storage, without owning Project data", () => {
  const values = new Map([["unrelated", "keep"], [SESSION_KEY, "old"]]);
  const storage = { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
  expect(readSession({ storage, isReload: () => false })).toEqual({});
  expect(values.has(SESSION_KEY)).toBe(false);
  values.set(SESSION_KEY, "malformed");
  expect(readSession({ storage, isReload: () => true })).toEqual({ failed: true });
  expect(values.get("unrelated")).toBe("keep");
  const denied = { ...storage, setItem: () => { throw new Error("denied"); }, removeItem: () => { throw new Error("denied"); } };
  expect(writeSession({} as ApplicationSession, denied)).toBe(false);
  expect(() => clearSession(denied)).toThrow("denied");
  const information = createInformationState(denied);
  const data = { information: { id: "one", releases: [] }, compatibility: { legacyReleaseInformationIds: {} } };
  expect(information.hasUnreadInformation(data)).toBe(true);
  information.markInformationSeen(data);
  expect(information.hasUnreadInformation(data)).toBe(false);
  expect(values.get("unrelated")).toBe("keep");
});

test("result export retains the calculation and never labels it with a newer Project draft", () => {
  const outcome = { summary: { label: "日本語" }, forecast_run: "original" };
  const state = { outcome, project: { name: "edited since calculation" }, destinationWind: null } as unknown as WebState;
  const result = exportNavLog(state);
  expect(JSON.parse(new TextDecoder().decode(result.content))).toEqual({ format: "autonavlog.navlog", version: 1, outcome, destinationWind: null });
  expect(() => exportNavLog({ ...state, outcome: null })).toThrow();
});

test("missing Browser file save / URL support is explicit, and script URLs are rejected", async () => {
  await expect(browserPlatform.files.save({ name: "result.json", mediaType: "application/json", content: new Uint8Array() })).rejects.toMatchObject({ code: "UNSUPPORTED" });
  expect(() => browserPlatform.openExternalUrl("https://example.com")).toThrow(/外部リンク/);
  expect(() => browserPlatform.openExternalUrl("javascript:alert(1)")).toThrow(/リンク/);
});

test("features have no Browser storage/clipboard or file API branches; core stays independent", () => {
  const files = ["App.tsx", "applicationSession.ts", "releaseNotes.ts", "application.ts", "localApplication.ts",
    ...readdirSync(resolve("src/components")).filter(name => name.endsWith(".tsx")).map(name => `components/${name}`)];
  for (const file of files) {
    const source = readFileSync(resolve("src", file), "utf8").replace(/\/\/[^\n]*/g, "");
    expect(source, file).not.toMatch(/navigator\.|(?:window\.)?(?:sessionStorage|localStorage)\.|\bindexedDB\b|new FileReader|\.createObjectURL\(|\.arrayBuffer\(/);
  }
  for (const file of ["localClient.ts", "local.worker.ts"]) {
    expect(readFileSync(resolve("src", file), "utf8")).not.toMatch(/from ["']\.\/platform/);
  }
});
