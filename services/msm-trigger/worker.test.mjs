import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { tick, API, FRESHNESS, FRESHNESS_LIMIT, COOLDOWN } from './worker.ts';
import { fixture, NOW } from './fixtures.mjs';
const run = (status = 'completed', time = NOW) => ({ id: 1, status, conclusion: 'success', created_at: new Date(time).toISOString(), display_title: 'msm-request/' + time });
test('request IDs are separate from observed Runs; acceptance never proves publication', async () => {
  const s = fixture(); const result = await s.tick();
  assert.equal(result.status, 'DISPATCH_ACCEPTED'); assert.equal(result.publication, 'NOT_VERIFIED');
  assert.deepEqual(s.posts, [{ ref: 'main', inputs: { feed_refresh: true, request_id: String(NOW), publish: true } }]);
  assert.equal((await s.tick()).status, 'COOLDOWN'); assert.equal(s.posts.length, 1);
  assert.ok(s.calls.every(c => c.url === FRESHNESS || c.url.startsWith(API)));
});
test('fresh path reads one small hint, without GitHub, catalog, release or upstream', async () => {
  const s = fixture(); s.now--;
  assert.equal((await s.tick()).status, 'FRESH'); assert.equal(s.calls.length, 1);
  assert.equal(s.data, null); assert.equal(s.posts.length, 0);
});
test('observe requires no credential, never writes or posts; HTTP entry stays closed', async () => {
  const s = fixture('observe'); delete s.env.GITHUB_TOKEN;
  assert.equal((await s.tick()).status, 'WOULD_REQUEST'); assert.equal(s.data, null);
  assert.equal(s.posts.length, 0); assert.equal((await worker.fetch()).status, 404);
});
for (const status of ['queued', 'in_progress', 'waiting', 'pending', 'requested']) test(`old ${status} blocks a request`, async () => {
  const s = fixture(); s.active = [run(status, NOW - 3 * 86400_000)];
  assert.equal((await s.tick()).status, 'IN_FLIGHT'); assert.equal(s.posts.length, 0);
});
for (const status of [302,401,403,429,500,503,526]) test(`HTTP ${status} fails closed with redacted stage diagnostics`, async () => {
  const s = fixture(); const diagnostics = [];
  s.error = url => url === FRESHNESS ? new Response('private-token', { status }) : null;
  await assert.rejects(tick(s.env, s.fetch, s.now, d => diagnostics.push(d)), new RegExp(`HTTP_${status}`));
  assert.deepEqual(diagnostics, [{ stage: 'canonical_freshness', target: 'navmate.yuto24.com', status, bytes: 0 }]);
  assert.equal(s.posts.length, 0);
});
for (const change of [{ schema_version: 2 }, { run: '20261025040000' }, { run: '20261025120000' },
  { app_commit: 'bad' }, { expires_at: '2026-10-26T12:00:00Z' }, { generated_at: 'bad' }]) test(`invalid hint ${JSON.stringify(change)} never requests`, async () => {
  const s = fixture(); Object.assign(s.hint, change);
  await assert.rejects(s.tick()); assert.equal(s.calls.length, 1); assert.equal(s.posts.length, 0);
});
test('migration: missing, HTML, malformed, oversize and failed hints cannot create request storms', async () => {
  for (const response of [() => new Response('', { status: 404 }), () => new Response('<html>SPA</html>'),
    () => new Response('{'), () => new Response('x'.repeat(FRESHNESS_LIMIT + 1))]) {
    const s = fixture(); s.error = response;
    for (let i = 0; i < 10; i++) { s.now += 900000; await assert.rejects(s.tick()); }
    assert.equal(s.posts.length, 0); assert.equal(s.data, null);
  }
  const s = fixture(); s.error = () => { throw new Error('private timeout'); };
  await assert.rejects(s.tick()); assert.equal(s.posts.length, 0);
});
test('expired valid hint allows bounded checks, without claiming an upstream Run exists', async () => {
  const s = fixture(); s.hint.generated_at = '2026-10-25T00:00:00Z'; s.hint.expires_at = '2026-10-25T06:00:00Z';
  assert.equal((await s.tick()).status, 'DISPATCH_ACCEPTED');
  assert.equal((await s.tick()).upstream, 'NOT_CHECKED');
});
test('no-op requests still consume daily budget; GitHub history and legacy requests count', async () => {
  const s = fixture(); s.recent = Array.from({ length: 8 }, (_, i) => ({ ...run('completed', NOW - 2 * 3600_000), id: i,
    display_title: i % 2 ? 'msm-fallback/20261024000000' : 'msm-request/' + i }));
  assert.equal((await s.tick()).status, 'DAILY_LIMIT');
  s.recent = []; s.data = JSON.stringify({ day: '2026-10-25', attempts: 8, lastAttempt: 0, target: '', targetAttempts: 0 });
  assert.equal((await s.tick()).status, 'DAILY_LIMIT'); assert.equal(s.posts.length, 0);
});
test('GitHub cooldown survives KV loss; pending legacy evidence and counters are retained', async () => {
  const s = fixture(); s.recent = [run()]; assert.equal((await s.tick()).status, 'COOLDOWN');
  s.recent = []; const previous = { day: '2026-10-25', attempts: 1, lastAttempt: NOW - COOLDOWN,
    target: '20261025030000', targetAttempts: 1, pending: { after: '20261025000000', target: '20261025030000', at: NOW - COOLDOWN, commit: 'b'.repeat(40) } };
  s.data = JSON.stringify(previous); await s.tick();
  assert.deepEqual(JSON.parse(s.data).pending, previous.pending); assert.equal(JSON.parse(s.data).attempts, 2);
});
test('KV write failure prevents POST; ambiguous POST reserves a slot without immediate retry', async () => {
  const s = fixture(); s.failKV = true; await assert.rejects(s.tick()); assert.equal(s.posts.length, 0);
  s.failKV = false; s.error = url => { if (url.endsWith('dispatches')) throw Error('lost'); };
  await assert.rejects(s.tick()); assert.equal(JSON.parse(s.data).attempts, 1);
  s.error = null; assert.equal((await s.tick()).status, 'COOLDOWN'); assert.equal(s.posts.length, 0);
});
test('corrupt state, truncated history and unavailable Actions do not grant requests', async () => {
  const s = fixture(); s.data = '{}'; await assert.rejects(s.tick(), /INVALID_STATE/);
  s.data = null; s.error = url => url.includes('/runs?') ? Response.json({ total_count: 101, workflow_runs: [] }) : null;
  await assert.rejects(s.tick(), /UNKNOWN_ACTIONS/);
  s.error = url => url.startsWith(API) ? new Response('', { status: 503 }) : null;
  await assert.rejects(s.tick(), /HTTP_503/); assert.equal(s.posts.length, 0);
});
test('streaming body remains bounded, exact and redirect-free', async () => {
  const s = fixture('observe');
  s.error = url => {
    if (url !== FRESHNESS) return null;
    const bytes = new TextEncoder().encode(JSON.stringify(s.hint));
    return new Response(new ReadableStream({ start(c) { c.enqueue(bytes.subarray(0, 10)); c.enqueue(bytes.subarray(10)); c.close(); } }));
  };
  assert.equal((await s.tick()).status, 'WOULD_REQUEST'); assert.ok(s.calls.every(c => c.options.redirect === 'manual'));
});
test('scheduled errors and heartbeat never expose credentials or imply publication success', async () => {
  const s = fixture('observe'); const saved = [globalThis.fetch, console.log, Date.now]; const logs = [];
  const heartbeat = 'https://hc-ping.com/00000000-0000-0000-0000-000000000000'; s.env.HEARTBEAT_URL = heartbeat;
  let pings = 0;
  globalThis.fetch = (url, options) => { if (url === heartbeat) { pings++; return Promise.resolve(new Response('OK')); } return s.fetch(url, options); };
  console.log = value => logs.push(value); Date.now = () => NOW;
  try {
    await worker.scheduled({}, s.env); assert.equal(pings, 1);
    s.error = () => { throw Error('private-token'); }; await assert.rejects(worker.scheduled({}, s.env), /check failed/);
    assert.equal(pings, 1); assert.ok(logs.every(line => !line.includes('private-token') && !line.includes(heartbeat)));
  } finally { [globalThis.fetch, console.log, Date.now] = saved; }
});

test('UTC rollover resets daily count but retains cooldown and legacy evidence', async () => {
  const s = fixture(); s.now = Date.parse('2026-10-25T23:59:59Z');
  await s.tick(); const previous = JSON.parse(s.data);
  s.now += 2000;
  assert.equal((await s.tick()).status, 'COOLDOWN'); assert.equal(s.posts.length, 1);
  s.now += COOLDOWN + 10001;
  await s.tick();
  assert.equal(JSON.parse(s.data).day, '2026-10-26');
  assert.equal(JSON.parse(s.data).attempts, 1);
  assert.ok(JSON.parse(s.data).lastAttempt > previous.lastAttempt);
});
test('history restores daily count when KV is missing; accepted no-ops still count', async () => {
  const s = fixture(); s.recent = Array.from({ length: 7 }, (_, i) => ({
    ...run('completed', NOW - 2 * 3600_000), id: i }));
  await s.tick(); assert.equal(JSON.parse(s.data).attempts, 8);
  s.now += COOLDOWN + 10001;
  assert.equal((await s.tick()).status, 'DAILY_LIMIT'); assert.equal(s.posts.length, 1);
});
for (const status of [200, 401, 403, 429, 500]) test(`unconfirmed POST ${status} keeps KV slot without retry`, async () => {
  const s = fixture(); let posts = 0;
  s.error = url => { if (url.endsWith('/dispatches')) { posts++; return new Response(null, { status }); } };
  await assert.rejects(s.tick()); assert.equal(posts, 1);
  assert.equal(JSON.parse(s.data).attempts, 1);
  assert.equal((await s.tick()).status, 'COOLDOWN'); assert.equal(posts, 1);
});
test('stale KV cannot bypass Actions active/cooldown checks, without claiming strict admission', async () => {
  const s = fixture(); s.env.STATE.get = async () => null;
  s.active = [run('waiting')];
  assert.equal((await s.tick()).status, 'IN_FLIGHT');
  s.active = []; s.recent = [run()];
  assert.equal((await s.tick()).status, 'COOLDOWN'); assert.equal(s.posts.length, 0);
});

test('slow KV reservation stops before POST and retains the consumed attempt', async () => {
  const s = fixture(); const clock = Date.now; let elapsed = 0;
  const put = s.env.STATE.put;
  Date.now = () => elapsed;
  s.env.STATE.put = async (...args) => { await put(...args); elapsed += 10001; };
  try {
    await assert.rejects(s.tick(), /RESERVATION_EXPIRED/);
    assert.equal(JSON.parse(s.data).attempts, 1); assert.equal(s.posts.length, 0);
  } finally { Date.now = clock; }
});
test('checks crossing UTC midnight do not charge yesterday and send today', async () => {
  const s = fixture(); s.now = Date.parse('2026-10-25T23:59:59Z');
  const clock = Date.now; let elapsed = 0; Date.now = () => elapsed;
  s.error = url => { if (url === FRESHNESS) elapsed += 2000; };
  try {
    await assert.rejects(s.tick(), /STATE_DAY_CHANGED/);
    assert.equal(s.data, null); assert.equal(s.posts.length, 0);
  } finally { Date.now = clock; }
});
