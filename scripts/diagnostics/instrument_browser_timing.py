"""Temporary CI-workspace instrumentation; never changes production deployments."""
from pathlib import Path


def change(path, old, new):
    file = Path(path)
    source = file.read_text()
    assert source.count(old) == 1, (path, old, source.count(old))
    file.write_text(source.replace(old, new))


worker = 'web/src/local.worker.ts'
change(worker, 'async function initialize() {', '''const timingWorker = Math.random().toString(36).slice(2, 8);
function timing(stage: string, since: number) {
  console.log("CI_TIMING " + JSON.stringify({scope: "worker", worker: timingWorker, stage, ms: performance.now() - since, at: Date.now()}));
}
async function initialize() {
  const initStart = performance.now();
  let stageStart = initStart;''')
change(worker, '  await pyodide.loadPackage(', '  timing("loadPyodide", stageStart); stageStart = performance.now();\n  await pyodide.loadPackage(')
change(worker, '  await pyodide.runPythonAsync(`\nimport micropip', '  timing("loadPackages", stageStart); stageStart = performance.now();\n  await pyodide.runPythonAsync(`\nimport micropip')
change(worker, '  const assetUrl = ', '  timing("micropipInstall", stageStart); stageStart = performance.now();\n  const assetUrl = ')
change(worker, '  for (const filename of ', '  timing("manifest", stageStart); stageStart = performance.now();\n  for (const filename of ')
change(worker, '  pyodide.runPython(`\nfrom pathlib', '  timing("wheelDataFetchVerifyInstall", stageStart); stageStart = performance.now();\n  pyodide.runPython(`\nfrom pathlib')
change(worker, '  return pyodide;', '  timing("createPythonApplication", stageStart); timing("initializeTotal", initStart);\n  return pyodide;')
change(worker, '        try { await acquireWeather(pyodide); }', '        try { const weatherStart = performance.now(); await acquireWeather(pyodide); timing("acquireWeather", weatherStart); }')
change(worker, '      return pyodide.runPython(`\nimport json\nlocal_application.dispatch_response(local_dispatch_path', '      const dispatchStart = performance.now();\n      const dispatched = pyodide.runPython(`\nimport json\nlocal_application.dispatch_response(local_dispatch_path')
change(worker, 'error_operation=local_path)\n`) as string;', 'error_operation=local_path)\n`) as string;\n      timing("dispatch:" + path, dispatchStart);\n      return dispatched;')
client = 'web/src/localClient.ts'
change(client, '    let timer: ReturnType<typeof setTimeout> | undefined;', '    const timingStart = performance.now();\n    let timer: ReturnType<typeof setTimeout> | undefined;')
change(client, '      clearTimeout(timer);', '      clearTimeout(timer);\n      console.log("CI_TIMING " + JSON.stringify({scope:"request", operation:path, ms:performance.now()-timingStart, at:Date.now()}));')
sync = 'web/src/accountSyncController.ts'
change(sync, '  private running = false;', '  private readonly timingId = Math.random().toString(36).slice(2, 8);\n  private running = false;')
change(sync, '  async run() {', '  async run() {\n    const timingStart = performance.now();')
change(sync, '        const result = await this.remote.commit(mutations);', '        const commitStart = performance.now();\n        const result = await this.remote.commit(mutations);\n        console.log("CI_TIMING " + JSON.stringify({scope:"sync", instance:this.timingId, stage:"commit", ms:performance.now()-commitStart, at:Date.now()}));')
change(sync, '        try { await this.local.receive(await this.remote.list()); }', '        try { const receiveStart = performance.now(); await this.local.receive(await this.remote.list()); console.log("CI_TIMING " + JSON.stringify({scope:"sync", instance:this.timingId, stage:"listAndReceive", ms:performance.now()-receiveStart, at:Date.now()})); }')
change(sync, '      this.running = false;', '      console.log("CI_TIMING " + JSON.stringify({scope:"sync", instance:this.timingId, stage:"run", ms:performance.now()-timingStart, at:Date.now(), failures:this.failures}));\n      this.running = false;')
change(sync, '  wake = () => {', '  wake = () => {\n    console.log("CI_TIMING " + JSON.stringify({scope:"sync", instance:this.timingId, stage:"wake", at:Date.now()}));')

# Timing logs omit payloads, identities, URLs and request bodies.
# Automatic capture is off; existing explicit test screenshots stay runner-local.
# Context console includes pages created in a new context by multi-device tests.
for spec in ['auth', 'sync', 'map-route']:
    path = Path(f'web/e2e/{spec}.spec.ts')
    with path.open('a') as output:
        output.write('''\n
const timingContexts = new WeakSet<import('@playwright/test').BrowserContext>();
const timingBrowsers = new WeakSet<import('@playwright/test').Browser>();
test.beforeEach(async ({ browser, context }) => {
  const watch = (ctx: import('@playwright/test').BrowserContext) => {
    if (timingContexts.has(ctx)) return;
    timingContexts.add(ctx);
    ctx.on('console', msg => {
      if (msg.text().startsWith('CI_TIMING ')) console.log(msg.text());
    });
  };
  watch(context);
  if (!timingBrowsers.has(browser)) {
    timingBrowsers.add(browser);
    const original = browser.newContext.bind(browser);
    browser.newContext = async (...args) => { const ctx = await original(...args); watch(ctx); return ctx; };
  }
});
''')

Path('web/timing-reporter.ts').write_text('''import type { Reporter, TestCase, TestResult, TestStep } from '@playwright/test/reporter';
export default class TimingReporter implements Reporter {
  onStepEnd(test: TestCase, result: TestResult, step: TestStep) {
    if (step.duration >= 100 && !step.steps.length) console.log('CI_STEP ' + JSON.stringify({test:test.title, at:step.startTime.getTime(), file:step.location?.file.split('/').pop(), line:step.location?.line, category:step.category, ms:step.duration}));
  }
  onTestEnd(test: TestCase, result: TestResult) { console.log('CI_TEST ' + JSON.stringify({title:test.title, ms:result.duration, status:result.status})); }
}
''')
for suite in ['auth', 'sync', 'local']:
    Path(f'web/playwright.timing-{suite}.config.ts').write_text(f'''import {{defineConfig}} from '@playwright/test';
import original from './playwright.{suite}.config';
export default defineConfig(original, {{retries:0, reporter:[['line'],['./timing-reporter.ts']], use:{{...original.use,trace:'off',screenshot:'off',video:'off'}}}});
''')
