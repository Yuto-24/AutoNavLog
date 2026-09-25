# Static build / feed / quota operations (#123)

[Static production](static_production.md) is the manual production contract;
[#121 acceptance](evidence/issue-121/remote-production-2026-09-24.md) remains its evidence.
This runbook adds CI orchestration, not an Application runtime. The output is still
ordinary Vite files with Pyodide wheels/data, served at the same origin. No Functions,
R2, paid runner, paid plan, billing activation, or automatic paid fallback is required.

## Ownership and activation

- `publish-github-release.yml` continues to publish VERSION/CHANGELOG GitHub releases.
  It does not publish Pages and does not authorize production changes.
- `test.yml` retains Python Reference, differential Local browser, schema/data,
  authentication, sync and TAF tests. It also runs operational safety tests in pytest
  and production artifact tests in the web job.
- `static-production.yml` builds a clean **full approved application SHA**, produces
  real MSM data, checks version/hash/size/freshness, and runs production Browser E2E
  without FastAPI. A dispatch with `publish=false` only builds/tests. No remote preview.
- The three-hour schedule is disabled unless `STATIC_AUTOMATION_ENABLED=true`.
  That variable is explicit authorization for recurring feed publication, not app upgrades.
  A manual app publication supplies a reviewed `source_sha` and `publish=true`.
- TAF deploy/rollback remains the independent [#145 manual contract](taf_proxy.md),
  with its existing CI tests/dry-run. Its ignored production configuration supplies
  the origin allowlist; never deploy the checked-in empty default allowlist as production.
  Pages never runs `wrangler deploy`.

**Not performed by PR #211:** create/change GitHub environments or variables/secrets,
Cloudflare/Firebase settings, scheduler activation, deployment, rollback, DNS or billing.
Before authorization, review the exact target, SHA, public configuration and plan evidence.

After explicit authorization, the operator configures:

1. GitHub `static-production` environment for unattended recurring feed updates,
   restricted to `main` without required reviewers after explicit recurring-policy
   approval. Use a separate `static-publication` environment, restricted to `main`
   with required reviewers, for one-off `publish=true` dispatches. Build-only
   dispatches use `static-validation`. A pending reviewer on every scheduled run is
   not unattended operation. Do not silently remove publication protection to
   unblock a run.
2. Repository variables `STATIC_ORIGIN=https://navmate.pages.dev`, `STATIC_APPROVED_SHA`
   (the full canonical production SHA), and the six public `VITE_*` settings documented
   in #121. Keep these in repository variables so build and monitor see the same values;
   do not shadow them with different environment values. No credentials in `VITE_*`.
3. `CLOUDFLARE_ACCOUNT_ID`; install environment secret `CLOUDFLARE_PAGES_TOKEN` in
   both `static-production` and `static-publication`, scoped only to Pages edit in
   the intended account. Keep #145 Worker credentials and configuration outside
   this workflow. Never expose the Pages token to the application build.
4. `STATIC_QUOTA_REPORT` using the schema below, then `STATIC_MONITOR_ENABLED=true`.
   Enable GitHub Actions failure notifications for the operator. Run the monitor once
   and verify receipt of an intentional test failure before relying on it.
5. Run build-only dispatch, inspect checks, then an approved publication. Confirm real
   origin/cache/ETag/404 and browser behavior per #121. Only then enable recurring updates.
   Cloudflare automatic Git builds/preview deployments remain disabled; use Direct Upload.

The existing repository is public (checked 2026-09-24); use standard `ubuntu-latest`
GitHub-hosted runners, not larger runners. Recheck public visibility and the current
[Actions billing policy](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
before activation/release. If visibility or eligibility changes, pause schedules and
use an existing machine with the same build commands; do not enable billing.

## Feed lifecycle and failure recovery

At minute 23 every three hours, the workflow reads canonical `release.json` and rejects
any mismatch with `STATIC_APPROVED_SHA`. It restores recent immutable NPZ files from
production, verifies byte lengths/hashes, and seeds the existing producer. Seven-day
Run retention survives runner replacement and cache eviction. No Project data is sent.
A failed read/hash check/producer/build/test stops before upload. A retained-only
producer result with no newly prepared Run is also rejected; renewing only the timestamp
is not a successful refresh. The previous deployment
is retained, and expired weather still fails closed in the browser.

The producer's six-hour catalog lifetime leaves roughly three hours for delay/retry.
The independent hourly monitor warns with fewer than two hours remaining. GitHub cron
is best-effort and can be delayed/dropped; public inactive repositories can have schedules
disabled. Check Actions schedule status and notification delivery during the daily audit.
Neither this workflow nor GitHub cron promises weather availability during outages.

The workflow rebuilds the pinned source/configuration automatically, so the operator no
longer manually rebuilds/redeploys merely because the MSM catalog expires. Same-source
feed publication rejects configuration drift. All app/feed jobs share a non-cancelling
concurrency group. Quota/source/artifact checks repeat immediately before upload.
Atomic Pages upload switches payload and catalog together. Post-upload checks compare
canonical **whole release inventory**, not just the version, and check freshness.
Failure after upload needs investigation; it does not automatically roll production back.

No large build artifacts or raw MSM caches are stored in Actions. Production itself is
the next retention source; job summaries record release hash and size/freshness report.
For an app release, retain the reviewed archive and report in the operator's existing
private local release store as in #121. Seven-day retained weather increases transfer
and build duration; the full artifact size/file count checks still run every time.
Measure duration and transfer before activation; increase neither paid quota nor retries
implicitly. An outage beyond retention requires a fresh feed; historical Runs older than
seven days are intentionally unavailable.

Recovery: inspect the failed run and independent monitor, correct the upstream/configuration
problem, then dispatch the same pinned SHA. Do not use `--allow-dirty`, expired-weather
opt-outs, synthetic forecasts, a different application branch or Legacy fallback.

## Quota evidence and monitoring

`static-monitor.yml` evaluates quota evidence hourly, including when weather failed.
This is continuous **evaluation**, not an invented provider usage API: aggregate account
counters unavailable to the current token must be exported/read by the operator. Missing,
null, non-finite, stale (>36 hours), or unattributed observations fail, never count as zero.
The operator updates plan evidence at least daily and refreshes usage after each
provider reset (UTC and Pacific); before that refresh the monitor reports unknown/current-
period evidence missing, not healthy zero. These usage alerts do not stop MSM publication.
Plan/billing evidence older than 36 hours blocks publication until it is rechecked.
An independently authorized read-only collector can produce the same JSON. A monitor failure requires action; absence of a run
is not a healthy reading. Notification transport is GitHub's failure notifications, not
an added paid alerting service. Its live delivery remains an activation gate.

Inventory / exact counter source:

| Dependency | Scope and window to record | Read-only source / exhaustion behavior |
| --- | --- | --- |
| Pages Free | All account Pages builds, calendar month | Cloudflare account Pages build usage. Direct Upload prebuilds locally; deployment count and Workers Builds minutes are **not** Pages build usage. Warn at 80%; Direct Upload does not consume this build counter. |
| Workers Free / optional TAF | All Workers, provider daily quota window | Cloudflare Workers Analytics/account GraphQL; include rejected and cached requests. TAF becomes unavailable, Local survives. |
| Firebase Spark / Firestore | Entire project/default free database: reads/writes/deletes in provider day, stored bytes now, outbound calendar month | Firebase Usage / Cloud Monitoring / plan console. Include Rules reads, listeners and retries. Sync can stop; preserve Local outbox and NAV LOG. |
| GitHub Actions | Owner-shared artifact/cache storage, current billing window | GitHub Billing/Actions storage; public standard-runner eligibility. No scheduled artifact uploads/cache writes are added. |
| Pyodide/jsDelivr, PyPI, JMA/RISH, OSM/GSI | Availability and acceptable-use policies | No billed account assumed; do not claim unlimited service/SLA. Existing fetch/cache failures remain explicit. |

Workers request CPU ceiling and Pages per-file/count limits must also be rechecked at
release. Static artifact checks enforce the existing Pages snapshot. No durable rule
asserts that provider limits never change.

Create JSON following `docs/static_quota.example.json`. Every metric needs `used`,
`limit`, `observed_at` (timezone), `scope` (actual account/project identifier), `window`
(`{ "start": "ISO", "end": "ISO" }` or `"instant"` for storage), and `source` (dashboard/API evidence).
Daily windows are UTC for Workers and America/Los_Angeles for Firestore; monthly
windows use UTC for Pages and America/Los_Angeles for Firestore outbound. Start/end
must match the current period. Metric scopes and report `scopes` must match
`CLOUDFLARE_ACCOUNT_ID`, `VITE_FIREBASE_PROJECT_ID`, and `GITHUB_REPOSITORY_OWNER`.
`plans_observed_at` records a separate, daily plan/billing audit.
The example deliberately contains null usage and is **not a passing audit**. Do not use
#121's September snapshot as current usage, nor relabel conservative estimates as counters.
If stored bytes or outbound cannot be obtained, record unknown and keep the gate open.

```sh
CLOUDFLARE_ACCOUNT_ID=your-account VITE_FIREBASE_PROJECT_ID=your-project \
  GITHUB_REPOSITORY_OWNER=your-owner STATIC_QUOTA_REPORT="$(cat /private/path/quota.json)" \
  python3 scripts/static_ops/operations.py quota
python3 scripts/static_ops/operations.py monitor --origin https://navmate.pages.dev
```

At >=80% of a recorded current limit, the check warns/fails; at 100% the provider's
Free/Spark behavior may already reject requests. Pause relevant workloads, investigate
all account consumers, reduce traffic or wait for reset. Do not upgrade automatically.
A failing **plan/billing** preflight prevents publication. Usage warnings remain
independent: Direct Upload consumes no Pages builds and this job stores no Actions
artifacts; TAF/Sync exhaustion must not cause an unrelated weather outage. The monitor
fails on any unknown/stale/near-limit usage; the publisher uses `quota --publication`
to check current, target-bound Free plan evidence. It never treats this as proof of
healthy TAF/Sync usage.
`limits_checked_at` must be refreshed against official documentation at every release
(and at least monthly). The report records Free/Spark/public-standard and disabled
billing; recheck these declarations daily with usage. An API permission error is unknown.

Official limits checked 2026-09-24: [Pages](https://developers.cloudflare.com/pages/platform/limits/),
[Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/),
[Workers](https://developers.cloudflare.com/workers/platform/pricing/),
[Firestore](https://firebase.google.com/docs/firestore/quotas),
[Firebase plans](https://firebase.google.com/pricing/), and Actions billing above.
Do not conflate Direct Upload deployments with billed Pages builds. Three-hour renewal
means at most 248 planned renewals in a 31-day month, plus explicitly dispatched app builds.
Validate actual producer/browser duration and retained payload volume during activation.

## App release and rollback checklist

- Confirm the existing test workflow (Reference/differential/browser/sync), independent
  review and release contract; GitHub release publishing stays separate.
- Recheck current limits, plan/billing state, shared usage, and report freshness. Inspect
  cold production build/runtime/reference hashes, artifact budgets and production E2E.
- Before app publication, pause `STATIC_AUTOMATION_ENABLED`, wait for the shared workflow
  group to become idle, and record current successful production deployment ID, full SHA,
  config, artifact and schema compatibility. `publish=false` first; no preview required.
- After approval, dispatch reviewed full SHA with `publish=true`. Complete #121 real-origin
  acceptance; update `STATIC_APPROVED_SHA` only after success, then resume recurring policy.
  Until updated, the scheduler stops on mismatch rather than reverting the new app.
- For rollback, pause the schedule and wait for in-flight upload completion **before**
  the separately authorized Pages rollback. Restore a successful production deployment
  using #121's manual procedure. Record ID/SHA/config; keep the same origin/storage.
- Set the approved SHA/config to the restored release before resuming. If its catalog is
  expired, dispatch that SHA with a fresh producer run; never weaken freshness checks.
  Existing Project / Last Calculation, FTD and saved NAV LOG remain available meanwhile.
- Worker rollback follows #145 independently; app rollback does not roll back Firebase
  Rules, schema, billing or TAF. Incompatible schema requires a forward fix.

## Acceptance still requiring production authorization

Repository verification proves build/gate logic, not remote operation. Activation must
record: environment protection and least-privilege credentials, current Free/Spark/shared
usage, notification delivery, at least two successful scheduled renewals across the old
catalog expiry, canonical catalog/payload continuity, and controlled same-origin rollback
with scheduler paused and saved Local data retained. Update #123 and parent #116 only
with those results; do not mark the production automation gate complete from local tests.
