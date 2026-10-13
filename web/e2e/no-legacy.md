# No-Legacy validation handoff (#146, 2026-10-13)

Tested application: `0568df91da469a7f3111e12c84db432d2173ee53`, v1.22.7.
Production v1.20.1 (`a4de2a226dfa205b8be42fb62a710a1b06271ed8`) was not changed.
Only tests/configuration for this isolated suite and this report are delivered.
No product, workflow, VERSION, Rules, credentials or production settings changed.

## Results suitable for a PR description

- Chromium 151.0.7922.173: `no Legacy Local` passed (1.2m): synthetic Google authentication
  through the real SDK, same-context Project/Last Calculation reload, authenticated offline
  Local save, reconnect/reload and account separation. This harness has no Firestore Sync.
- Chromium: `no Legacy artifact` passed (28.7s): real static entry boot, calculation, save,
  reload and Last Calculation display. Migration URL absent from release configuration and
  all 8 generated JS files. Both executed paths recorded **0 Legacy request attempts**.
- Typecheck, synthetic static build, 9 static-artifact contract tests passed.
- GPT-6.1 Sol read-only review: no findings. Test collection: 9 cases, not 9 executions.
- **Not run:** `no Legacy: reload, fresh-device restoration, two-way sync, offline save
  and account boundary` **exists in `no-legacy.spec.ts`**, but Firestore emulator jar download
  returned `403: Domain forbidden` before any emulator/Rules/sync test ran. WebKit also
  remains untested after the browser distribution download was denied. No retry/mirror bypass.
- Real Google popup/redirect, production Rules/data, physical Safari/Edge and actual storage
  at the production origin require separate acceptance. New contexts test empty-device
  restore, not persistence after discarding a private profile. Persistence tests retain
  their context. The secondary context copies viewport only; it does not reproduce every
  mobile device setting. Emulator harness uses long polling, unlike production defaults.
- Source comparison: v1.20.1 and v1.22.7 both skip migration when its URL is omitted.
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

## Resume unexecuted tests

From the repository root, using the already available Firebase CLI:

```sh
# Optional only when using an installed system Chromium:
# export AUTONAVLOG_CHROMIUM_EXECUTABLE=/usr/bin/chromium
CI=1 firebase emulators:exec --only firestore --project demo-autonavlog-sync \
  'npm --prefix web run test:firestore && cd web && npx playwright test --config playwright.no-legacy.config.ts --grep "no Legacy: reload"'
```

The command runs existing Rules tests followed by the unexecuted sync case on all three
configured profiles. Add `--project chromium` to the Playwright command to run Chromium only.
Remove `--grep "no Legacy: reload"` to run all 9 cases, including the unexecuted WebKit Local/artifact
cases. No Legacy harness/FastAPI is started. Every case requires 0 Legacy attempts.

Original temporary logs: `/tmp/autonavlog-no-legacy-*.log`; ignored outputs:
`web/public-local`, `web/dist-static`, `web/node_modules`, `/tmp/autonavlog-no-legacy-browser`.
These and the synthetic feed are **not committed**. Ports 4188/5188/8088 were stopped at handoff.
