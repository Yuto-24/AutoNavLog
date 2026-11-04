import { expect, test } from "@playwright/test";
import { ForecastBackupTransport, permitsForecastBackup } from "../src/forecastBackup";
import { LocalApplication } from "../src/localApplication";
import { ApplicationError } from "../src/application";
import type { LocalProjectRepository } from "../src/localProjectRepository";
import type { WebState } from "../src/types";
const original = globalThis.fetch;
test.afterEach(() => { globalThis.fetch = original; });
const endpoint = "https://backup.example/api/forecast-backup/jobs/";
const input = { input_identity: "a".repeat(64) };

for (const failure of ["headers", "body", "gateway"]) {
  test(`unknown PUT ${failure} outcome reconciles same capability without duplicate computation`, async () => {
    const calls: { url: string; options: RequestInit }[] = [];
    let id = "";
    globalThis.fetch = async (url, options) => {
      calls.push({ url: String(url), options: options! });
      if (calls.length === 1) {
        id = JSON.parse(String(options!.body)).request_id;
        if (failure === "headers") throw new TypeError("connection lost");
        if (failure === "gateway") return new Response("gateway error", { status: 502 });
        return new Response(new ReadableStream({ start(controller) { controller.error(new TypeError("body lost")); } }));
      }
      return Response.json({ request_id: id, status: "succeeded", result: { checked: true } });
    };
    const result = await new ForecastBackupTransport(endpoint).calculate(input, new AbortController().signal);
    expect(result.result).toEqual({ checked: true });
    expect(calls.map(call => call.options.method)).toEqual(["PUT", "GET"]);
    expect(calls[0]!.url).toBe(calls[1]!.url);
    expect(calls[0]!.options.headers).toEqual(calls[1]!.options.headers);
    expect(calls.every(call => call.options.credentials === "omit")).toBe(true);
    expect(calls[0]!.url).not.toContain((calls[0]!.options.headers as any)["X-AutoNavLog-Request"]);
  });
}

test("explicit absence retries same request; invalid results do not retry", async () => {
  const calls: RequestInit[] = [];
  globalThis.fetch = async (_url, options) => {
    calls.push(options!);
    if (calls.length === 1) throw new TypeError("lost");
    if (calls.length === 2) return Response.json({}, { status: 404 });
    return Response.json({ request_id: JSON.parse(String(options!.body)).request_id,
      status: "succeeded", result: { checked: true } });
  };
  await new ForecastBackupTransport(endpoint).calculate(input, new AbortController().signal);
  expect(calls.map(call => call.method)).toEqual(["PUT", "GET", "PUT"]);
  expect(calls[0]!.body).toBe(calls[2]!.body);
  let count = 0;
  globalThis.fetch = async () => { count++; return new Response("corrupt"); };
  await expect(new ForecastBackupTransport(endpoint).calculate(input, new AbortController().signal))
    .rejects.toMatchObject({ code: "FORECAST_BACKUP_INVALID" });
  expect(count).toBe(1);
});

test("only classified weather delivery failures permit fallback", () => {
  for (const code of ["WEATHER_CATALOG_EXPIRED", "WEATHER_COMMUNICATION_FAILED", "WEATHER_PREPARED_UNAVAILABLE"])
    expect(permitsForecastBackup(new ApplicationError("weather", code))).toBe(true);
  for (const code of ["VALIDATION_FAILED", "WEATHER_CATALOG_INVALID", "WEATHER_PAYLOAD_INTEGRITY_FAILED", "HTTP_500", "REQUEST_FAILED"])
    expect(permitsForecastBackup(new ApplicationError("failure", code))).toBe(false);
  expect(permitsForecastBackup(new Error("programming error"))).toBe(false);
});

function harness() {
  const originalState = { project: null, outcome: { id: "prior" }, workingRecovery: {
    version: 1, project: null, outcome: { id: "prior" } }, savedProjects: [] } as unknown as WebState;
  let current = originalState;
  let release!: () => void;
  let entered!: () => void;
  const adopting = new Promise<void>(resolve => { entered = resolve; });
  let calls = 0;
  const writes: unknown[] = [];
  const repository = { list: async () => ({ projects: [], unavailable: [] }),
    write: async (value: unknown) => { writes.push(value); } } as unknown as LocalProjectRepository;
  const app = new LocalApplication(() => repository, () => ({ request: async <T>(operation: string, payload?: any) => {
    if (operation === "calculate" && calls++ === 0) throw new ApplicationError("expired", "WEATHER_CATALOG_EXPIRED");
    if (operation === "prepareForecastBackup") return input as T;
    if (operation === "adoptForecastBackup") {
      entered(); await new Promise<void>(resolve => { release = resolve; });
      current = { ...originalState, outcome: { id: "backup" } } as unknown as WebState;
    }
    if (operation === "refreshSavedWork") current = { ...originalState, outcome: payload.working.outcome };
    return current as T;
  }, dispose() {} }), undefined, { calculate: async () => ({ request: {}, result: {} }) });
  return { app, adopting, release: () => release(), current: () => current, writes };
}

test("edit during worker adoption restores previous result before queued edit", async () => {
  const h = harness();
  const calculation = h.app.calculate().catch(error => error);
  await h.adopting;
  const edit = h.app.renameRouteNode("node", "new");
  h.release();
  expect(await calculation).toMatchObject({ code: "FORECAST_BACKUP_STALE" });
  await edit;
  expect(h.current().outcome).toEqual({ id: "prior" });
  expect(h.writes).toEqual([]);
});

test("account disposal during adoption cannot save or return old result", async () => {
  const h = harness();
  const calculation = h.app.calculate().catch(error => error);
  await h.adopting;
  h.app.dispose(); h.release();
  expect(await calculation).toBeInstanceOf(ApplicationError);
  expect(h.writes).toEqual([]);
});
