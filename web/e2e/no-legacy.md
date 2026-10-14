# No-Legacy validation handoff (#146, 2026-10-14)

Tested application: `0568df91da469a7f3111e12c84db432d2173ee53`, v1.22.7.
Production v1.20.1 (`a4de2a226dfa205b8be42fb62a710a1b06271ed8`) was not changed.
Only tests/configuration for this isolated suite and this report are delivered.
No product, workflow, VERSION, Rules, credentials or production settings changed.

## Current execution results

These are the 2026-10-14 rerun results, not the earlier partial successes.
Test baseline: `4dd5d60d49790e0981b293f522ce5df2adc57722`; only this report and
`no-legacy.spec.ts` changed. Product code and Rules remain unchanged.
Executed test source SHA-256:
`b795ca623d817718b3f25ea33e2fa058e386e19ea83faca4af3819cd30e9314b`.

| Profile | Cloud composite case | Local case | Static artifact case | Legacy attempts |
| --- | --- | --- | --- | --- |
| Chromium 151.0.7922.34 | passed, 41.0s | passed, 18.9s | passed, 9.1s | 0 in each case |
| iPad WebKit 26.5 | passed, 46.3s | passed, 21.7s | passed, 12.3s | 0 in each case |
| iPhone WebKit 26.5 | passed, 44.8s | passed, 25.9s | passed, 11.8s | 0 in each case |

- All **9 browser cases executed and passed**; 0 failed, 0 skipped, no retries.
- All three cloud cases completed every stage: authenticated save, same-context reload,
  empty-context Project/Last Calculation restore, offline dirty save, online receipt of 78,
  reverse sync of 76 and opening it in the other context, account boundary and Legacy zero.
  These are complete composite-case successes, not only intermediate stage successes.
- Shared real-SDK Firestore Rules tests: **4 passed** in this rerun. They run in Node and
  do not have a separate browser profile. Typecheck and diff whitespace checks passed.
- Recorded Firestore requests in all three cloud cases use `127.0.0.1:8088` with
  `demo-autonavlog-sync`; no production Firestore request was recorded.
- Node 24.21.0, Temurin Java 21.0.12.1+1, Firebase CLI 15.10.1, Firestore emulator 1.20.2
  and existing browser assets were reused. Official distribution checksums were verified
  during preparation; PATH/JAVA_HOME remain command-local.

## Previous failure and minimal correction

The previous cloud cases stopped when the original context tried to save FUEL 76:
its open WorkingCalculation still held 90 although its durable repository had received 78.
The saved-Project open action first flushes the open draft. That stale working state
therefore encountered the repository's remote-version conflict protection, and a modal
blocked the Save click. The trace-free diagnostic identified the blocked click directly.

The corrected test uses the normal **New** button and accepts its ordinary discard
confirmation, retaining the same context/account and saved Projects. It then opens the
received Project and asserts the input shows **78** before changing/saving **76**.
The other context also closes its old working input via New, opens the received Project,
and asserts **76**. Both directions still require the local dirty flag to clear.
The original context's stale input of 90 is explicitly checked before this UI reset.
All three complete cloud cases now pass without a product change, strongly supporting
the stale-working test sequence as the cause. This does not establish every possible
production concurrency behavior.

No DOM dialog removal, forced clicks, fixed sleeps, timeout extensions or weaker
expectations were added. The 240s test and 45s expect timeouts remain unchanged.
A context-dependent auto fixture saves Legacy attempts, sanitized request URLs and
stage statuses before automatic context teardown; the cloud case also saves before
closing its additional context. Pending stages remain `not-run` after an early failure.
Queries, request headers and bodies are omitted from the request log.

## Evidence and acceptance boundaries

Current evidence: `/tmp/autonavlog-no-legacy-complete-h8wqzwqp/evidence/`.
`rules-results.json` and `no-legacy-results.json` contain this rerun's results;
`results-summary.json` includes each case's Legacy count and cloud stage results.
Per-case outputs include `legacy-request-attempts.json`, `network-request-attempts.json`
and `case-progress.json`. Runtime configs only redirect reports/output and stop subsequent
cases after a failure; browser settings, trace policy and acceptance timeouts are retained.

Previous evidence is preserved separately:
`/tmp/autonavlog-no-legacy-resume-cjs1u4e4/evidence/report-ja.md`.
The earlier denied downloads and later Java/socket/browser setup failures are historical
results; they are not counted as successes or failures of this rerun.

Real Google popup/redirect, production Rules/data, physical Safari/Edge and actual storage
at the production origin require separate acceptance. New contexts test empty-device
restore, not persistence after discarding a private profile. Persistence tests retain
those contexts. The secondary context copies viewport only; it does not reproduce every
mobile device setting. The emulator harness uses long polling, unlike production defaults.
Source comparison remains: v1.20.1 and v1.22.7 skip migration when its URL is omitted;
v1.22.7 adds account lifecycle/generation handling. Old-version runtime was not retested.

## Prerequisites and preparation (repository root)

Use an existing authorized isolated test environment with Python 3.12 + repository
`.[test]`/vendored wheel dependencies, Node 24, Java 21, Firebase CLI **15.10.1** and
Firestore emulator **1.20.2** already available. Chromium/WebKit and their OS dependencies
must also be available. Do not retry denied downloads in the original environment.
Use a clean checkout without personal `.env*` files. Ports 8088, 5188 and 4188 must be free.
Only `demo-autonavlog-sync` and synthetic identities are used; no Google login or production
Firestore access is needed. Pyodide's existing CDN assets must be reachable/cached.

```sh
npm --prefix web ci
export AUTONAVLOG_TEST_FEED="$(mktemp -d /tmp/autonavlog-no-legacy-feed.XXXXXX)"
python3 - <<'PY'
import json, os, shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
source = Path('tests/fixtures/msm-portable')
target = Path(os.environ['AUTONAVLOG_TEST_FEED'])
catalog = json.loads((source / 'catalog.json').read_text())
now = datetime.now(timezone.utc)
catalog.update(generated_at=now.isoformat(), expires_at=(now + timedelta(hours=6)).isoformat())
for asset in catalog['assets']:
    shutil.copyfile(source / asset['file'], target / asset['file'])
(target / 'catalog.json').write_text(json.dumps(catalog))
(target / 'TEST_ONLY.txt').write_text('Synthetic timestamps; not real fresh weather. NEVER PUBLISH.\n')
PY
env -u VITE_LEGACY_MIGRATION_URL -u AUTONAVLOG_TEST_FIXTURES \
  VITE_FIREBASE_API_KEY=issue-184-test VITE_FIREBASE_AUTH_DOMAIN=autonavlog-test.firebaseapp.com \
  VITE_FIREBASE_PROJECT_ID=demo-autonavlog-sync VITE_FIREBASE_APP_ID=test-app \
  VITE_TAF_PROXY_URL=https://taf.example/taf AUTONAVLOG_MSM_FEED="$AUTONAVLOG_TEST_FEED" \
  npm --prefix web run build:static -- --allow-dirty
```

The feed and `dist-static` are **synthetic test artifacts, never publish them**.
Changed fixture timestamps are only for build structure tests, not weather freshness evidence.

## Re-run the suite

From the repository root, using the already available Firebase CLI:

```sh
# Optional only when using an installed system Chromium:
# export AUTONAVLOG_CHROMIUM_EXECUTABLE=/usr/bin/chromium
CI=1 firebase emulators:exec --only firestore --project demo-autonavlog-sync \
  'npm --prefix web run test:firestore && cd web && npx playwright test --config playwright.no-legacy.config.ts'
```

The command runs the shared Rules tests followed by all 9 browser cases on the three
configured profiles. Add `--project chromium` for Chromium only, or
`--grep "no Legacy: reload"` for the cloud composite cases only.
No Legacy harness/FastAPI is started. Every case requires 0 Legacy attempts.

Original temporary logs: `/tmp/autonavlog-no-legacy-*.log`; ignored outputs:
`web/public-local`, `web/dist-static`, `web/node_modules`, `/tmp/autonavlog-no-legacy-browser`.
These and the synthetic feed are **not committed**. Ports 4188/5188/8088 were stopped at handoff.
