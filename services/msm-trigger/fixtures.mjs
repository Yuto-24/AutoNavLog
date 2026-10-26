import assert from 'node:assert/strict';
import { tick, FRESHNESS, API } from './worker.ts';
export const NOW = Date.parse('2026-10-25T09:00:00Z');
export const OLD = '20261025000000';
/** Isolated scheduling hint, Actions history and KV; every outbound request is mocked. */
export function fixture(mode = 'dispatch') {
  const s = { now: NOW, data: null, posts: [], calls: [], active: [], recent: [], error: null, failKV: false,
    hint: { schema_version: 1, run: OLD, generated_at: '2026-10-25T06:00:00Z',
      expires_at: '2026-10-25T12:00:00Z', app_commit: 'b'.repeat(40) } };
  s.env = { MODE: mode, GITHUB_TOKEN: 'fixture-only', STATE: {
    get: async () => s.data,
    put: async (_k, value) => { if (s.failKV) throw Error('private'); s.data = value; },
  } };
  s.fetch = async (url, options = {}) => {
    url = String(url); s.calls.push({ url, options });
    if (s.error) { const result = s.error(url, options); if (result) return result; }
    if (url === FRESHNESS) return Response.json(s.hint);
    if (url.includes('/runs?')) {
      const status = new URL(url).searchParams.get('status');
      const runs = status ? s.active.filter(r => r.status === status) : s.recent;
      return Response.json({ total_count: runs.length, workflow_runs: runs });
    }
    assert.equal(url, `${API}/dispatches`); assert.equal(options.method, 'POST');
    s.posts.push(JSON.parse(options.body));
    return new Response(null, { status: 204 });
  };
  s.tick = () => tick(s.env, s.fetch, s.now);
  return s;
}
