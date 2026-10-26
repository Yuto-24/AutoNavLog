// Metadata-only fallback. Completeness, decoding and publication belong to Actions.
export const ORIGIN = "https://navmate.yuto24.com";
export const API = "https://api.github.com/repos/Yuto-24/AutoNavLog/actions/workflows/static-production.yml";
export const FRESHNESS = `${ORIGIN}/weather/msm/freshness.json`;
export const FRESHNESS_LIMIT = 2048;
export const COOLDOWN = 90 * 60_000;
export const DAILY_LIMIT = 8;
const HOUR = 3600_000;
const KEY = "trigger-v1";
const PREFIXES = ["msm-fallback/", "msm-request/"];
export interface Env {
  MODE: string;
  STATE: { get(key: string): Promise<string | null>; put(key: string, value: string): Promise<void> };
  GITHUB_TOKEN?: string;
  HEARTBEAT_URL?: string;
}
interface State {
  day: string; attempts: number; lastAttempt: number;
  target: string; targetAttempts: number;
  pending?: { after: string; target: string; at: number; commit: string };
}
interface Run { id: number; status: string; conclusion: string | null; created_at: string; display_title: string }
interface Freshness { schema_version: number; run: string; generated_at: string; expires_at: string; app_commit: string }
const targets = {
  canonical_freshness: "navmate.yuto24.com",
  github_queued: "api.github.com", github_in_progress: "api.github.com", github_waiting: "api.github.com",
  github_pending: "api.github.com", github_requested: "api.github.com", github_recent: "api.github.com",
  dispatch: "api.github.com", heartbeat: "hc-ping.com",
} as const;
type Stage = keyof typeof targets;
interface Diagnostic { stage: Stage; target: string; status: number | "IO_ERROR" | "BODY_LIMIT"; bytes: number }
type Diagnose = (record: Diagnostic) => void;
const noDiagnostics: Diagnose = () => {};
const decoder = new TextDecoder();
class Fault extends Error {}
/** Raise a redacted fault code when a required invariant is not established. */
function requireThat(value: unknown, code: string): asserts value { if (!value) throw new Fault(code); }
/** Validate a real UTC MSM Run on a three-hour boundary and return its epoch time. */
function runTime(run: string): number {
  requireThat(/^\d{14}$/.test(run), "INVALID_RUN");
  const iso = `${run.slice(0,4)}-${run.slice(4,6)}-${run.slice(6,8)}T${run.slice(8,10)}:${run.slice(10,12)}:${run.slice(12,14)}Z`;
  const time = Date.parse(iso);
  requireThat(Number.isFinite(time) && new Date(time).toISOString().replace(/[-:TZ.]/g, "").slice(0,14) === run && time % (3 * HOUR) === 0, "INVALID_RUN");
  return time;
}
// No retries here: ambiguous POSTs consume a slot before sending, then reconcile next tick.
/** Fetch once with bounded time and body size; never retry an ambiguous dispatch POST. */
async function request(stage: Stage, url: string, fetcher: typeof fetch, diagnose: Diagnose, options: RequestInit = {}, limit = 1024 * 1024): Promise<{ response: Response; bytes: Uint8Array }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  let status: Diagnostic["status"] = "IO_ERROR", size = 0;
  try {
    const response = await fetcher(url, { ...options, redirect: "manual", signal: controller.signal });
    status = response.status;
    requireThat(response.ok, `HTTP_${response.status}`);
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = [];
    if (reader) for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.length;
      if (size > limit) { status = "BODY_LIMIT"; await reader.cancel(); throw new Fault("BODY_LIMIT"); }
      chunks.push(value);
    }
    // A complete single chunk already owns the exact bounded bytes; avoid copying it again.
    const bytes = chunks.length === 1 ? chunks[0]! : new Uint8Array(size); let offset = 0;
    if (chunks.length > 1) for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    return { response, bytes };
  } catch (error) {
    if (!(error instanceof Fault) && status !== "BODY_LIMIT") status = "IO_ERROR";
    throw error;
  } finally {
    clearTimeout(timeout);
    // Only code-owned labels and numeric sizes/statuses. Never log URLs, bodies or headers.
    diagnose({ stage, target: targets[stage], status, bytes: size });
  }
}
/** Decode metadata bytes for subsequent JSON or listing validation. */
const decode = (bytes: Uint8Array): string => decoder.decode(bytes);
/** Read a small untrusted scheduling hint. This never establishes publication success. */
async function freshness(fetcher: typeof fetch, now: number, diagnose: Diagnose) {
  const { bytes } = await request("canonical_freshness", FRESHNESS, fetcher, diagnose, { cache: "no-store" }, FRESHNESS_LIMIT);
  const value: Freshness = JSON.parse(decode(bytes));
  const generated = Date.parse(value.generated_at), expires = Date.parse(value.expires_at);
  requireThat(value.schema_version === 1 && typeof value.run === "string" &&
    runTime(value.run) <= generated && Number.isFinite(generated) && generated <= now &&
    expires - generated === 6 * HOUR && /^[a-f0-9]{40}$/.test(value.app_commit), "INVALID_FRESHNESS");
  return { run: value.run, expires };
}
/** Read bounded, complete Actions history; reject malformed or truncated evidence. */
async function githubRuns(fetcher: typeof fetch, headers: Record<string,string>, query: string, stage: Stage, diagnose: Diagnose): Promise<Run[]> {
  const { bytes } = await request(stage, `${API}/runs?per_page=100&${query}`, fetcher, diagnose, { headers });
  const value = JSON.parse(decode(bytes));
  // Truncated or malformed history is unknown, never evidence of inactivity.
  requireThat(Number.isSafeInteger(value.total_count) && value.total_count >= 0 && value.total_count <= 100 &&
    Array.isArray(value.workflow_runs) && value.workflow_runs.length === value.total_count &&
    new Set(value.workflow_runs.map((r: Run) => r.id)).size === value.total_count, "UNKNOWN_ACTIONS");
  for (const run of value.workflow_runs) requireThat(Number.isSafeInteger(run.id) && typeof run.status === "string" &&
    Number.isFinite(Date.parse(run.created_at)) && typeof run.display_title === "string", "UNKNOWN_ACTIONS");
  return value.workflow_runs;
}
/** Validate saved counters and pending observation; reset only the daily budget on date rollover. */
function stateFrom(raw: string | null, day: string): State {
  if (raw === null) return { day, attempts: 0, lastAttempt: 0, target: "", targetAttempts: 0 };
  const s: State = JSON.parse(raw);
  requireThat(/^\d{4}-\d{2}-\d{2}$/.test(s.day) && Number.isSafeInteger(s.attempts) && s.attempts >= 0 &&
    Number.isFinite(s.lastAttempt) && s.lastAttempt >= 0 && typeof s.target === "string" &&
    Number.isSafeInteger(s.targetAttempts) && s.targetAttempts >= 0, "INVALID_STATE");
  if (s.pending) requireThat(runTime(s.pending.after) > 0 && runTime(s.pending.target) > 0 &&
    Number.isFinite(s.pending.at) && /^[a-f0-9]{40}$/.test(s.pending.commit), "INVALID_STATE");
  if (s.day !== day) { s.day = day; s.attempts = 0; }
  return s;
}
/** Observe publication and budgets, then optionally reserve an attempt before dispatching once. */
export async function tick(env: Env, fetcher: typeof fetch = fetch, now = Date.now(), diagnose: Diagnose = noDiagnostics) {
  requireThat(env.MODE === "observe" || env.MODE === "dispatch", "INVALID_MODE");
  const started = Date.now();
  const day = new Date(now).toISOString().slice(0,10);
  const state = stateFrom(await env.STATE.get(KEY), day);
  const hint = await freshness(fetcher, now, diagnose);
  const result = (status: string) => ({ status, publication: "NOT_VERIFIED", hint_run: hint.run,
    advertised_run: null, upstream: "NOT_CHECKED", expired: hint.expires <= now });
  // Missing/invalid hints throw before GitHub access. Fresh hints give schedule priority.
  if (hint.expires - now > 3 * HOUR) return result("FRESH");
  if (now - state.lastAttempt < COOLDOWN) return result("COOLDOWN");
  if (state.attempts >= DAILY_LIMIT) return result("DAILY_LIMIT");
  const headers: Record<string,string> = { Accept: "application/vnd.github+json", "User-Agent": "AutoNavLog-MSM-trigger", "X-GitHub-Api-Version": "2026-03-10" };
  if (env.GITHUB_TOKEN) headers.Authorization = `Bearer ${env.GITHUB_TOKEN}`;
  // Unfiltered active query catches old waiting approvals as well as queued executions.
  for (const status of ["queued", "in_progress", "waiting", "pending", "requested"] as const) {
    if ((await githubRuns(fetcher, headers, `status=${status}`, `github_${status}`, diagnose)).length) return result("IN_FLIGHT");
  }
  const recent = await githubRuns(fetcher, headers, `created=${encodeURIComponent(">=" + new Date(now - 24 * HOUR).toISOString())}`, "github_recent", diagnose);
  requireThat(recent.every(r => ["completed","queued","in_progress","waiting","pending","requested"].includes(r.status)), "UNKNOWN_ACTIONS");
  if (recent.some(r => r.status !== "completed")) return result("IN_FLIGHT");
  const external = recent.filter(r => PREFIXES.some(prefix => r.display_title.startsWith(prefix)));
  const lastGitHub = Math.max(0, ...external.map(r => Date.parse(r.created_at)));
  if (now - Math.max(state.lastAttempt, lastGitHub) < COOLDOWN) return result("COOLDOWN");
  if (external.filter(r => r.created_at.startsWith(day)).length >= DAILY_LIMIT) return result("DAILY_LIMIT");
  if (env.MODE === "observe") return result("WOULD_REQUEST");
  requireThat(env.GITHUB_TOKEN, "AUTH_REQUIRED");
  // KV records attempts before sending, but is not a distributed lock. Strict
  // concurrent admission belongs to a follow-up, not this deployment.
  state.attempts = Math.max(state.attempts, external.filter(r => r.created_at.startsWith(day)).length) + 1;
  const reservationStarted = Date.now();
  const attemptAt = now + (reservationStarted - started);
  requireThat(new Date(attemptAt).toISOString().slice(0,10) === day, "STATE_DAY_CHANGED");
  state.lastAttempt = attemptAt + 10_000;
  // Preserve legacy target/pending evidence; failed or ambiguous POSTs keep the slot.
  await env.STATE.put(KEY, JSON.stringify(state));
  requireThat(Date.now() - reservationStarted <= 10_000, "RESERVATION_EXPIRED");
  const { response } = await request("dispatch", `${API}/dispatches`, fetcher, diagnose, {
    method: "POST", headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { feed_refresh: true, request_id: String(now), publish: true } }),
  });
  requireThat(response.status === 204, "DISPATCH_UNCONFIRMED");
  return result("DISPATCH_ACCEPTED");
}
export default {
  /** Keep the Worker HTTP entrypoint closed; only scheduled events perform checks. */
  async fetch() { return new Response("Not found", { status: 404 }); },
  /** Run a scheduled check with redacted errors and an optional liveness-only heartbeat. */
  async scheduled(_event: unknown, env: Env) {
    let result;
    const diagnose: Diagnose = record => console.log(JSON.stringify(record));
    try { result = await tick(env, fetch, Date.now(), diagnose); }
    catch (error) {
      // Never expose native errors, request headers, API responses or secret URLs.
      console.log(JSON.stringify({ status: "UNKNOWN", reason: error instanceof Fault ? error.message : "IO_OR_VALIDATION_FAILED" }));
      throw new Error("MSM trigger check failed");
    }
    console.log(JSON.stringify(result));
    if (env.HEARTBEAT_URL) {
      // Liveness only; #273 owns weather incidents and recovery notification deduplication.
      requireThat(/^https:\/\/hc-ping\.com\/[a-f0-9-]{36}$/.test(env.HEARTBEAT_URL), "INVALID_HEARTBEAT");
      try { await request("heartbeat", env.HEARTBEAT_URL, fetch, diagnose, {}, 4096); }
      catch { throw new Error("MSM trigger heartbeat failed"); }
    }
  },
};
