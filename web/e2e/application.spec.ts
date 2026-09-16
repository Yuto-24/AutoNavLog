import { browserPlatform } from "../src/browserPlatform";
import { expect, test } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { ApplicationError } from "../src/application";
import type { AutoNavLogApplication, UpdateProjectInput } from "../src/application";
import type { WebState } from "../src/types";
import { LegacyApplication } from "../src/legacyApplication";
import { LocalApplication } from "../src/localApplication";
import type { LocalProjectRepository } from "../src/localProjectRepository";

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
    expect(text, name).not.toMatch(/\/api\/|credentials|document\.cookie|\b401\b|new Worker|firebase[\/\"]|comlink|localClient|legacyApplication|localApplication|FastAPI|fetch\(/);
  }
  expect(readFileSync(resolve(source, "application.ts"), "utf8")).not.toMatch(/\bstatus\s*:|RequestInit|Response|\brequest\s*[<(]/);
});

test("bootstrap shares initialization and contains session creation", async () => {
  const calls = responses(json({ state, token: "tab-one" }));
  const app: AutoNavLogApplication = new LegacyApplication();
  expect(await Promise.all([app.bootstrap(), app.bootstrap()])).toEqual([state, state]);
  expect(calls.map(call => call.path)).toEqual(["/api/application-session"]);
  expect(calls.every(call => call.options?.credentials === "same-origin")).toBe(true);
});

test("Legacy retries an operation once and does not expose HTTP status", async () => {
  const calls = responses(json({}, 401), json({ state, token: "adapter-token" }), json({ error: { code: "SESSION_REQUIRED", message: "session missing" } }, 401));
  const app: AutoNavLogApplication = new LegacyApplication();
  await expect(app.updateProject(update)).rejects.toMatchObject({ code: "SESSION_REQUIRED", message: "session missing" });
  expect(calls.map(call => call.path)).toEqual(["/api/project", "/api/application-session", "/api/project"]);
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
    ["/api/application-session", "DELETE"],
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

test("Local uses common operations, real progress milestones, and durable repository composition", async () => {
  let created = 0, disposed = 0;
  const operations: string[] = [];
  const repository = { list: async () => ({ projects: [], unavailable: [] }), delete: async () => {},
    read: async () => { throw new ApplicationError("missing", "LOCAL_PROJECT_UNAVAILABLE"); } } as unknown as LocalProjectRepository;
  const app: AutoNavLogApplication = new LocalApplication(() => repository, () => {
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
  await expect(app.saveProject("name")).rejects.toMatchObject({ code: "PROJECT_REQUIRED" });
  await expect(app.loadProject("id")).rejects.toMatchObject({ code: "LOCAL_PROJECT_UNAVAILABLE" });
  await app.deleteProject("id");
  await app.newWork();
  await app.bootstrap();
  expect([created, disposed]).toEqual([2, 1]);
});

test("Local failure never invokes Legacy or emits successful progress", async () => {
  const calls = responses(json(state));
  const domainError = new ApplicationError("missing", "PROJECT_REQUIRED", { candidates: ["a"] });
  const app = new LocalApplication(browserPlatform.persistence.createProjectRepository, () => ({ request: async () => { throw domainError; }, dispose() {} }));
  const progress: number[] = [];
  await expect(app.calculate(value => progress.push(value.percent))).rejects.toBe(domainError);
  expect(progress).toEqual([0]);
  const unavailable = new LocalApplication(browserPlatform.persistence.createProjectRepository, () => { throw new Error("Worker internals"); });
  await expect(unavailable.bootstrap()).rejects.toMatchObject({ code: "APPLICATION_UNAVAILABLE" });
  expect(calls).toEqual([]);
});


test("internal failures remain neutral at the Legacy UI boundary", async () => {
  responses(new Response("Internal Server Error", { status: 500 }));
  await expect(new LegacyApplication().updateAndRecalculate(update)).rejects.toMatchObject({
    code: "REQUEST_FAILED", message: "処理に失敗しました。", details: {},
  });
  responses(json({ status: "failed", error: {
    code: "CALCULATION_JOB_FAILED", message: "計算に失敗しました。", status: 500,
  } }));
  await expect(new LegacyApplication().calculate()).rejects.toMatchObject({
    code: "CALCULATION_JOB_FAILED", message: "計算に失敗しました。", details: {},
  });
});

test("Legacy tab handles and recovery retries remain adapter-private", async () => {
  const recovery = { version: 1, project: null, outcome: null, destination_wind: null, import_result: null, import_filename: null } as const;
  const restored = { ...state, workingRecovery: recovery };
  const calls = responses(json({ state: restored, token: "one" }), json({ state, token: "two" }),
    json({}, 401), json({ state: restored, token: "replacement" }), json(restored));
  const one = new LegacyApplication(), two = new LegacyApplication();
  await one.bootstrap(recovery);
  await two.bootstrap();
  await one.updateProject(update);
  expect(JSON.parse(calls[0].options!.body as string)).toEqual(recovery);
  expect(JSON.parse(calls[1].options!.body as string)).toBeNull();
  expect(calls[2].options!.headers).toMatchObject({ "X-AutoNavLog-Session": "one" });
  expect(JSON.parse(calls[3].options!.body as string)).toEqual(recovery);
  expect(calls[4].options!.headers).toMatchObject({ "X-AutoNavLog-Session": "replacement" });
});

test("session decoding preserves invalid text and rejects incompatible UI shapes", async () => {
  const { decodeSession, emptyCheckPointDraft } = await import("../src/applicationSession");
  const { initialPlanningForm } = await import("../src/forms");
  const session = {
    version: 1, working: { version: 1, project: null, outcome: null, destination_wind: null,
      import_result: null, import_filename: null },
    form: { ...initialPlanningForm(), tglCount: "abc", totalUsableFuelGal: "", destinationPatternAltitudeFtMsl: "123" },
    altitudeInputs: { a: "-7" }, navLogDrafts: {}, projectDraft: null,
    calculationInputsAreLocallyCurrent: false, pastedKml: "<incomplete", projectName: "",
    selectedProjectId: "", pendingKmz: null, selectedKmzDocument: "",
    checkPointDraft: { ...emptyCheckPointDraft(), latitude: "invalid" },
    nodeNameDraft: { id: null, name: "" }, vorColumns: [{ id: 0, stationIdentifier: null }],
  };
  expect(decodeSession(JSON.stringify(session))).toEqual(session);
  for (const malformed of [
    { ...session, version: 99 }, { ...session, form: {} },
    { ...session, navLogDrafts: { a: null } }, { ...session, pendingKmz: {} },
    { ...session, projectDraft: { id: "x", sections: [] } },
    { ...session, checkPointDraft: [] }, { ...session, vorColumns: [null] },
  ]) expect(() => decodeSession(JSON.stringify(malformed))).toThrow();
});

test("failed durable hydration restores the previous runtime working copy", async () => {
  const previous = { version: 1, project: { id: "previous" }, outcome: null,
    destination_wind: null, import_result: null, import_filename: null };
  let current: unknown = previous;
  let opened = false;
  const repository = {
    list: async () => ({ projects: [], unavailable: [] }),
    read: async () => ({ id: "next", draft: { id: "next" }, lastCalculation: null }),
    open: async () => { opened = true; },
  } as unknown as LocalProjectRepository;
  const app = new LocalApplication(() => repository, () => ({
    request: async <T>(operation: string, input?: any) => {
      if (operation === "bootstrap") {
        current = input;
        if (input.project.id === "next") {
          current = null;
          throw new ApplicationError("unavailable reference", "SESSION_RECOVERY_INVALID");
        }
      }
      return { ...state, workingRecovery: current } as T;
    }, dispose() {},
  }));
  await expect(app.loadProject("next")).rejects.toMatchObject({ code: "SESSION_RECOVERY_INVALID" });
  expect(current).toEqual(previous);
  expect(opened).toBe(false);
});

for (const storageFails of [false, true]) {
  test(`Local failed recalculation exposes canonical recovery with ${storageFails ? "unchanged" : "committed"} token`, async () => {
    const project = { id: "draft", revision: 0, name: "Draft", metadata: {}, total_usable_fuel_gal: 90 };
    let recovery: any = { version: 1, project, last_calculation: { retained: true }, durableToken: "before" };
    let stored: any = { id: project.id, token: "before", draft: project, checkpoint: null, lastCalculation: recovery.last_calculation };
    const client = () => ({
      request: async <T>(operation: string, input?: any) => {
        if (operation === "bootstrap") recovery = input;
        if (operation === "updateAndRecalculate" || operation === "updateProject") {
          recovery = { ...recovery, project: { ...recovery.project, total_usable_fuel_gal: input.total_usable_fuel_gal } };
          if (operation === "updateAndRecalculate") throw new ApplicationError("calculation failed", "CALCULATION_JOB_FAILED", { retained: true });
        }
        return structuredClone({ ...state, project: recovery.project, workingRecovery: recovery }) as T;
      }, dispose() {},
    });
    const repository = {
      list: async () => ({ projects: [], unavailable: [] }),
      read: async () => structuredClone(stored),
      write: async (record: any, expected: string) => {
        if (storageFails) throw new ApplicationError("storage failed", "LOCAL_STORAGE_FAILED");
        expect(expected).toBe(stored.token);
        stored = structuredClone(record);
      },
    } as unknown as LocalProjectRepository;
    const app = new LocalApplication(() => repository, client);
    await app.bootstrap(recovery);
    const failure = await app.updateAndRecalculate({ ...update, total_usable_fuel_gal: 72 }).catch(error => error);
    expect(failure).toBeInstanceOf(ApplicationError);
    expect(failure.code).toBe(storageFails ? "LOCAL_STORAGE_FAILED" : "CALCULATION_JOB_FAILED");
    expect(failure.committedState.workingRecovery.project.total_usable_fuel_gal).toBe(72);
    expect(failure.committedState.workingRecovery.last_calculation).toEqual({ retained: true });
    expect(failure.committedState.workingRecovery.durableToken).toBe(stored.token);
    if (!storageFails) {
      expect(failure.message).toBe("calculation failed");
      expect(failure.details).toEqual({ retained: true });
      const reloaded = new LocalApplication(() => repository, client);
      await reloaded.bootstrap(failure.committedState.workingRecovery);
      await reloaded.updateProject({ ...update, total_usable_fuel_gal: 71 });
      expect(stored.draft.total_usable_fuel_gal).toBe(71);
    }
  });
}
