// Metadata-only fallback. Completeness, decoding and publication belong to Actions.
export const ORIGIN = "https://navmate.yuto24.com";
export const API = "https://api.github.com/repos/Yuto-24/AutoNavLog/actions/workflows/static-production.yml";
export const UPSTREAM = "https://database.rish.kyoto-u.ac.jp/arch/jmadata/data/gpv/original";
export const COOLDOWN = 90 * 60_000;
export const DAILY_LIMIT = 8;
export const RUN_LIMIT = 2;
const HOUR = 3600_000;
const KEY = "trigger-v1";
const PREFIX = "msm-fallback/";
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
interface Asset { run: string; file: string; bytes: number; sha256: string }
interface Catalog { schema_version: number; generated_at: string; expires_at: string; listings: Record<string, string>; assets: Asset[] }
interface Release { commit: string; dirty: boolean; files: Record<string, { bytes: number; sha256: string }> }
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
async function request(url: string, fetcher: typeof fetch, options: RequestInit = {}, limit = 1024 * 1024): Promise<{ response: Response; bytes: Uint8Array }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await fetcher(url, { ...options, redirect: "manual", signal: controller.signal });
    requireThat(response.ok, `HTTP_${response.status}`);
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = []; let size = 0;
    if (reader) for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      size += value.length;
      if (size > limit) { await reader.cancel(); throw new Fault("BODY_LIMIT"); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    return { response, bytes };
  } finally { clearTimeout(timeout); }
}
/** Decode metadata bytes for subsequent JSON or listing validation. */
const decode = (bytes: Uint8Array): string => new TextDecoder().decode(bytes);
/** Verify catalog lifecycle and release inventory; return metadata, not payload acceptance. */
async function canonical(fetcher: typeof fetch, now: number) {
  const { bytes } = await request(`${ORIGIN}/weather/msm/catalog.json`, fetcher, { cache: "no-store" });
  const catalog: Catalog = JSON.parse(decode(bytes));
  const release: Release = JSON.parse(decode((await request(`${ORIGIN}/release.json`, fetcher, { cache: "no-store" })).bytes));
  const generated = Date.parse(catalog.generated_at), expires = Date.parse(catalog.expires_at);
  requireThat(catalog.schema_version === 1 && catalog.listings && typeof catalog.listings === "object" &&
    !Array.isArray(catalog.listings) && Object.values(catalog.listings).every(v => typeof v === "string") &&
    Object.values(catalog.listings).some(v => v.trim()) && Array.isArray(catalog.assets) && catalog.assets.length > 0 &&
    Number.isFinite(generated) && generated <= now && expires - generated === 6 * HOUR, "INVALID_CATALOG");
  requireThat(/^[a-f0-9]{40}$/.test(release.commit) && release.dirty === false, "INVALID_RELEASE");
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes as Uint8Array<ArrayBuffer>)), b => b.toString(16).padStart(2,"0")).join("");
  const recorded = release.files?.["weather/msm/catalog.json"];
  requireThat(recorded?.bytes === bytes.length && recorded?.sha256 === digest, "CATALOG_RELEASE_MISMATCH");
  for (const asset of catalog.assets) {
    requireThat(typeof asset.run === "string" && runTime(asset.run) <= generated && /^[a-f0-9]{64}$/.test(asset.sha256) &&
      asset.file === `${asset.sha256}.npz` && Number.isSafeInteger(asset.bytes) && asset.bytes > 0, "INVALID_ASSET");
    const file = release.files?.[`weather/msm/${asset.file}`];
    requireThat(file?.bytes === asset.bytes && file?.sha256 === asset.sha256, "ASSET_RELEASE_MISMATCH");
  }
  return { run: catalog.assets.map(a => a.run).sort().at(-1)!, expires, commit: release.commit };
}
/** Find the newest advertised Run without claiming source completeness or acquiring forecasts. */
async function advertised(fetcher: typeof fetch, now: number): Promise<string> {
  const runs = new Set<string>();
  for (const time of [now - 24 * HOUR, now]) {
    const day = new Date(time).toISOString().slice(0,10).replaceAll("-", "/");
    const { bytes } = await request(`${UPSTREAM}/${day}/`, fetcher, {}, 256 * 1024);
    const matches = [...decode(bytes).matchAll(/Z__C_RJTD_(\d{14})_MSM_GPV_Rjp_(?:Lsurf|L-pall)_FH[\d-]+_grib2\.bin/g)];
    requireThat(matches.length > 0, "UNKNOWN_LISTING");
    for (const match of matches) { requireThat(runTime(match[1]) <= now, "FUTURE_RUN"); runs.add(match[1]); }
  }
  return [...runs].sort().at(-1)!;
}
/** Read bounded, complete Actions history; reject malformed or truncated evidence. */
async function githubRuns(fetcher: typeof fetch, headers: Record<string,string>, query: string): Promise<Run[]> {
  const { bytes } = await request(`${API}/runs?per_page=100&${query}`, fetcher, { headers });
  const value = JSON.parse(decode(bytes));
  // Truncated or malformed history is unknown, never evidence of inactivity.
  requireThat(Number.isSafeInteger(value.total_count) && value.total_count >= 0 && value.total_count <= 100 &&
    Array.isArray(value.workflow_runs) && value.workflow_runs.length === value.total_count, "UNKNOWN_ACTIONS");
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
export async function tick(env: Env, fetcher: typeof fetch = fetch, now = Date.now()) {
  requireThat(env.MODE === "observe" || env.MODE === "dispatch", "INVALID_MODE");
  const day = new Date(now).toISOString().slice(0,10);
  const state = stateFrom(await env.STATE.get(KEY), day);
  const published = await canonical(fetcher, now);
  const headers: Record<string,string> = { Accept: "application/vnd.github+json", "User-Agent": "AutoNavLog-MSM-trigger", "X-GitHub-Api-Version": "2026-03-10" };
  if (env.GITHUB_TOKEN) headers.Authorization = `Bearer ${env.GITHUB_TOKEN}`;
  // Unfiltered active query catches old waiting approvals as well as queued executions.
  const active: Run[] = [];
  for (const status of ["queued", "in_progress", "waiting", "pending", "requested"]) {
    active.push(...await githubRuns(fetcher, headers, `status=${status}`));
  }
  const recent = await githubRuns(fetcher, headers, `created=${encodeURIComponent(">=" + new Date(now - 24 * HOUR).toISOString())}`);
  requireThat(recent.every(r => ["completed","queued","in_progress","waiting","pending","requested"].includes(r.status)), "UNKNOWN_ACTIONS");
  const target = await advertised(fetcher, now);
  const pendingRuns = state.pending ? recent.filter(r => r.display_title === PREFIX + state.pending!.target && Date.parse(r.created_at) >= state.pending!.at - 60_000) : [];
  const publication = !state.pending ? "NONE" : pendingRuns.some(r => r.status === "completed" && r.conclusion !== "success") ? "FAILED" : "UNCONFIRMED";
  const base = { publication, published_run: published.run, advertised_run: target, expired: published.expires <= now };
  const result = (status: string) => ({ status, ...base });
  if (state.pending && published.run > state.pending.after && published.run >= state.pending.target &&
      published.expires > now && published.commit === state.pending.commit) {
    // Confirms canonical metadata only; NPZ hash/decode remains the existing publication gate.
    delete state.pending;
    await env.STATE.put(KEY, JSON.stringify(state));
    return result("PUBLICATION_OBSERVED");
  }
  if (state.pending) requireThat(published.commit === state.pending.commit, "APP_CHANGED");
  if (active.length || recent.some(r => r.status !== "completed")) return result("IN_FLIGHT");
  const external = recent.filter(r => r.display_title.startsWith(PREFIX));
  const lastGitHub = Math.max(0, ...external.map(r => Date.parse(r.created_at)));
  if (state.pending && now - state.pending.at < COOLDOWN) return result("AWAITING_PUBLICATION");
  if (target <= published.run) {
    requireThat(target === published.run && published.expires - now >= 2 * HOUR, "NO_NEW_RUN_UNHEALTHY");
    return result("NO_UPDATE");
  }
  // Give the hourly schedule first chance; urgency begins when half the six-hour lease is gone.
  if (published.expires - now > 3 * HOUR) return result("FRESH");
  if (now - Math.max(state.lastAttempt, lastGitHub) < COOLDOWN) return result("COOLDOWN");
  if (state.attempts >= DAILY_LIMIT || external.filter(r => r.created_at.startsWith(day)).length >= DAILY_LIMIT) return result("DAILY_LIMIT");
  if ((state.target === target && state.targetAttempts >= RUN_LIMIT) ||
      external.filter(r => r.display_title === PREFIX + target).length >= RUN_LIMIT) return result("RUN_LIMIT");
  if (env.MODE === "observe") return result(state.pending ? "PUBLICATION_UNCONFIRMED" : "WOULD_DISPATCH");
  requireThat(env.GITHUB_TOKEN, "AUTH_REQUIRED");
  state.attempts++; state.lastAttempt = now;
  state.targetAttempts = state.target === target ? state.targetAttempts + 1 : 1;
  state.target = target;
  state.pending = { after: published.run, target, at: now, commit: published.commit };
  await env.STATE.put(KEY, JSON.stringify(state)); // Failure stops before POST; KV is not a mutex.
  await request(`${API}/dispatches`, fetcher, { method: "POST", headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { feed_refresh: true, observed_run: target, publish: true } }) });
  return result("DISPATCH_ACCEPTED");
}
export default {
  /** Keep the Worker HTTP entrypoint closed; only scheduled events perform checks. */
  async fetch() { return new Response("Not found", { status: 404 }); },
  /** Run a scheduled check with redacted errors and an optional liveness-only heartbeat. */
  async scheduled(_event: unknown, env: Env) {
    let result;
    try { result = await tick(env); }
    catch (error) {
      // Never expose native errors, request headers, API responses or secret URLs.
      console.log(JSON.stringify({ status: "UNKNOWN", reason: error instanceof Fault ? error.message : "IO_OR_VALIDATION_FAILED" }));
      throw new Error("MSM trigger check failed");
    }
    console.log(JSON.stringify(result));
    if (env.HEARTBEAT_URL) {
      // Liveness only; #273 owns weather incidents and recovery notification deduplication.
      requireThat(/^https:\/\/hc-ping\.com\/[a-f0-9-]{36}$/.test(env.HEARTBEAT_URL), "INVALID_HEARTBEAT");
      try { await request(env.HEARTBEAT_URL, fetch, {}, 4096); }
      catch { throw new Error("MSM trigger heartbeat failed"); }
    }
  },
};
