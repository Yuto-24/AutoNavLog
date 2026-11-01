# Account Sync project shards

## Purpose and dependency

Split the remaining Account Sync critical path across ordinary GitHub-hosted runners without
reducing tests. PR #326's readiness/encoding and verified release-base fixes are now in main
at `b42f1b3c082b60452aa016c807d68000cc3bdf6b` and are inherited unchanged, not duplicated in
this diff. This PR targets main, which now includes PR #330's Local/Python split and PR #333's MSM forecast coverage. The PR diff itself remains limited to Account Sync sharding. No paid runner, permissions/protection change, main merge, or deployment is part
of this change.

The main synchronization resolves adjacent workflow job names, documentation, and runtime matrix
assertions by preserving all three shard configurations. Main's VERSION and CHANGELOG are unchanged.
The release-source regression suite includes all 109 checks from the merged dependency.
Fresh hosted CI is required for this synchronized head; earlier stack CI is not evidence for
the new integrated Local/Python/Account Sync configuration.

## Suites and independent state

| Runner | Commands / coverage |
| --- | --- |
| chromium | Every sync test in project chromium (13 at implementation) |
| ipad-webkit | Every sync test in project ipad-webkit (13) |
| iphone-webkit | Every sync test in project iphone-webkit (13) |
| contracts | All Firestore Rules (4), storage-upgrade (16), then migration (6) |

Each runner starts its own Firestore emulator with the same pinned Firebase CLI and
`firebase.json`. Rules and indexes are loaded from that configuration at emulator startup;
`test:firestore` is a verification suite, not a prerequisite that installs rules for later suites.
Rules tests, sync application workflows, storage-upgrade cloud fixtures, and real migration
seeding use their own random subjects/records. None consumes records created by a different
suite. Static synthetic identities in readiness/error fixtures do not cross runner boundaries.
The migration harness is still available independently in every runner; contracts keeps both
Chromium and iPad migration projects, and both storage-upgrade projects, unchanged.

All existing suite scripts, test assertions, timeout/retry settings, and worker counts remain.
Sync keeps `--workers=2` without `fullyParallel`; a single project/file can use only one worker,
preserving its internal ordering. This change adds CPU isolation across projects rather than
increasing same-runner concurrency. Each failure artifact includes its suite name.

The aggregate `account-sync` check retains the previous check name. `always()` makes it evaluate
failed/cancelled/skipped/missing prerequisites; its first command accepts only success. Proven
docs-only changes retain the original skip behavior. The aggregate also compares full Playwright
collection with the union of all three project selections. A new unassigned project, duplicate,
empty selection, or collection error fails rather than silently shrinking coverage. Contracts
uses whole configs (not project filters), since storage and migration project names differ from
sync and migration intentionally has no iPhone project.

## Evidence and verification

The unchanged Account Sync in [PR #330's successful run](https://github.com/Yuto-24/AutoNavLog/actions/runs/41275766965)
took 13m23s; workflow elapsed about 13m38s. That is a motivation, not a matched comparison against
this stack's inherited readiness fixes. Target is an Account Sync aggregate under ten minutes;
actual hosted runtime and completion must be measured, including setup and aggregate collection.

Local checks: shell dispatch/stop-on-failure/unknown-suite tests, workflow aggregate contract,
Node collection identity/union regressions, full 39-test project union and 4/16/6 contract
collections, typecheck, ruff, and release/version source validation. CI-only changes do not need
UI screenshots; real emulator/browser execution is validated by hosted CI. No successful local
collection is presented as a browser runtime pass.

Reproduce collection verification with:

```bash
npm --prefix web ci
node --test web/scripts/check-account-sync-partition.test.mjs
node web/scripts/check-account-sync-partition.mjs
```

For runtime reproduction, install the workflow dependencies, prepare the Local fixtures, start
the migration harness as in the workflow, then use a fresh emulator for each invocation of
`sh scripts/run_account_sync_suite.sh <chromium|ipad-webkit|iphone-webkit|contracts>`.
Do not share an emulator between simultaneous shard commands.
