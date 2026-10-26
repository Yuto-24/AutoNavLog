import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { tick, ORIGIN, API, UPSTREAM, COOLDOWN } from './worker.ts';
import { fixture, NOW, OLD, NEW } from './fixtures.mjs';
const run = (status = 'completed', conclusion = 'success', time = NOW) => ({ id: 1, status, conclusion, created_at: new Date(time).toISOString(), display_title: 'msm-fallback/' + NEW });
test('normal renewal: acceptance is not publication, later canonical Run/hash/lifetime confirms', async () => {
  const s = fixture(); assert.equal((await s.tick()).status, 'DISPATCH_ACCEPTED');
  assert.deepEqual(s.posts, [{ ref: 'main', inputs: { feed_refresh: true, observed_run: NEW, publish: true } }]);
  assert.equal((await s.tick()).status, 'AWAITING_PUBLICATION');
  s.catalog.assets[0].run = NEW;
  assert.equal((await s.tick()).status, 'PUBLICATION_OBSERVED');
  const cooldown = await s.tick();
  assert.equal(cooldown.status, 'COOLDOWN'); assert.equal(cooldown.upstream, 'NOT_CHECKED');
  assert.equal(s.posts.length, 1);
});
test('observe requires no credential and cannot POST; HTTP never triggers work', async () => {
  const s = fixture('observe'); delete s.env.GITHUB_TOKEN;
  assert.equal((await s.tick()).status, 'WOULD_DISPATCH'); assert.equal(s.posts.length, 0);
  assert.equal(s.data, null); assert.equal((await worker.fetch()).status, 404);
});
test('fresh catalog gives schedule first chance; same Run cannot renew TTL', async () => {
  const s = fixture(); s.now -= 1;
  const fresh = await s.tick();
  assert.equal(fresh.status, 'FRESH'); assert.equal(fresh.upstream, 'NOT_CHECKED');
  assert.equal(fresh.advertised_run, null); assert.equal(s.calls.length, 2);
  s.now = NOW; s.latest = OLD;
  assert.equal((await s.tick()).status, 'NO_UPDATE'); assert.equal(s.posts.length, 0);
});
for (const status of ['queued','in_progress','waiting','pending','requested']) test(`old ${status} execution blocks fallback`, async () => {
  const s = fixture(); s.active = [run(status, null, NOW - 3 * 86400_000)];
  assert.equal((await s.tick()).status, 'IN_FLIGHT'); assert.equal(s.posts.length, 0);
});
for (const code of [401,403,429,500,503]) test(`GitHub ${code} fails closed without dispatch`, async () => {
  const s = fixture(); s.error = url => url.startsWith(API) ? new Response('sensitive', { status: code }) : null;
  await assert.rejects(s.tick(), new RegExp(`HTTP_${code}`)); assert.equal(s.posts.length, 0);
});
test('upstream timeout, unrecognized listing and malformed catalog are not healthy no-ops', async () => {
  const s = fixture(); s.error = url => { if (url.startsWith(UPSTREAM)) throw new DOMException('secret', 'TimeoutError'); };
  await assert.rejects(s.tick(), /secret/); assert.equal(s.posts.length, 0);
  s.error = url => url.startsWith(UPSTREAM) ? new Response('<html>maintenance</html>') : null;
  await assert.rejects(s.tick(), /UNKNOWN_LISTING/);
  s.error = url => url.endsWith('catalog.json') ? new Response('{') : null;
  await assert.rejects(s.tick());
});
test('expired catalog recovers through newer Run only', async () => {
  const s = fixture(); s.catalog.generated_at = '2026-10-25T00:00:00Z'; s.catalog.expires_at = '2026-10-25T06:00:00Z';
  assert.equal((await s.tick()).status, 'DISPATCH_ACCEPTED');
  s.data = null; s.latest = OLD;
  await assert.rejects(s.tick(), /NO_NEW_RUN_UNHEALTHY/); assert.equal(s.posts.length, 1);
});
test('public GitHub success alone is not success; failed publication is recorded and retries bounded', async () => {
  const s = fixture(); await s.tick(); s.recent = [run('completed','failure')];
  let result = await s.tick(); assert.equal(result.publication, 'FAILED'); assert.equal(result.status, 'AWAITING_PUBLICATION');
  s.now += COOLDOWN; assert.equal((await s.tick()).status, 'DISPATCH_ACCEPTED');
  s.now += COOLDOWN; assert.equal((await s.tick()).status, 'RUN_LIMIT'); assert.equal(s.posts.length, 2);
});
test('expiry, catalog/release mismatch and app change never confirm a pending publication', async () => {
  const s = fixture(); await s.tick(); s.catalog.assets[0].run = NEW; s.mismatch = true;
  await assert.rejects(s.tick(), /CATALOG_RELEASE_MISMATCH/);
  s.mismatch = false; s.commit = 'c'.repeat(40); await assert.rejects(s.tick(), /APP_CHANGED/);
  s.commit = 'b'.repeat(40); s.now += 6 * 3600_000;
  await assert.rejects(s.tick(), /NO_NEW_RUN_UNHEALTHY/);
});
test('KV failure prevents POST; ambiguous POST consumes budget and is not retried immediately', async () => {
  const s = fixture(); s.failKV = true; await assert.rejects(s.tick()); assert.equal(s.posts.length, 0);
  s.failKV = false; s.error = url => { if (url.endsWith('dispatches')) throw Error('connection lost'); };
  await assert.rejects(s.tick()); assert.equal(JSON.parse(s.data).attempts, 1);
  s.error = null; assert.equal((await s.tick()).status, 'AWAITING_PUBLICATION'); assert.equal(s.posts.length, 0);
});
test('daily limit uses KV and GitHub evidence even when KV is stale', async () => {
  const s = fixture(); s.recent = Array.from({ length: 8 }, (_, i) => ({ ...run('completed','failure', NOW - 2 * 3600_000), id: i, display_title: 'msm-fallback/20261024000000' }));
  assert.equal((await s.tick()).status, 'DAILY_LIMIT');
  s.recent = []; s.data = JSON.stringify({ day: '2026-10-25', attempts: 8, lastAttempt: 0, target: '', targetAttempts: 0 });
  assert.equal((await s.tick()).status, 'DAILY_LIMIT'); assert.equal(s.posts.length, 0);
});
test('GitHub cooldown and per-Run cap protect against lost KV counters', async () => {
  const s = fixture(); s.recent = [run()]; assert.equal((await s.tick()).status, 'COOLDOWN');
  s.now += COOLDOWN; s.recent.push({ ...run(), id: 2 });
  assert.equal((await s.tick()).status, 'RUN_LIMIT');
});
test('unknown/truncated execution history and corrupt KV stop checks', async () => {
  const s = fixture(); s.data = '{}'; await assert.rejects(s.tick(), /INVALID_STATE/);
  s.data = null; s.error = url => url.includes('/runs?') ? Response.json({ total_count: 101, workflow_runs: [] }) : null;
  await assert.rejects(s.tick(), /UNKNOWN_ACTIONS/); assert.equal(s.posts.length, 0);
});
test('concurrent stale KV ticks can both request; no claim of KV locking', async () => {
  const s = fixture(); s.env.STATE.get = async () => null;
  const results = await Promise.all([s.tick(), s.tick()]);
  assert.ok(results.every(r => r.status === 'DISPATCH_ACCEPTED'));
  assert.equal(s.posts.length, 2); // Python tests cover serialized start and final same-Run refusal.
});
test('response body budgets and redirect policy apply to every fetch', async () => {
  const s = fixture(); await s.tick(); assert.ok(s.calls.every(c => c.options.redirect === 'manual'));
  s.error = url => url.endsWith('catalog.json') ? new Response('x'.repeat(1024 * 1024 + 1)) : null;
  await assert.rejects(s.tick(), /BODY_LIMIT/);
});

test('operator abandonment after legitimate app update restores observation without resetting budget', async () => {
  const { abandonPending } = await import('./ops.mjs');
  const s = fixture(); await s.tick(); s.commit = 'c'.repeat(40);
  await assert.rejects(s.tick(), /APP_CHANGED/);
  const before = JSON.parse(s.data);
  s.data = JSON.stringify(abandonPending(before));
  assert.equal((await s.tick()).status, 'COOLDOWN');
  s.now += COOLDOWN; s.env.MODE = 'observe';
  assert.equal((await s.tick()).status, 'WOULD_DISPATCH');
  assert.equal(JSON.parse(s.data).attempts, 1); assert.equal(s.posts.length, 1);
});

test('redirect responses fail closed and missing catalog listings are unknown', async () => {
  const s = fixture(); s.error = url => url.startsWith(API) ? new Response(null, { status: 302, headers: { Location: 'https://unexpected.test/' } }) : null;
  await assert.rejects(s.tick(), /HTTP_302/); assert.equal(s.posts.length, 0);
  s.error = null; s.catalog.listings = {};
  await assert.rejects(s.tick(), /INVALID_CATALOG/);
});
test('optional heartbeat signals liveness only; native errors and secret URLs are never logged', async () => {
  const s = fixture('observe');
  const realFetch = globalThis.fetch, realLog = console.log, realNow = Date.now;
  const logs = []; let pings = 0;
  const heartbeat = 'https://hc-ping.com/00000000-0000-0000-0000-000000000000';
  s.env.HEARTBEAT_URL = heartbeat;
  globalThis.fetch = (url, options) => { if (url === heartbeat) { pings++; return Promise.resolve(new Response('OK')); } return s.fetch(url, options); };
  console.log = value => logs.push(value); Date.now = () => NOW;
  try {
    await worker.scheduled({}, s.env);
    assert.equal(pings, 1); assert.equal(s.posts.length, 0);
    s.error = () => { throw Error('fixture-private-token'); };
    await assert.rejects(worker.scheduled({}, s.env), /check failed/);
    assert.equal(pings, 1);
    assert.ok(logs.every(line => !line.includes('fixture-private-token') && !line.includes(heartbeat)));
    s.error = null; await worker.scheduled({}, s.env); assert.equal(pings, 2);
  } finally { globalThis.fetch = realFetch; console.log = realLog; Date.now = realNow; }
});

test('canonical publication proof completes before unrelated Actions or upstream outages', async () => {
  const s = fixture(); await s.tick(); s.catalog.assets[0].run = NEW;
  s.calls.length = 0;
  s.error = url => url.startsWith(API) || url.startsWith(UPSTREAM) ? new Response('private', { status: 526 }) : null;
  const result = await s.tick();
  assert.equal(result.status, 'PUBLICATION_OBSERVED'); assert.equal(result.publication, 'OBSERVED');
  assert.equal(result.upstream, 'NOT_CHECKED'); assert.equal(result.advertised_run, null);
  assert.equal(JSON.parse(s.data).pending, undefined); assert.equal(s.calls.length, 2);
  assert.equal(s.posts.length, 1);
});

test('early publication confirmation still requires KV reconciliation and every canonical gate', async () => {
  const s = fixture(); await s.tick(); s.catalog.assets[0].run = NEW; s.failKV = true;
  await assert.rejects(s.tick()); assert.ok(JSON.parse(s.data).pending);
  s.failKV = false; s.commit = 'c'.repeat(40);
  await assert.rejects(s.tick(), /APP_CHANGED/); assert.ok(JSON.parse(s.data).pending);
  s.commit = 'b'.repeat(40); s.catalog.assets[0].bytes = 0;
  await assert.rejects(s.tick(), /INVALID_ASSET/); assert.ok(JSON.parse(s.data).pending);
});

test('a fresh intermediate publication cannot retry a higher pending target after cooldown', async () => {
  const s = fixture(); s.latest = '20261025060000';
  assert.equal((await s.tick()).status, 'DISPATCH_ACCEPTED');
  s.now += COOLDOWN; s.catalog.assets[0].run = NEW;
  s.catalog.generated_at = '2026-10-25T10:00:00Z'; s.catalog.expires_at = '2026-10-25T16:00:00Z';
  s.calls.length = 0;
  const result = await s.tick();
  assert.equal(result.status, 'FRESH'); assert.equal(result.publication, 'UNCONFIRMED');
  assert.equal(result.upstream, 'NOT_CHECKED'); assert.equal(result.advertised_run, null);
  assert.ok(s.calls.every(c => !c.url.startsWith(UPSTREAM)));
  assert.equal(JSON.parse(s.data).pending.target, '20261025060000');
  assert.equal(JSON.parse(s.data).attempts, 1); assert.equal(s.posts.length, 1);
});

test('freshness is never used to skip invalid canonical evidence', async () => {
  const s = fixture(); s.now -= 1; s.mismatch = true;
  await assert.rejects(s.tick(), /CATALOG_RELEASE_MISMATCH/);
  assert.equal(s.posts.length, 0); assert.equal(s.calls.length, 2);
});

test('in-flight and budget exits do not acquire upstream or claim it was checked', async () => {
  for (const status of ['queued', 'in_progress', 'waiting', 'pending', 'requested']) {
    const s = fixture(); s.active = [run(status, null, NOW - 3 * 86400_000)];
    const result = await s.tick();
    assert.equal(result.status, 'IN_FLIGHT'); assert.equal(result.upstream, 'NOT_CHECKED');
    assert.equal(result.advertised_run, null);
    assert.ok(s.calls.every(c => !c.url.startsWith(UPSTREAM) && !c.url.includes('created=')));
    assert.equal(s.data, null); assert.equal(s.posts.length, 0);
  }
  const raced = fixture(); raced.recent = [run('in_progress', null)];
  assert.equal((await raced.tick()).status, 'IN_FLIGHT');
  assert.ok(raced.calls.every(c => !c.url.startsWith(UPSTREAM)));
  for (const status of ['COOLDOWN', 'DAILY_LIMIT', 'AWAITING_PUBLICATION']) {
    const s = fixture();
    if (status === 'COOLDOWN') s.recent = [run()];
    if (status === 'DAILY_LIMIT') s.data = JSON.stringify({ day: '2026-10-25', attempts: 8, lastAttempt: 0, target: '', targetAttempts: 0 });
    if (status === 'AWAITING_PUBLICATION') { await s.tick(); s.calls.length = 0; }
    s.error = url => url.startsWith(UPSTREAM) ? new Response('private', { status: 526 }) : null;
    const result = await s.tick();
    assert.equal(result.status, status); assert.equal(result.upstream, 'NOT_CHECKED');
    assert.ok(s.calls.every(c => !c.url.startsWith(UPSTREAM)));
  }
});

test('duplicate filenames are deduplicated without accepting invalid, future or missing Runs', async () => {
  const s = fixture('observe');
  const filename = value => `Z__C_RJTD_${value}_MSM_GPV_Rjp_Lsurf_FH00-15_grib2.bin`;
  s.error = url => url.startsWith(UPSTREAM) ? new Response(filename(NEW).repeat(100)) : null;
  assert.equal((await s.tick()).status, 'WOULD_DISPATCH');
  for (const [value, reason] of [['20261025040000', 'INVALID_RUN'], ['20261025120000', 'FUTURE_RUN']]) {
    s.error = url => url.startsWith(UPSTREAM) ? new Response(filename(NEW).repeat(50) + filename(value).repeat(50)) : null;
    await assert.rejects(s.tick(), new RegExp(reason));
  }
  s.error = url => url.startsWith(UPSTREAM) ? new Response(url.endsWith('/25/') ? '' : filename(NEW)) : null;
  await assert.rejects(s.tick(), /UNKNOWN_LISTING/); assert.equal(s.posts.length, 0);
});

test('bounded streaming preserves exact catalog bytes across multiple chunks', async () => {
  const s = fixture('observe');
  s.error = url => {
    if (!url.endsWith('catalog.json')) return null;
    const bytes = new TextEncoder().encode(JSON.stringify(s.catalog));
    return new Response(new ReadableStream({ start(controller) {
      controller.enqueue(bytes.subarray(0, 10)); controller.enqueue(bytes.subarray(10)); controller.close();
    } }));
  };
  assert.equal((await s.tick()).status, 'WOULD_DISPATCH'); assert.equal(s.posts.length, 0);
});

test('526 diagnostics distinguish every request stage without exposing native content or credentials', async () => {
  const cases = [
    ['canonical_catalog', ORIGIN + '/weather/msm/catalog.json', 'navmate.yuto24.com'],
    ['canonical_release', ORIGIN + '/release.json', 'navmate.yuto24.com'],
    ...['queued', 'in_progress', 'waiting', 'pending', 'requested'].map(status => [`github_${status}`, `status=${status}`, 'api.github.com']),
    ['github_recent', 'created=', 'api.github.com'],
    ['upstream_previous', UPSTREAM + '/2026/10/24/', 'database.rish.kyoto-u.ac.jp'],
    ['upstream_today', UPSTREAM + '/2026/10/25/', 'database.rish.kyoto-u.ac.jp'],
    ['dispatch', API + '/dispatches', 'api.github.com'],
  ];
  for (const [stage, match, target] of cases) {
    const s = fixture(); const diagnostics = [];
    s.env.GITHUB_TOKEN = 'fixture-private-credential';
    s.error = url => url.includes(match) ? new Response('fixture-private-body', { status: 526, headers: { 'fixture-private-header': 'fixture-private-value' } }) : null;
    await assert.rejects(tick(s.env, s.fetch, s.now, record => diagnostics.push(record)), /HTTP_526/);
    assert.deepEqual(diagnostics.at(-1), { stage, target, status: 526, bytes: 0 });
    for (const record of diagnostics) assert.deepEqual(Object.keys(record), ['stage', 'target', 'status', 'bytes']);
    const serialized = JSON.stringify(diagnostics);
    assert.ok(!serialized.includes('fixture-private')); assert.ok(!serialized.includes('https://'));
    assert.equal(s.posts.length, 0);
  }
});

test('diagnostics report response byte counts, body limits and read failures separately', async () => {
  const s = fixture('observe'); const diagnostics = [];
  await tick(s.env, s.fetch, s.now, record => diagnostics.push(record));
  assert.equal(diagnostics[0].bytes, new TextEncoder().encode(JSON.stringify(s.catalog)).length);
  assert.equal(diagnostics[0].status, 200);
  s.error = url => url.endsWith('catalog.json') ? new Response('x'.repeat(1024 * 1024 + 1)) : null;
  await assert.rejects(tick(s.env, s.fetch, s.now, record => diagnostics.push(record)), /BODY_LIMIT/);
  assert.equal(diagnostics.at(-1).status, 'BODY_LIMIT'); assert.equal(diagnostics.at(-1).bytes, 1024 * 1024 + 1);
  s.error = url => url.endsWith('catalog.json') ? new Response(new ReadableStream({ pull() { throw Error('fixture-private-read'); } })) : null;
  await assert.rejects(tick(s.env, s.fetch, s.now, record => diagnostics.push(record)));
  assert.deepEqual(diagnostics.at(-1), { stage: 'canonical_catalog', target: 'navmate.yuto24.com', status: 'IO_ERROR', bytes: 0 });
  assert.ok(!JSON.stringify(diagnostics).includes('fixture-private-read'));
});
