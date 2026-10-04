# Static build / feed / quota operations (#123)

[Static production](static_production.md) is the manual production contract;
[#121 acceptance](evidence/issue-121/remote-production-2026-09-24.md) remains its evidence.
This runbook adds CI orchestration, not an Application runtime. The output is still
ordinary Vite files with Pyodide wheels/data, served at the same origin. No Functions,
R2, paid runner, paid plan, billing activation, or automatic paid fallback is required.

The canonical production origin is **`https://navmate.yuto24.com`**. Pages / Direct
Upload remains the hosting backend. Follow [custom-domain cutover](custom_domain_cutover.md)
before activation: publication, retention restore, monitoring and rollback acceptance
all target this origin. The historical #121 pages.dev evidence is not new-origin acceptance.
`STATIC_ORIGIN` remains an operator-owned GitHub variable, not a tracked workflow default.

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
2. Repository variables `STATIC_ORIGIN=https://navmate.yuto24.com`, `STATIC_APPROVED_SHA`
   (the full canonical production SHA), and the six public `VITE_*` settings documented
   in #121. Keep these in repository variables so build and monitor see the same values;
   do not shadow them with different environment values. No credentials in `VITE_*`.
3. `CLOUDFLARE_ACCOUNT_ID`; install environment secret `CLOUDFLARE_PAGES_TOKEN` in
   both `static-production` and `static-publication`, scoped only to Pages edit in
   the intended account. Keep #145 Worker credentials and configuration outside
   this workflow. Never expose the Pages token to the application build.
4. Complete and accept unattended provider collection before enabling monitoring.
   The existing `STATIC_QUOTA_REPORT` workflow is the legacy implementation, not the
   accepted steady-state model. Do not periodically replace that variable to unblock
   activation. Failure-notification receipt was already accepted on 2026-10-03; the
   remaining gate is healthy current-evidence collection. See the read-only checklist below.
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
The post-upload verifier immediately checks canonical `release.json`, then retries failed
checks after 10 seconds for up to 300 seconds (monotonic deadline). Each check runs the
unchanged `check_candidate.py --remote` in a child process with at most 30 seconds,
clipped to the remaining overall budget; a hung or slowly streaming response cannot
extend the polling window. Inventory mismatch, HTTP/network errors, invalid JSON and
other checker failures are retried within that budget. Redirects remain rejected on
every attempt and are never followed; only exact inventory equality passes. Reads
retain the existing `Cache-Control: no-cache` header, origin and byte budget.
Deadline expiry fails closed, including a match returned at/after the deadline. Attempt
diagnostics go to the job log; only successful identity verification enters the summary.
The fresh MSM monitor runs **after** identity verification succeeds, never on timeout.
These retries do not re-upload, change the approved SHA/configuration gates, or authorize
another deployment. Failure after upload needs investigation; it does not automatically
roll production back.

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

`static-monitor.yml` evaluates schema v2 evidence hourly even if weather failed. It
prints validation failures and successful evaluations into the job summary. This is
continuous **evaluation**, not automatic collection. Missing, malformed, non-finite,
wrong-target, future or stale (>36 hours) evidence fails. Refresh observations after
each provider reset as well as daily; a recently fetched previous-month report is not
current-month evidence. An API permission error, unavailable dashboard or missing row
is unknown, never zero. Do not enable billing to collect evidence.

Plan/billing observations must be refreshed daily. Publication uses `quota --publication`
and checks current, target-bound Free/Spark/public-standard and **boolean false**
`automatic_billing`, plus `limits_checked_at` (at release and at least every 31 days).
Usage failure does not stop MSM publication: Direct Upload uses no Pages builds and the
feed workflow adds no artifact uploads/cache writes. This preserves the independence of
Local calculation, TAF, Sync and weather. A passing publication gate is not healthy usage.

### Schema v2

Start from [the deliberately incomplete example](static_quota.example.json). The Python
validator in `scripts/static_ops/operations.py` is the executable schema; it requires
exact metric names and variant fields. The example contains null readings and placeholder
evidence, and **must fail** until actual current observations replace them. Its free limits
are a 2026-10-01 documentation snapshot; recheck provider specifications at each release.

Report fields are `schema_version: 2`, `plans`, `plans_observed_at`, `limits_checked_at`,
`scopes`, and `metrics`. Scopes must match `CLOUDFLARE_ACCOUNT_ID`,
`VITE_FIREBASE_PROJECT_ID`, `GITHUB_REPOSITORY_OWNER` and `GITHUB_REPOSITORY`
(`owner/repository`). Publication also accepts the previous three-scope plan-only report.

Every metric has `observation`, `unit`, timezone-qualified `observed_at`, target `scope`,
`window`, and nonblank `source`. Sources identify a retained dashboard export/screenshot
or API response with its query/filter and observation time; an official limits URL alone
is not actual account usage evidence. No credentials or Project data belong in this JSON.
Nested billing/configuration evidence shares the parent metric's scope and observation time;
cache billing has its own explicit monthly window rather than the instant usage window.
All of those sources must be audited together, not copied from an older observation.

| Metric | Observation, unit and scope | Window / evidence |
| --- | --- | --- |
| `pages_builds` | `counter`, `count`, Cloudflare account | UTC calendar month; all account Pages builds |
| `workers_requests` | `counter`, `count`, Cloudflare account | UTC day; all Workers requests, including cached/rejected requests |
| `firestore_reads`, `firestore_writes`, `firestore_deletes` | `counter`, `count`, Firebase project | America/Los_Angeles day; whole free database |
| `firestore_storage_bytes` | `counter`, `bytes`, Firebase project | `instant`; actual stored data including indexes/metadata |
| `firestore_outbound_bytes` | `counter` or `provider_unobservable`, `bytes`, Firebase project | America/Los_Angeles calendar month; see bounded exception below |
| `actions_storage_gb_hours` | `accrued_billing`, `GB-hours`, GitHub owner | UTC calendar month; Actions accumulated storage and separate shared allowance evidence |
| `actions_cache_storage_bytes` | `counter`, `bytes`, GitHub repository | `instant`; current repository cache usage and configured capacity |

Period windows are `{ "start": "ISO", "end": "ISO" }`, with exact current-period
boundaries and `start <= observed_at < end`. Pacific boundaries follow daylight saving
changes. Numeric `counter` variants require finite, non-boolean `used >= 0` and `limit > 0`.
At >=80% the monitor fails. Unknown usage cannot be supplied as a numeric estimate.

### Collecting provider evidence

**Cloudflare:** read account Pages build usage. If reconstructing from the
[Deployments API](https://developers.cloudflare.com/api/resources/pages/subresources/projects/subresources/deployments/methods/list/),
retain all pages of results for all account projects and inspect actual build stages,
triggers and timestamps for the current month. Deployment totals, `ad_hoc` Direct Upload,
skipped Git builds and Workers Builds minutes are not interchangeable with Pages builds.
Only a complete audit proving no build started supports zero; ambiguous/deleted history
requires another authoritative source. Use Workers account Analytics/GraphQL for the UTC
daily total; do not report only this application's Worker. Recheck the
[Pages limits](https://developers.cloudflare.com/pages/platform/limits/),
[Direct Upload contract](https://developers.cloudflare.com/pages/get-started/direct-upload/)
and [Workers daily reset](https://developers.cloudflare.com/workers/platform/limits/#daily-requests).
TAF may become unavailable on exhaustion; Local calculation survives.

**Firestore:** read Firebase Usage / Cloud Monitoring for the entire project's free
database, including stored data. Retain the actual provider observation and its scope;
do not reconstruct reads from application activity. The
[usage dashboard](https://firebase.google.com/docs/firestore/monitor-usage) is approximate
and can differ from billed operations (including Rules/index reads); it is not an exact
billing ledger. Preserve this limitation in source evidence. The
[free quotas](https://firebase.google.com/docs/firestore/quotas) include 10 GiB/month outbound.
The [pricing documentation](https://firebase.google.com/docs/firestore/pricing#network)
points to billing export for bandwidth. That does not establish that a Spark project
without billing exposes a monthly counter.

Only `firestore_outbound_bytes` may use `observation: "provider_unobservable"`. It requires
`used: null`, `limit: 10737418240` (10 GiB), `unit: "bytes"`, and
`reason: "spark_no_usage_counter"`, alongside current monthly window, project, timestamp
and source evidence. Audit and retain which Firebase Usage, Cloud Monitoring and Quotas
surfaces were checked and why none provides this counter for the current Spark/billing-off
configuration. This is the bounded configuration finding in #230, **not** a claim that
Firestore can never expose the metric. An expired token, denied permission, failed fetch,
or ordinary missing data does not qualify. Spark / disabled billing evidence still expires
after 36 hours. Missing stored bytes or any other numeric counter still fails.

A successful result lists `unobservable: ["firestore_outbound_bytes"]`: the zero-cost
policy passes with an explicit visibility gap, not proof of outbound headroom or service
availability. Do not compute a percentage or substitute zero. If a read-only counter
becomes available without billing, change the observation to `counter`, remove `reason`,
and supply actual `used` / current `limit`; normal >=80% monitoring resumes.

**GitHub artifact/Packages storage:** use owner Billing & licensing → Usage, filtered to
the current month and Actions storage, or an authorized read-only
[billing usage API](https://docs.github.com/en/rest/billing/usage). Preserve quantity,
unit/SKU, period and billed amount. `actions_storage_gb_hours.used` is the provider's
**accrued Actions storage** quantity; `billed_amount_usd` must be numeric zero. There is
no synthetic `limit` and no bytes conversion or invented 80% ratio for this variant.

Additionally, `shared_allowance` must contain `coverage: "actions_artifacts_and_packages"`,
`status: "within_included"`, numeric-zero `billed_amount_usd` for the whole shared pool,
and a `source` proving both the current owner-wide included allowance status and bill.
Include other repositories/private Packages consumers; an Actions-only row does not prove
shared allowance headroom. Net zero from credits/discounts is insufficient without evidence
that storage remains inside the included allowance. If this evidence cannot be obtained,
keep the gate failed. No paid plan or billing activation is required by this contract.
Historical September `104.9 GB-hr / $0` in #230 is not an October reading.
[Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
distinguishes accrued usage from current storage and identifies the shared pool;
[Packages billing](https://docs.github.com/en/billing/concepts/product-billing/github-packages)
confirms that pool. Deleting current artifacts does not erase accrued usage. Provider
reporting can lag; daily evidence is not a real-time no-charge guarantee.

**GitHub cache:** this repository's normal CI uses dependency caches, so v2 requires a
separate observation. Read `GET /repos/{owner}/{repo}/actions/cache/usage`
(`active_caches_size_in_bytes`) or the repository Actions → Caches display. See the
[cache API](https://docs.github.com/en/rest/actions/cache#get-github-actions-cache-usage-for-a-repository).
Record the repository's cache settings in `configuration_source`, and the configured
capacity in `configured_limit` (bytes); it must be positive and no larger than the
free `limit`, which must equal the current 10 binary GB (10737418240 bytes) per repository.
A provider change to this ceiling or the outbound exception quota requires a reviewed
validator/example update, not relabeling paid capacity as free. Cache is not part of the
artifact/Packages pool; its billing rows can show only excess usage and cannot be used as
current cache size. The nested `billing` requires an explicit current UTC month `window`,
`source` for this repository's Actions Cache Storage bill, and numeric-zero
`billed_amount_usd`. Current size/configuration alone cannot rule out charges already
accrued before a deletion or limit reduction. An isolated missing row or failed query does not prove zero. A complete provider export
covering this repository and current month with no cache-overage SKU can support zero
billed amount; retain that coverage audit in `source`. This is no excess charge, not a
claim that cache usage was zero.
Audit owner-wide billing settings separately with the plan evidence;
this metric checks the target repository, not all owner repositories' caches.

Workers CPU ceilings and Pages per-file/count budgets also need release-time checks.
Pyodide/jsDelivr, PyPI, JMA/RISH and OSM/GSI availability and acceptable-use policies remain
external dependencies, with no paid account, unlimited-service or SLA assumption.

### Migration

Do not automatically rewrite the live `STATIC_QUOTA_REPORT`. Prepare a private replacement:

1. Set `schema_version: 2`; keep current plan/billing evidence and add `github_repository`
   to `scopes`. Add `observation: "counter"` and `unit` to the six existing numeric metrics.
2. Choose actual numeric or the narrowly justified unobservable variant for outbound.
   Do not relabel an old/permission-denied reading as provider-unobservable.
3. Remove `actions_storage_bytes`. Obtain current `actions_storage_gb_hours` billing and
   shared allowance evidence, plus separate `actions_cache_storage_bytes` evidence.
   Do not convert instant bytes or previous-period GB-hours into current counters.
4. Validate locally with the intended targets. Old/unversioned monitor reports fail with
   an explicit schema-v2 migration error; there is no silent compatibility conversion.
   Plan-only `quota --publication` remains compatible with the old three-scope format.

```sh
CLOUDFLARE_ACCOUNT_ID=your-account VITE_FIREBASE_PROJECT_ID=your-project \
  GITHUB_REPOSITORY_OWNER=your-owner GITHUB_REPOSITORY=your-owner/your-repo \
  STATIC_QUOTA_REPORT="$(cat /private/path/quota.json)" \
  python3 scripts/static_ops/operations.py quota
# Same environment/report, independent publication plan gate:
# python3 scripts/static_ops/operations.py quota --publication
python3 scripts/static_ops/operations.py monitor --origin https://navmate.yuto24.com
```

After #230 is reviewed/merged and separately authorized, the operator can install the
validated report and resume #123 acceptance. A clean quota check alone does not authorize
scheduler activation or deployment. Refresh all period evidence at reset, exercise live
failure notifications, then complete the scheduled-renewal and rollback gates below.
The monitor evaluates evidence even when catalog checks fail; absent/skipped runs are not
healthy readings. At warning/exhaustion, reduce workloads or wait for reset; never upgrade
automatically. GitHub failure notifications remain the transport, with live receipt still
an acceptance requirement. Application / calculation / Project schemas are unchanged.

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
- Keep the custom domain, `STATIC_ORIGIN`, migration origin and canonical-only TAF
  allowlist through artifact rollback. Returning users to pages.dev is an origin/data
  cutover, not a same-origin rollback; use the separate cutover recovery procedure.

## Acceptance still requiring production authorization

Repository verification proves build/gate logic, not remote operation. Activation must
record: environment protection and least-privilege credentials, current Free/Spark/shared
usage, notification delivery, at least two successful scheduled renewals across the old
catalog expiry, canonical catalog/payload continuity, and controlled same-origin rollback
with scheduler paused and saved Local data retained. Update #123 and parent #116 only
with those results; do not mark the production automation gate complete from local tests.

## Unattended collection investigation (#123, 2026-10-03)

The [latest #123 decision](https://github.com/Yuto-24/AutoNavLog/issues/123#issuecomment-5972103619)
supersedes periodic manual `STATIC_QUOTA_REPORT` renewal as the operating model.
**Unattended collection is not implemented or accepted yet.** The workflows above
still evaluate operator reports; they must not be activated on the strength of the
new probe. The schema example and migration commands remain diagnostic tools, not
an unattended collection solution. September evidence is historical only.

`python3 scripts/static_ops/probe_providers.py` performs bounded, read-only API
capability probes for Cloudflare, Google and GitHub. It does not read an old report,
construct a schema-v2 report, refresh any observation/limits timestamp, or authorize
publication. It always exits 1 with `status: BLOCKED`, including when every endpoint
is reachable. It continues independent probes after failures. JSON output contains
fixed check names/status codes and unresolved evidence requirements only; no response
body, token, account identifier, private repository name, usage quantity, SKU, amount,
or owner-wide billing row is logged or saved. The request window uses UTC for Workers
and GitHub, and Pacific local midnight for Firestore (including DST).

Run it only with already authorized read credentials supplied through the environment:

- `STATIC_CLOUDFLARE_READ_TOKEN`: scoped to the target account; read access to Pages,
  subscriptions and Analytics. This is separate from the Pages deployment token.
- `STATIC_GOOGLE_ACCESS_TOKEN`: an existing short-lived access token authorized to read
  project billing state and Cloud Monitoring. The probe neither creates nor renews tokens,
  enables APIs/billing, changes IAM, nor configures OIDC. Token automation still needs
  separate approval and verification; a short-lived token secret is not unattended auth.
- `STATIC_GITHUB_READ_TOKEN`: target repository Actions read and personal-owner Plan read
  for the billing usage API (subject to actual account eligibility). `GITHUB_TOKEN` must not be assumed to read owner billing.
  The current probe uses the documented **personal user** endpoint for Yuto-24; an
  organization owner needs an independently verified adapter and permissions.
- The existing four scope variables: `CLOUDFLARE_ACCOUNT_ID`,
  `VITE_FIREBASE_PROJECT_ID`, `GITHUB_REPOSITORY_OWNER`, `GITHUB_REPOSITORY`.

The tool has no write endpoints, configurable hosts, raw export option, or credential
CLI arguments. Redirects are rejected, responses are bounded to 8 MiB, requests to 40,
and the collection time budget to 180 seconds (an in-flight socket read can take up to
15 additional seconds). No credentials were configured or account APIs probed during
implementation. Offline fixtures verify transport failure/redaction and negative cases;
they are not recordings of actual account responses.

### Ownership of remaining decisions and evidence work

API selection, parsing, aggregation, pagination, clock handling and evidence semantics
are engineering responsibilities, not questions for the operator to answer. The previous
list of open API questions describes technical investigation; it is not a request that
the user select an API. No new product decision is currently needed: unattended operation,
zero cost, strict failure on unknown evidence, the outbound exception and publication
warning behavior are already defined by #123/#230.

**Operator decisions/approval, when a concrete setup is ready:** authorize the exact
least-privilege credential/IAM/OIDC/secret changes; later approve live monitor acceptance
and recurring publication activation. No setup changes or activation have been performed.
If research establishes that a requirement cannot be met with supported, zero-cost,
unattended access, report that conflict with evidence. Do not invent a weaker policy,
paid dependency, periodically renewed manual report or extra unobservable exception.
There is no request now to choose an API or reinterpret evidence semantics.

**Actual account access needed to verify facts (no credential setup in this task):**

| Scope | Read capabilities to verify | Facts that public documentation cannot prove |
| --- | --- | --- |
| Target Cloudflare account | Pages Read, Account Analytics Read, and the subscriptions endpoint's billing-read capability (`#billing:read` in the official schema); verify token support and precise grants before proposing setup | Current subscriptions/Free status, account-wide build coverage, Workers data/coverage and no-data meaning for this account |
| Target Firebase/Google project | `resourcemanager.projects.get`, `monitoring.timeSeries.list`, `monitoring.metricDescriptors.list`; `datastore.databases.list` and `firebase.projects.get` for identity/free-database checks | Current billing state, active Firebase identity, free database identity, real operation/storage series and whether outbound counters/audit surfaces are available on Spark |
| GitHub personal owner Yuto-24 and target repository | Personal-user `Plan: read` for `/users/{username}/settings/billing/usage`; repository `Actions: read` for cache usage and storage limit | Actual product/SKU/unit/gross/discount/net rows; owner-wide artifact/Packages pool coverage; personal spending/payment controls; accrued repository cache charges |

Tokens need only be made available through an approved secure mechanism, never chat or
tracked files. Account-wide billing responses must remain private and in memory; the
existing probe intentionally returns only fixed statuses. Owner-wide read visibility is
needed to evaluate the shared allowance, not permission to disclose its constituent rows.
The Google token also needs appropriate read OAuth scopes; project billing supports
`cloud-billing.readonly`, Monitoring supports `monitoring.read`. An existing short-lived
token is useful for investigation but does not establish unattended renewal.

**Technical findings and remaining engineering work:**

| Evidence | Finding from authoritative documentation / engineering action |
| --- | --- |
| Cloudflare plans and Pages builds | Inspect subscriptions and all account Pages projects/deployments with complete pagination. Establish retention/deletion coverage before treating deployment-history enumeration as an authoritative monthly build total. Direct Upload and skipped Git deployments are not builds. The documented subscription schema exposes billing-read access; actual least-privilege token eligibility needs account verification. |
| Workers requests | Cloudflare's metrics documentation excludes WAF/security-blocked requests from invocation totals and distinguishes cached subrequests. Therefore invocation analytics alone does not prove the existing all-account cached/rejected quota contract. Resolve which rejections count against provider quota and compare the account's quota surface; do not silently change scope or interpret an empty response as zero. |
| Firebase plan and Firestore operations/storage | Public documentation now identifies `document/read_ops_count`, `write_ops_count`, `delete_ops_count` and `storage/data_and_index_storage_bytes`, on `firestore.googleapis.com/Database`. The probe uses the documented `resource_container` label (not `project_id`). The native collector now implements complete pagination, free-database binding, DELTA aggregation and latest GAUGE selection from native timestamps; actual account access and series coverage remain unverified. The Usage dashboard is an estimate, not an exact billing ledger; #230 already preserves that limitation. No new user policy decision is needed for this documented limitation. |
| Firebase billing/plan interpretation | Google defines `billingEnabled=false` as no open billable account; Firebase documents downgrade to Spark when its billing account is unlinked or closed. These establish a technical inference after active Firebase project identity is verified, rather than asking the operator to define Spark semantics. The diagnostic probe reports only the billing flag. The native collector combines that flag with an ACTIVE Firebase project and the explicit free-tier database identity; the resulting Spark fragment is not an all-provider plan report. |
| Firestore outbound | The inspected Firestore metric reference does not list a monthly outbound-byte counter. That alone is not proof of provider-unobservable status for this project. Check current Firebase Usage, Monitoring and Quotas using approved read access; only their combined, current configuration finding can support the existing exception. Errors/missing series never qualify. |
| GitHub storage/shared allowance/cache | Use documented billing Usage/Usage Summary and separate cache usage/configuration endpoints; resolve actual SKU/unit/period and complete coverage privately. Net zero does not prove that discounts came from included allowance. The published Budget REST API inspected is organization-scoped, not a verified personal-account spending-control endpoint. No supported personal allowance/control source has been verified yet; this is an unresolved technical/access limitation, not a request for the user to invent one. |
| Official limits | `verify_limits.py` now checks reviewed current public table columns and affirmative clauses, recording source, retrieval time and content digest only after all sources pass. Live HTML compatibility remains unverified because direct source fetches failed in this environment. Only successful semantic checks may renew `limits_checked_at`; HTTP success or a digest alone is insufficient. Unexpected clauses/values fail closed for review. Parser/source selection is delegated engineering work, not an outstanding user decision. Release-time review and the existing 31-day bound remain. |

Sources inspected:
[Cloudflare OpenAPI](https://github.com/cloudflare/api-schemas/blob/main/openapi.json),
[Workers metrics](https://developers.cloudflare.com/workers/observability/metrics-and-analytics/),
[Cloud Billing project state](https://docs.cloud.google.com/billing/docs/reference/rest/v1/ProjectBillingInfo),
[project billing permission](https://docs.cloud.google.com/billing/docs/reference/rest/v1/projects/getBillingInfo),
[Firebase plan transitions](https://firebase.google.com/docs/projects/billing/firebase-pricing-plans),
[Firestore metrics](https://docs.cloud.google.com/monitoring/api/metrics_gcp_d_h#firestore),
[Firestore usage limitations](https://firebase.google.com/docs/firestore/monitor-usage),
[Monitoring permissions](https://docs.cloud.google.com/monitoring/access-control),
[GitHub usage](https://docs.github.com/en/rest/billing/usage),
[GitHub budgets](https://docs.github.com/en/rest/billing/budgets), and
[GitHub cache configuration/usage](https://docs.github.com/en/rest/actions/cache).
The observed GitHub UI `$0.02` remains **not a confirmed charge** until the actual
product/SKU/gross/discount/net and shared-allowance evidence is resolved.

### Prepared validation boundary

`quota(..., publication=True, collected=True)` validates all nine schema-v2 metric
structures, scopes, periods, freshness and billing constraints before returning the
existing `free-plans-only` publication result. It ignores numeric usage warning
thresholds, so valid TAF/Sync exhaustion does not interrupt MSM publication. Unknown,
incomplete, stale or malformed collection and nonzero billing still fail. This flag is
an internal validator mode, **not proof that collection occurred**. The legacy CLI and
existing plan-only migration interface are unchanged until a complete native collector
can supply trustworthy evidence. No workflow integration is claimed by this preparatory
change.

When those sources are verified, the collector must build a new report entirely from
current responses, call this boundary, and expose only a reviewed non-secret audit
summary. Both monitor and repeated pre-upload publication checks must invoke it directly,
with read credentials scoped to those steps (never build/job-wide or `VITE_*`). A
collection failure must stop the gate without consulting `STATIC_QUOTA_REPORT` or cached
reports. Until then the remaining work is blocked, not satisfied by periodic manual data
entry. Keep recurring production disabled. Live failure-notification delivery and
same-origin rollback/Local retention have already been accepted in #123 and need not
be repeated for this implementation.

### Native collection implemented so far

`python scripts/static_ops/collect_providers.py` now performs actual read-only collection
for the verified mappings below, using the same scoped environment variables and bounded
HTTP client as the probe. It always exits nonzero while the other required evidence is
unsupported. Its output is a fixed-status diagnostic summary, **not** a schema-v2 report.
No fragments, billing rows, project metadata or tokens are exported. It neither reads
`STATIC_QUOTA_REPORT` nor persists partial results for later reuse.

- Firebase identity: GET `firebase.googleapis.com/v1beta1/projects/{project}` requires
  an ACTIVE project and matching project ID/number; project billing must explicitly be
  disabled. Database listing must be complete, with one native database explicitly
  marked `freeTier=true`. An assumed default database, missing flags, unreachable
  locations, extra databases, or mismatched identity do not pass. These reads support
  the documented Spark inference, not a claim about every provider's billing controls.
- Firestore reads/writes/deletes: paginate the current Pacific day's native DELTA
  series, bind project/database/location, validate kind/type/unit and documented
  operation labels, then sum intervals from midnight **through native `observed_at`**.
  Gaps, overlaps, duplicates, absent operation classes, partial errors and no data fail;
  none are interpreted as zero. Explicit zero-valued points are valid. The current
  36-hour freshness and current-period rules remain; fetching never relabels old points
  with a fresh timestamp. Sampling and visibility delays do not create synthetic data.
- Firestore storage: select the latest native GAUGE value, including data and indexes,
  for the verified database. Do not sum observations over time. Preserve the native
  timestamp and require the same 36-hour bound. A missing gauge is not an outbound
  exception.
- GitHub repository cache: read current active cache bytes, require the native `full_name`
  to match the configured repository (GitHub names are case-insensitive), then read the
  configured storage limit. Validate native integers and the existing 10 GiB free ceiling. This fragment deliberately
  lacks monthly billing evidence. Schema-v2 validation rejects it until separate current
  cache billing evidence is implemented; instantaneous usage cannot establish accrued
  charges or the Actions/Packages shared allowance.

The Firestore quota numbers are reviewed constants, **not** a fresh limits review.
Neither this collector nor its tests renew `limits_checked_at`. The separate `verify_limits.py` implements official-limit semantic checks, but its
live HTML acceptance and integration remain unverified; a successful fetch, digest, fixture,
account query or test run must never renew that timestamp by itself. The four Firestore
fragments have offline integration coverage against the existing schema-v2 validator;
fixtures do not prove API availability or explicit zero-series coverage on this account.

The full #123 implementation is still incomplete: Cloudflare plan/account-total adapters,
Firestore outbound's current three-surface finding, GitHub accrued SKU/shared allowance
and billing controls, live limit-verifier acceptance, complete report assembly, audit summary
and workflow replacement have not been completed. `static-monitor` and
`static-production` remain unchanged; their current manual-report implementation must
not be described as unattended acceptance. Keep recurring publication disabled.

The smallest next **read-only access verification**, distinct from permission to create
credentials, is to run the probe and native collector using existing approved credentials
for only the target scopes. Their redacted status output is safe to return; never copy
raw responses or tokens into chat. If an adapter is blocked, inspect the native response
privately in that approved environment to resolve:

| Provider | Minimum next native facts |
| --- | --- |
| Cloudflare | Subscription result shape and actual permission support; complete account project/deployment coverage; request-series shape and whether an explicit zero/quota counter is provided when invocation analytics has no data. |
| Firebase/Firestore | ACTIVE project and free database metadata; explicit billing-disabled flag; current read/write/delete/storage series including point intervals and operation classes. Inspect current Usage/Monitoring/Quotas privately for outbound availability; no data or API denial cannot substitute for that finding. |
| GitHub | Current owner billing product/SKU/unit/quantity/gross/discount/net semantics, shared Actions/Packages included-allowance attribution, personal billing-prevention control, and distinct current-month cache charges. Repository cache API success alone is insufficient. |

If no supported zero-cost API can provide a required fact, document that concrete
provider limitation and bring the resulting requirements conflict to the operator.
Do not ask the operator to design an adapter or silently weaken the evidence contract.
No credential/IAM/OIDC/secret changes are included in this work.

Additional mapping references:
[Firebase project resource](https://firebase.google.com/docs/reference/firebase-management/rest/v1beta1/projects),
[Firestore database freeTier](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/projects.databases),
[Monitoring database resource labels](https://docs.cloud.google.com/monitoring/api/resources#tag_firestore.googleapis.com/Database),
[Firestore free quota](https://firebase.google.com/docs/firestore/quotas).

### Exact next read-only checks (2026-10-04)

The latest connector read still reports remote `main=56d62a5` and #123 comment
`5972103619` as the governing unattended-collection decision. The saved local work is
based on that SHA. GitHub repository metadata reads through the existing connector
succeeded and confirmed `Yuto-24/AutoNavLog` is public; they did **not** establish billing
access. The connector explicitly excludes user/billing endpoint families and does not
expose cache/variable reads. Environment `gh api` failed even for repository metadata
with `Forbidden` at the request boundary. This is an access-path failure, not evidence
of a provider 403 response or a diagnosis of the token's actual grants. Do not bypass
that boundary or retry with broader credentials. The required Cloudflare and Google
read-token environment variables were not set. No credentials were printed or installed.

The production targets below come from the checked-in
[#121 account audit](evidence/issue-121/remote-production-2026-09-24.md), not a new
account query. Before collection acceptance, confirm these still match the existing
repository variables; a mismatch must stop collection rather than select another scope.

| Provider / exact target | Smallest next read | Existing permission needed | What success establishes |
| --- | --- | --- | --- |
| Cloudflare account `cae366c3bb569163773b80f4bdff2d8e`, Pages `navmate` | GET `/client/v4/accounts/cae366c3bb569163773b80f4bdff2d8e/pages/projects` on `api.cloudflare.com` | Account-scoped **Cloudflare Pages Read** | API access and native project-list shape; not complete pagination, Free status or monthly builds |
| Firebase/Google project `navmate-prod` | GET `/v1/projects/navmate-prod/billingInfo` on `cloudbilling.googleapis.com` | Project `resourcemanager.projects.get`; existing OAuth scope `cloud-billing.readonly` or an already authorized compatible scope | Explicit current project billing flag; not Spark identity by itself |
| GitHub repository `Yuto-24/AutoNavLog` | GET `/repos/Yuto-24/AutoNavLog/actions/cache/usage` on `api.github.com` | Target repository **Actions: read** | Native current cache bytes, not accrued billing |
| GitHub personal owner `Yuto-24` (a distinct subsequent read) | GET `/users/Yuto-24/settings/billing/usage?year=2026&month=10` on `api.github.com` | Personal-user **Plan: read**, subject to endpoint eligibility | Native current-month product/SKU/unit/gross/discount/net rows kept privately in memory; not included-allowance attribution or billing prevention |

Run only the corresponding fixed probe after an **existing approved** credential is
available through the secure execution mechanism. The selector performs no other
provider or owner-billing request. Token values are never command arguments:

```sh
CLOUDFLARE_ACCOUNT_ID=cae366c3bb569163773b80f4bdff2d8e \
  python scripts/static_ops/probe_providers.py --check cloudflare_pages_projects
VITE_FIREBASE_PROJECT_ID=navmate-prod \
  python scripts/static_ops/probe_providers.py --check firebase_billing
GITHUB_REPOSITORY_OWNER=Yuto-24 GITHUB_REPOSITORY=Yuto-24/AutoNavLog \
  python scripts/static_ops/probe_providers.py --check github_cache_usage
GITHUB_REPOSITORY_OWNER=Yuto-24 \
  python scripts/static_ops/probe_providers.py --check github_billing_usage
```

All probes intentionally exit 1: reachability is never publication authorization.
Return only their fixed statuses. Inspect any native billing rows privately in the
approved execution environment; never paste them into chat, a ticket or an artifact.
The billing probe derives its year/month from the actual UTC clock; the October endpoint
above is the exact next request for this dated checklist, not a hard-coded future period.

After these reads succeed, engineering continues with existing collectors and the
remaining native response mappings: Cloudflare subscriptions (`#billing:read` capability,
actual token eligibility still unverified), Analytics Read and complete Pages history;
Google `firebase.projects.get`, `datastore.databases.list`,
`monitoring.timeSeries.list` and `monitoring.metricDescriptors.list` on `navmate-prod`;
GitHub cache configuration and current owner/shared billing evidence. The historical
Cloudflare `workers/settings.free_tier` response is not an approved automated mapping:
the inspected public schema documents `workers/account-settings` with different fields.
Do not promote an undocumented historical endpoint to production plan evidence.

**Authorization boundary:** existing connected repository reads above are already within
this task. Using an existing approved read session is distinct from creating or changing
credentials. Any new token, service account, IAM binding, OIDC trust, secret storage or
upload needs an exact target/grant/change proposal and explicit approval at that action.
No such changes, deploy, schedule activation, runtime retirement, push or PR publication
have been performed. The access failure does not authorize any of them.

Permission references:
[Cloudflare Pages list](https://developers.cloudflare.com/api/resources/pages/subresources/projects/methods/list/),
[Google project billing read](https://docs.cloud.google.com/billing/docs/reference/rest/v1/projects/getBillingInfo),
[GitHub cache read](https://docs.github.com/en/rest/actions/cache#get-github-actions-cache-usage-for-a-repository),
[GitHub personal billing read](https://docs.github.com/en/rest/billing/usage#get-billing-usage-report-for-a-user).

### Public official-limit verification

`python scripts/static_ops/verify_limits.py` needs **no account credentials**. It fetches
only four fixed HTTPS public documentation URLs, rejects redirects, limits response size
and elapsed time, and checks the reviewed free-plan table columns, units, reset rules,
single-free-database clause and GitHub shared/separate allowance semantics. Assertions
are bound to visible main-content sections and affirmative paragraph/list blocks;
hidden/deleted/template content and historical or negated replacements cannot renew the
timestamp. Changed or unrecognized markup fails closed for parser/source review.

Only success across all four sources emits `limits_checked_at`, eight numeric limits,
and source URLs/digests. It does not fabricate a static Actions accrued GB-hours limit
or prove a particular account is within the shared pool. No input/cache/old-report path
exists. Successful fixtures, HTTP status alone and a hash alone cannot renew anything.
The current run returned `transport_failed` for all four direct source reads and emitted
**no timestamp**. Separate documentation research verified the reviewed clauses,
but is not substituted for this process's failed fetch. Real HTML end-to-end acceptance
and full-report integration remain pending. The existing monthly/release review contract
and 31-day validation bound are unchanged.

Cloudflare source research also identified
[`/accounts/{account_id}/billable-usage/info` (v1 Alpha)](https://developers.cloudflare.com/api/resources/billing/subresources/usage/methods/paygo_info/)
and [`/accounts/{account_id}/billable/usage` (v2 Alpha, Restricted)](https://developers.cloudflare.com/api/resources/billing/subresources/usage/methods/get/).
The latter documents daily metered records including free-tier usage, but says cost and
pricing fields are not yet populated. These are candidates for private capability
investigation after existing read access is established, not implemented quota mappings.
Actual account eligibility, token support, metric identifiers, zero-record semantics and
complete current-day/month coverage remain unverified. Do not enable a paid product or
assume a missing cost field means zero to obtain or use this source.
