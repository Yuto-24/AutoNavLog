import { expect, test } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { ApplicationError } from "../src/application";
import type { AutoNavLogApplication, UpdateProjectInput } from "../src/application";
import type { WebState } from "../src/types";
import { LegacyApplication } from "../src/legacyApplication";
import { LocalApplication } from "../src/localApplication";

const originalFetch = globalThis.fetch;
test.afterEach(() => { globalThis.fetch = originalFetch; });
const state = { project: null, outcome: null, savedProjects: [] } as unknown as WebState;
const update: UpdateProjectInput = {
  flight_date: "2026-09-11", departure_time_jst: "09:00",
  total_usable_fuel_gal: 90, default_variation_deg_east: 8,
};
function responses(...items: Response[]) {
  const calls: { path: string; options?: RequestInit }[] = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path: String(path), options });
    const response = items.shift();
    if (!response) throw new Error("Unexpected fetch");
    return response;
  };
  return calls;
}
function json(value: unknown, status = 200) { return Response.json(value, { status }); }

test("UI and application contract have no transport dependencies", () => {
  const source = resolve("src");
  const uiFiles = ["App.tsx", ...readdirSync(source).filter(name => /^use.*\.tsx?$/.test(name)),
    ...readdirSync(resolve(source, "components")).filter(name => /\.tsx?$/.test(name)).map(name => `components/${name}`)];
  for (const name of [...uiFiles, "application.ts"]) {
    const text = readFileSync(resolve(source, name), "utf8");
    expect(text, name).not.toMatch(/\/api\/|credentials|document\.cookie|\b401\b|new Worker|comlink|localClient|legacyApplication|localApplication|FastAPI|fetch\(/);
  }
  expect(readFileSync(resolve(source, "application.ts"), "utf8")).not.toMatch(/\bstatus\s*:|RequestInit|Response|\brequest\s*[<(]/);
});

test("bootstrap shares initialization and contains session creation", async () => {
  const calls = responses(json({}, 401), json({ state }));
  const app: AutoNavLogApplication = new LegacyApplication();
  expect(await Promise.all([app.bootstrap(), app.bootstrap()])).toEqual([state, state]);
  expect(calls.map(call => call.path)).toEqual(["/api/state", "/api/session"]);
  expect(calls.every(call => call.options?.credentials === "same-origin")).toBe(true);
});

test("Legacy retries an operation once and does not expose HTTP status", async () => {
  const calls = responses(json({}, 401), json({ state }), json({ error: { code: "SESSION_REQUIRED", message: "session missing" } }, 401));
  const app: AutoNavLogApplication = new LegacyApplication();
  await expect(app.updateProject(update)).rejects.toMatchObject({ code: "SESSION_REQUIRED", message: "session missing" });
  expect(calls.map(call => call.path)).toEqual(["/api/project", "/api/session", "/api/project"]);
  expect(calls[0].options?.body).toBe(calls[2].options?.body);
  responses(json({ error: { code: "PROJECT_REQUIRED", message: "先に経路を確定してください。" } }, 400));
  const error = await app.calculate().catch(error => error);
  expect(error).toBeInstanceOf(ApplicationError);
  expect(error).not.toHaveProperty("status");
});

test("Legacy retains application details and normalizes validation failures", async () => {
  responses(json({ error: { code: "KMZ_DOCUMENT_SELECTION_REQUIRED", message: "select", candidates: ["a.kml", "b.kml"] } }, 409));
  const app = new LegacyApplication();
  await expect(app.importRoute({ filename: "a.kmz", content_base64: "" })).rejects.toMatchObject({ code: "KMZ_DOCUMENT_SELECTION_REQUIRED", details: { candidates: ["a.kml", "b.kml"] } });
  responses(json({ detail: [{ loc: ["body", "flight_date"], msg: "Field required", type: "missing", input: {} }] }, 422));
  await expect(app.updateProject(update)).rejects.toMatchObject({ code: "VALIDATION_FAILED", message: "入力内容を確認してください。", details: { issues: [{ location: ["flight_date"], message: "Field required", type: "missing" }] } });
  responses(json({ detail: [{ loc: ["path", "node_id"], msg: "Invalid UUID", type: "uuid_parsing" }] }, 422));
  await expect(app.renameRouteNode("invalid", "name")).rejects.toMatchObject({ code: "VALIDATION_FAILED", details: { issues: [{ location: ["node_id"], message: "Invalid UUID", type: "uuid_parsing" }] } });
  responses(new Response("gateway internals", { status: 502, statusText: "Internal Gateway" }));
  await expect(app.bootstrap()).rejects.toMatchObject({ code: "REQUEST_FAILED", message: "処理に失敗しました。" });
  responses(new Response("invalid success JSON"));
  await expect(app.bootstrap()).rejects.toBeInstanceOf(ApplicationError);
});

test("Legacy keeps update/recalculate atomic and maps all project operations", async () => {
  const calls = responses(...Array.from({ length: 6 }, () => json(state)), new Response(null, { status: 204 }));
  const app: AutoNavLogApplication = new LegacyApplication();
  await app.updateAndRecalculate(update);
  await app.saveProject("name");
  await app.loadProject("project");
  await app.deleteProject("project/a");
  await app.renameRouteNode("node/a", "renamed");
  await app.acknowledge("key/a", true);
  await app.newWork();
  expect(calls.map(call => [call.path, call.options?.method])).toEqual([
    ["/api/project/recalculate", "POST"], ["/api/projects/save", "POST"],
    ["/api/projects/load", "POST"], ["/api/projects/project%2Fa", "DELETE"],
    ["/api/project/route-nodes/node%2Fa/name", "PUT"], ["/api/acknowledgements/key%2Fa", "PUT"],
    ["/api/session", "DELETE"],
  ]);
  expect(JSON.parse(calls[0].options!.body as string)).toEqual(update);
});

test("Legacy calculation progress and job failure retain application meaning", async () => {
  responses(json({ job_id: "job", status: "calculating", progress_percent: 30, progress_message: "calculating" }),
    json({ status: "succeeded", state, progress_percent: 100, progress_message: "done" }));
  const progress: number[] = [];
  expect(await new LegacyApplication().calculate(value => progress.push(value.percent))).toEqual(state);
  expect(progress).toEqual([30, 100]);
  responses(json({ status: "failed", error: { code: "PROJECT_REQUIRED", message: "missing", status: 400 } }));
  const error = await new LegacyApplication().calculate().catch(error => error);
  expect(error).toMatchObject({ code: "PROJECT_REQUIRED", message: "missing", details: {} });
  expect(error).not.toHaveProperty("status");
});

test("Local uses common operations, real progress milestones, and explicit unavailable persistence", async () => {
  let created = 0, disposed = 0;
  const operations: string[] = [];
  const app: AutoNavLogApplication = new LocalApplication(() => {
    created++;
    return {
      request: async <T>(operation: string) => { operations.push(operation); return state as T; },
      dispose: () => { disposed++; },
    };
  });
  const progress: number[] = [];
  await app.bootstrap();
  await app.updateProject(update);
  await app.updateAndRecalculate(update);
  await app.renameRouteNode("node", "name");
  await app.replaceCheckPoints([]);
  await app.acknowledge("key", true);
  expect(await app.calculate(value => progress.push(value.percent))).toBe(state);
  expect(progress).toEqual([0, 100]);
  expect(operations).toEqual(["bootstrap", "updateProject", "updateAndRecalculate", "renameRouteNode", "replaceCheckPoints", "acknowledge", "calculate"]);
  for (const operation of [() => app.saveProject("name"), () => app.loadProject("id"), () => app.deleteProject("id")]) {
    await expect(operation()).rejects.toMatchObject({ code: "LOCAL_PERSISTENCE_UNAVAILABLE", details: { issue: 124 } });
  }
  expect(operations).toHaveLength(7);
  await app.newWork();
  await app.bootstrap();
  expect([created, disposed]).toEqual([2, 1]);
});

test("Local failure never invokes Legacy or emits successful progress", async () => {
  const calls = responses(json(state));
  const domainError = new ApplicationError("missing", "PROJECT_REQUIRED", { candidates: ["a"] });
  const app = new LocalApplication(() => ({ request: async () => { throw domainError; }, dispose() {} }));
  const progress: number[] = [];
  await expect(app.calculate(value => progress.push(value.percent))).rejects.toBe(domainError);
  expect(progress).toEqual([0]);
  const unavailable = new LocalApplication(() => { throw new Error("Worker internals"); });
  await expect(unavailable.bootstrap()).rejects.toMatchObject({ code: "APPLICATION_UNAVAILABLE" });
  expect(calls).toEqual([]);
});
