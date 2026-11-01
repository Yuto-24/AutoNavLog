// Explicit operator actions. Importable helpers permit tests without credentials.
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
const root = dirname(fileURLToPath(import.meta.url));
const stateDir = resolve(root, '.state');
const stateFile = resolve(stateDir, 'operator.json');
const configFile = resolve(stateDir, 'wrangler.json');
const template = JSON.parse(readFileSync(resolve(root, 'wrangler.jsonc')));
const title = 'autonavlog-msm-trigger-state';
/** Build a deployment config from validated IDs; default to observe with no Cron. */
export function configuration(state, mode = 'observe', cron = false) {
  if (!/^[a-f0-9]{32}$/.test(state.account_id) || !/^[a-f0-9]{32}$/.test(state.namespace_id)) throw Error('Run ensure-kv first');
  return { ...template, main: resolve(root, template.main), account_id: state.account_id,
    vars: { MODE: mode }, triggers: { crons: cron ? ['*/15 * * * *'] : [] },
    kv_namespaces: [{ binding: 'STATE', id: state.namespace_id }] };
}
/** Validate every inventory page before reusing or creating the single named KV namespace. */
export async function ensureNamespace(call) {
  const found = [];
  const seen = new Set();
  let total;
  for (let page = 1; ; page++) {
    const response = await call('GET', `/storage/kv/namespaces?per_page=100&order=id&direction=asc&page=${page}`);
    const info = response.result_info;
    if (!Array.isArray(response.result) || !Number.isSafeInteger(info?.total_count) || info.total_count < 0 ||
        info.page !== page || info.per_page !== 100 || info.count !== response.result.length ||
        info.count !== Math.min(100, Math.max(0, info.total_count - (page - 1) * 100))) throw Error('Unknown namespace inventory');
    if (total !== undefined && total !== info.total_count) throw Error('Namespace inventory changed; retry inventory');
    total = info.total_count;
    if (total > 10_000) throw Error('Namespace inventory exceeds budget');
    for (const item of response.result) {
      if (!/^[a-f0-9]{32}$/.test(item?.id ?? '') || typeof item.title !== 'string' || seen.has(item.id)) throw Error('Unknown namespace inventory');
      seen.add(item.id);
      if (item.title === title) found.push(item);
    }
    if (page * 100 >= total) break;
  }
  if (found.length > 1) throw Error('Ambiguous namespace title; do not create another');
  if (found.length === 1) return found[0].id;
  const created = await call('POST', '/storage/kv/namespaces', { title });
  if (!/^[a-f0-9]{32}$/.test(created.result?.id)) throw Error('Namespace creation unconfirmed; rerun inventory');
  return created.result.id;
}
/** Remove only pending observation, retaining attempt counters and cooldown. */
export function abandonPending(value) {
  if (!value || !Number.isSafeInteger(value.attempts) || value.attempts < 0 ||
      !Number.isSafeInteger(value.targetAttempts) || value.targetAttempts < 0 ||
      !Number.isFinite(value.lastAttempt) || value.lastAttempt < 0 ||
      !/^\d{4}-\d{2}-\d{2}$/.test(value.day) || typeof value.target !== 'string') throw Error('Invalid KV counters');
  const copy = { ...value };
  delete copy.pending;
  return copy;
}
/** Run the pinned CLI with the staged config and stop on any unsuccessful command. */
function wrangler(args, { capture = false } = {}) {
  const result = spawnSync(resolve(root, 'node_modules/.bin/wrangler'), [...args, '--config', configFile], {
    cwd: root, env: { ...process.env, WRANGLER_SEND_METRICS: 'false' }, encoding: 'utf8',
    stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
  });
  if (result.status !== 0) throw Error('Wrangler failed; no subsequent operation performed');
  return capture ? JSON.parse(result.stdout) : undefined;
}
/** Execute one explicit operator action, keeping Secret staging and activation separate. */
async function main() {
  const [command, name] = process.argv.slice(2);
  if (command === 'plan') {
    console.log(JSON.stringify({ worker: template.name, namespace: title, defaults: template.vars,
      cron: template.triggers.crons, dispatch: 'separate opt-in in Worker AND GitHub',
      actions: ['ensure-kv', 'deploy-observe', 'stage-secret GITHUB_TOKEN', 'deploy-staged',
        'observe-cron', 'enable-dispatch', 'enable-cron', 'stop', 'abandon-pending'],
      secret_warning: 'versions secret put uploads an undeployed version; deploy-staged is separate' }, null, 2));
    return;
  }
  mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  const state = existsSync(stateFile) ? JSON.parse(readFileSync(stateFile)) : {};
  const save = () => writeFileSync(stateFile, JSON.stringify(state, null, 2) + '\n', { mode: 0o600 });
  if (command === 'ensure-kv') {
    const account = state.account_id ?? process.env.CLOUDFLARE_ACCOUNT_ID;
    if (!/^[a-f0-9]{32}$/.test(account ?? '') || !process.env.CLOUDFLARE_API_TOKEN) throw Error('Explicit Cloudflare account and approved API token required');
    if (process.env.CLOUDFLARE_ACCOUNT_ID && process.env.CLOUDFLARE_ACCOUNT_ID !== account) throw Error('Account changed');
    state.account_id = account;
    state.namespace_id = await ensureNamespace(async (method, path, body) => {
      const response = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}${path}`, {
        method, redirect: 'error', signal: AbortSignal.timeout(30_000),
        headers: { Authorization: `Bearer ${process.env.CLOUDFLARE_API_TOKEN}`, 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) throw Error(`Cloudflare HTTP ${response.status}`);
      const value = await response.json();
      if (value.success !== true) throw Error('Cloudflare operation not confirmed');
      return value;
    });
    save();
    writeFileSync(configFile, JSON.stringify(configuration(state), null, 2));
    console.log('Namespace selected; no Worker deployed.'); return;
  }
  if (command === 'abandon-pending') {
    if (state.mode !== 'observe' || state.cron !== false) throw Error('Stop Cron before abandoning a pending request');
    writeFileSync(configFile, JSON.stringify(configuration(state), null, 2));
    const previous = wrangler(['kv', 'key', 'get', 'trigger-v1', '--binding', 'STATE', '--remote', '--text'], { capture: true });
    const updated = abandonPending(previous);
    const pendingFile = resolve(stateDir, 'reconciled.json');
    writeFileSync(pendingFile, JSON.stringify(updated), { mode: 0o600 });
    wrangler(['kv', 'key', 'put', 'trigger-v1', '--binding', 'STATE', '--remote', '--path', pendingFile]);
    console.log('Pending observation abandoned; all attempt counters and cooldown preserved. No success recorded.');
    return;
  }
  if (command === 'stage-secret') {
    if (state.staged_version) throw Error('Deploy the pending secret version before staging another');
    if (!['GITHUB_TOKEN', 'HEARTBEAT_URL'].includes(name)) throw Error('Only documented secret names allowed');
    if (state.mode !== 'observe' || state.cron !== false) throw Error('Run deploy-observe (or stop) first; stage credentials with Cron stopped');
    writeFileSync(configFile, JSON.stringify(configuration(state), null, 2));
    const before = wrangler(['versions', 'list', '--json'], { capture: true }).map(v => v.id);
    wrangler(['versions', 'secret', 'put', name]); // Hidden interactive input; never argv or a file.
    const after = wrangler(['versions', 'list', '--json'], { capture: true }).filter(v => !before.includes(v.id));
    if (after.length !== 1) throw Error('Concurrent version changes: inspect versions before deployment');
    state.staged_version = after[0].id; save();
    console.log('Secret version staged, NOT deployed. Run deploy-staged after review.'); return;
  }
  if (command === 'deploy-staged') {
    if (!/^[a-f0-9-]{36}$/.test(state.staged_version ?? '') || state.mode !== 'observe' || state.cron !== false) throw Error('No safely staged version');
    wrangler(['versions', 'deploy', `${state.staged_version}@100%`, '--yes']);
    delete state.staged_version; save(); return;
  }
  const stages = { 'deploy-observe': ['observe', false], 'observe-cron': ['observe', true],
    'enable-dispatch': ['dispatch', false], 'enable-cron': ['dispatch', true], 'stop': ['observe', false] };
  if (!stages[command]) throw Error('Unknown action; use plan');
  const [mode, cron] = stages[command];
  if (command === 'enable-cron' && state.mode !== 'dispatch') throw Error('Run enable-dispatch separately first');
  if (state.staged_version) throw Error('Deploy the staged secret version before changing code/configuration');
  writeFileSync(configFile, JSON.stringify(configuration(state, mode, cron), null, 2));
  // No routes, workers.dev or paid-plan flag. An explicit action deploys code/config.
  wrangler(['deploy']);
  state.mode = mode; state.cron = cron; save();
  console.log(`Deployed mode=${mode}, cron=${cron}; verify remote settings and propagation before acceptance.`);
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(() => { console.error('Operator action failed. Inspect non-secret configuration and operation status; do not print tokens.'); process.exitCode = 1; });
}
