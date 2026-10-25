import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { tick, ORIGIN, API, UPSTREAM } from './worker.ts';
export const NOW = Date.parse('2026-10-25T09:00:00Z');
export const OLD = '20261025000000', NEW = '20261025030000';
export function fixture(mode = 'dispatch') {
  const s = { now: NOW, data: null, posts: [], calls: [], active: [], recent: [], latest: NEW, error: null, failKV: false,
    catalog: { schema_version: 1, generated_at: '2026-10-25T06:00:00Z', expires_at: '2026-10-25T12:00:00Z',
      listings: { [UPSTREAM + '/2026/10/25']: `Z__C_RJTD_${OLD}_MSM_GPV_Rjp_Lsurf_FH00-15_grib2.bin` },
      assets: [{ run: OLD, sha256: 'a'.repeat(64), file: 'a'.repeat(64) + '.npz', bytes: 100 }] }, commit: 'b'.repeat(40), mismatch: false };
  s.env = { MODE: mode, GITHUB_TOKEN: 'test-only-never-live', STATE: {
    get: async () => s.data,
    put: async (_k, value) => { if (s.failKV) throw Error('private detail'); s.data = value; },
  } };
  s.fetch = async (url, options = {}) => {
    url = String(url); s.calls.push({ url, options });
    if (s.error) { const result = s.error(url, options); if (result) return result; }
    if (url === `${ORIGIN}/weather/msm/catalog.json`) return Response.json(s.catalog);
    if (url === `${ORIGIN}/release.json`) {
      const bytes = Buffer.from(JSON.stringify(s.catalog));
      return Response.json({ commit: s.commit, dirty: false, files: {
        'weather/msm/catalog.json': { bytes: bytes.length, sha256: s.mismatch ? 'bad' : createHash('sha256').update(bytes).digest('hex') },
        ...Object.fromEntries(s.catalog.assets.map(a => ['weather/msm/' + a.file, { bytes: a.bytes, sha256: a.sha256 }])),
      } });
    }
    if (url.startsWith(UPSTREAM)) return new Response(`Z__C_RJTD_${s.latest}_MSM_GPV_Rjp_Lsurf_FH00-15_grib2.bin`);
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
