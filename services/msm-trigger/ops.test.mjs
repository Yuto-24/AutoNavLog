import { test } from 'node:test';
import assert from 'node:assert/strict';
import { configuration, ensureNamespace, abandonPending } from './ops.mjs';
test('existing namespace is reused across pages and reruns', async () => {
  const calls = [];
  const call = async (method, path) => {
    calls.push(method);
    return { result: path.endsWith('page=2') ? [{ title: 'autonavlog-msm-trigger-state', id: 'a'.repeat(32) }] : [], result_info: { total_pages: 2 } };
  };
  assert.equal(await ensureNamespace(call), 'a'.repeat(32));
  assert.equal(await ensureNamespace(call), 'a'.repeat(32));
  assert.deepEqual(calls, ['GET','GET','GET','GET']);
});
test('missing namespace created once; ambiguous or unknown inventory fails closed', async () => {
  let id;
  const call = async method => {
    if (method === 'POST') { id = 'b'.repeat(32); return { result: { id } }; }
    return { result: id ? [{ title: 'autonavlog-msm-trigger-state', id }] : [], result_info: { total_pages: 1 } };
  };
  assert.equal(await ensureNamespace(call), await ensureNamespace(call));
  await assert.rejects(ensureNamespace(async () => ({ result: [] })), /Unknown/);
  await assert.rejects(ensureNamespace(async () => ({ result: Array(2).fill({ title: 'autonavlog-msm-trigger-state' }), result_info: { total_pages: 1 } })), /Ambiguous/);
});
test('configuration defaults to stopped observe and keeps namespace identity', () => {
  const state = { account_id: 'a'.repeat(32), namespace_id: 'b'.repeat(32) };
  const config = configuration(state);
  assert.equal(config.vars.MODE, 'observe'); assert.deepEqual(config.triggers.crons, []);
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
