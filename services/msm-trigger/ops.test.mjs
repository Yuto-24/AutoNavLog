import { test } from 'node:test';
import assert from 'node:assert/strict';
import { configuration, ensureNamespace, abandonPending } from './ops.mjs';
const namespace = (id, title = 'unrelated') => ({ id: id.toString(16).padStart(32, '0'), title });
const inventory = (items, page = 1, total = items.length) => ({ result: items,
  result_info: { count: items.length, page, per_page: 100, total_count: total } });
test('documented pagination reuses existing namespace across pages and reruns', async () => {
  const calls = [];
  const selected = namespace(101, 'autonavlog-msm-trigger-state');
  const call = async (method, path) => {
    calls.push(method);
    assert.match(path, /order=id&direction=asc/);
    return path.endsWith('page=2') ? inventory([selected], 2, 101) :
      inventory(Array.from({ length: 100 }, (_, i) => namespace(i + 1)), 1, 101);
  };
  const state = { account_id: 'a'.repeat(32), namespace_id: await ensureNamespace(call) };
  assert.equal(state.namespace_id, selected.id);
  assert.equal(await ensureNamespace(call), selected.id);
  assert.equal(configuration(JSON.parse(JSON.stringify(state))).kv_namespaces[0].id, selected.id);
  assert.deepEqual(calls, ['GET','GET','GET','GET']);
});
test('missing namespace created once; documented empty and single-page inventory works', async () => {
  let id;
  let creations = 0;
  const call = async method => {
    if (method === 'POST') { creations++; id = 'b'.repeat(32); return { result: { id } }; }
    return inventory(id ? [{ title: 'autonavlog-msm-trigger-state', id }] : []);
  };
  assert.equal(await ensureNamespace(call), await ensureNamespace(call));
  assert.equal(creations, 1);
});
test('ambiguous, incomplete, excessive, or changing inventory fails closed without creation', async () => {
  const reject = async (response, pattern = /Unknown/) => assert.rejects(ensureNamespace(async method => {
    assert.equal(method, 'GET'); return response;
  }), pattern);
  await reject({ result: [] });
  await reject({ result: [], result_info: { total_pages: 1 } });
  await reject(inventory([namespace(1, 'autonavlog-msm-trigger-state'), namespace(2, 'autonavlog-msm-trigger-state')]), /Ambiguous/);
  await reject(inventory([{ title: 'autonavlog-msm-trigger-state', id: 'invalid' }]));
  await reject(inventory(Array(2).fill(namespace(1))));
  await reject(inventory([], 1, 1));
  await reject(inventory(Array.from({ length: 100 }, (_, i) => namespace(i)), 1, 10_001), /budget/);
  await assert.rejects(ensureNamespace(async (method, path) => {
    assert.equal(method, 'GET');
    return path.endsWith('page=2') ? inventory([namespace(101), namespace(102)], 2, 102) :
      inventory(Array.from({ length: 100 }, (_, i) => namespace(i)), 1, 101);
  }), /changed/);
});
test('configuration defaults to stopped observe and keeps namespace identity', () => {
  const state = { account_id: 'a'.repeat(32), namespace_id: 'b'.repeat(32) };
  const config = configuration(state);
  assert.equal(config.vars.MODE, 'observe'); assert.deepEqual(config.triggers.crons, []);
  assert.equal(config.durable_objects, undefined); assert.equal(config.migrations, undefined);
  assert.equal(config.workers_dev, false); assert.equal(config.preview_urls, false);
  assert.deepEqual(configuration(state, 'dispatch', true).triggers.crons, ['*/15 * * * *']);
  assert.deepEqual(configuration(state, 'dispatch', true).kv_namespaces, config.kv_namespaces);
  assert.throws(() => configuration({}), /ensure-kv/);
});

test('explicit pending abandonment preserves every attempt and cooldown counter and is idempotent', () => {
  const original = { day: '2026-10-25', attempts: 2, lastAttempt: 1792900000000, target: '20261025030000',
    targetAttempts: 2, pending: { after: '20261025000000', target: '20261025030000', at: 1792900000000, commit: 'a'.repeat(40) } };
  const updated = abandonPending(original);
  assert.deepEqual(updated, { day: original.day, attempts: 2, lastAttempt: original.lastAttempt, target: original.target, targetAttempts: 2 });
  assert.deepEqual(abandonPending(updated), updated);
  assert.ok(original.pending); assert.throws(() => abandonPending({}), /Invalid/);
});
