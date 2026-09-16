/** Opt-in real-data acceptance. Run against a production Static build with a fresh feed. */
import { chromium } from 'playwright';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { compare } from '../e2e/helpers/compare.ts';
import { calculationCoreGolden } from '../e2e/helpers/localGolden.ts';

const args = Object.fromEntries(Array.from({ length: (process.argv.length - 2) / 2 }, (_, i) =>
  [process.argv[2 + i * 2], process.argv[3 + i * 2]]));
if (!args['--reference'] || !args['--output']) throw Error('--reference and --output are required');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const browser = args['--cdp'] ? await chromium.connectOverCDP(args['--cdp']) : await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const system = await browser.newBrowserCDPSession();
const requests = [];
const failures = [];
const crashes = [];
const memory = [];
let stage = 'startup';
let stop = false;
const run = promisify(execFile);
const sampling = (async () => {
  while (!stop) {
    try {
      const { processInfo } = await system.send('SystemInfo.getProcessInfo');
      const pids = processInfo.map(p => Number(p.id)).filter(Number.isSafeInteger);
      let rss = 0;
      if (process.platform === 'win32') {
        const { stdout } = await run(`${process.env.SystemRoot}\\System32\\WindowsPowerShell\\v1.0\\powershell.exe`,
          ['-NoProfile', '-Command', `(Get-Process -Id ${pids.join(',')} -ErrorAction SilentlyContinue | Measure-Object WorkingSet64 -Sum).Sum`]);
        rss = Number(stdout.trim());
      } else {
        for (const pid of pids) {
          try { rss += Number(readFileSync(`/proc/${pid}/status`, 'utf8').match(/^VmRSS:\s+(\d+)/m)?.[1] ?? 0) * 1024; }
          catch { /* process may have exited */ }
        }
      }
      memory.push({ at: Date.now(), stage, process_rss_bytes: rss, processes: pids.length });
    } catch (e) { memory.push({ stage, error: String(e) }); }
    await new Promise(r => setTimeout(r, 250));
  }
})();
try {
  context.on('request', request => requests.push({ url: request.url(), stage }));
  context.on('requestfailed', request => failures.push({ url: request.url(), error: request.failure() }));
  await context.route('**/api/**', route => route.abort());
  page.on('crash', () => crashes.push('page crash'));
  page.on('pageerror', error => crashes.push(String(error)));
  await page.addInitScript(() => {
    window.acceptanceStates = [];
    const Original = Worker;
    window.Worker = class extends Original {
      constructor(...args) {
        super(...args);
        this.addEventListener('message', event => {
          if (typeof event.data.value === 'string') {
            try { const parsed = JSON.parse(event.data.value);
              if (parsed.workingRecovery || parsed.error) window.acceptanceStates.push(parsed);
            } catch { /* unrelated RPC */ }
          }
        });
      }
    };
  });
  const base = args['--url'] ?? 'http://127.0.0.1:4179';
  const started = Date.now();
  await page.goto(base);
  await page.getByLabel('DATE', { exact: true }).fill(args['--flight-date'] ?? '2026-09-16');
  await page.getByLabel('ETD JST', { exact: true }).fill(args['--departure-time'] ?? '12:00');
  await page.getByLabel('気象モード').selectOption('FORECAST');
  await page.locator('input[type="file"]').setInputFiles(resolve(root, 'tests/fixtures/issue_43_golden.kml'));
  await page.getByLabel('地図とKML記載順を確認しました').check();
  await page.getByRole('button', { name: '経路を確定', exact: true }).click();
  for (const [name, altitude] of [['RJFM', '6500'], ['米ノ津', '7500'], ['玉名', '6500']]) {
    const field = page.getByLabel(`${name}出発Legの計画高度`, { exact: true });
    await field.fill(altitude); await field.blur();
  }
  async function calculate() {
    const count = await page.evaluate(() => window.acceptanceStates.length);
    await page.getByRole('button', { name: /NAV LOGを(?:作る|再計算)$/ }).click();
    await page.waitForFunction(n => window.acceptanceStates.length > n &&
      (window.acceptanceStates.at(-1)?.error || window.acceptanceStates.at(-1)?.readiness?.calculationIsCurrent), count, { timeout: 120000 });
    const state = await page.evaluate(() => window.acceptanceStates.at(-1));
    if (state.error) throw Error(JSON.stringify(state.error));
    return state;
  }
  stage = 'cold-forecast';
  const coldStart = Date.now();
  const cold = await calculate();
  const coldEnd = Date.now();
  const reference = JSON.parse(readFileSync(args['--reference'], 'utf8'));
  compare(calculationCoreGolden(cold), calculationCoreGolden(reference));
  const coldRequests = requests.filter(r => r.url.includes('/weather/msm/'));
  const cacheBytes = await page.evaluate(async () => {
    const result = [];
    for (const name of await caches.keys()) if (name.startsWith('autonavlog.weather.')) {
      const cache = await caches.open(name);
      for (const request of await cache.keys()) {
        const response = await cache.match(request);
        result.push({ url: request.url, bytes: (await response.arrayBuffer()).byteLength });
      }
    }
    return result;
  });
  stage = 'warm-reload';
  await page.reload();
  await page.locator('.nav-log-table').waitFor();
  stage = 'warm-forecast';
  const warmStart = Date.now();
  const warm = await calculate();
  const warmEnd = Date.now();
  compare(calculationCoreGolden(warm), calculationCoreGolden(reference));
  const weatherRequests = requests.filter(r => r.url.includes('/weather/msm/'));
  if (weatherRequests.length !== coldRequests.length) throw Error('Warm reload downloaded Weather again');
  if (requests.some(r => new URL(r.url).pathname.startsWith('/api/'))) throw Error('Local called Legacy');
  if (crashes.length) throw Error(JSON.stringify(crashes));
  stop = true; await sampling;
  const report = {
    status: 'PASS', measured_at: new Date().toISOString(), browser: await browser.version(),
    user_agent: await page.evaluate(() => navigator.userAgent), host_platform: process.platform,
    source_run: cold.outcome.selected_forecast_run_id, summary: cold.outcome.summary,
    numerical_reference: 'desktop GRIB/NetCDF (caller-supplied reference); abs <= 1e-8, exact display/provenance',
    startup_through_cold_ms: coldEnd - started, cold_calculation_ms: coldEnd - coldStart,
    warm_calculation_ms: warmEnd - warmStart, cold_weather_files: cacheBytes,
    cold_weather_body_bytes: cacheBytes.reduce((sum, entry) => sum + entry.bytes, 0),
    warm_weather_download_bytes: 0, weather_requests: weatherRequests,
    memory_method: 'sampled sum of resident working sets for the dedicated Browser process tree; not exact peak or WASM heap',
    memory, failures, crashes, api_requests: [],
    provenance: cold.workingRecovery.last_calculation.forecast_metadata,
  };
  writeFileSync(args['--output'], JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: report.status, browser: report.browser, run: report.source_run,
    cold_weather_body_bytes: report.cold_weather_body_bytes, warm_weather_download_bytes: 0,
    cold_calculation_ms: report.cold_calculation_ms, warm_calculation_ms: report.warm_calculation_ms }));
} finally {
  stop = true; await sampling;
  await context.close(); await browser.close();
}
