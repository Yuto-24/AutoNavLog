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
- The hourly schedule is disabled unless `STATIC_AUTOMATION_ENABLED=true`.
  That variable is explicit authorization for recurring feed publication, not app upgrades.
  This MSM-only workflow rejects `source_sha` unless it equals `STATIC_APPROVED_SHA`.
  App upgrades use the separately approved #121 publication procedure.
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
   The monitor calls the native collection gate directly. Missing adapters and
   unattended Google authentication block healthy usage monitoring, independently
   of static-only build/publication. Missing usage remains **UNKNOWN**; it is never
   zero, healthy, or proof of account-wide zero billing. Do not periodically replace
   `STATIC_QUOTA_REPORT`. Failure-notification receipt was already accepted on 2026-10-03; the
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

At minute 23 every hour, the workflow reads canonical `release.json` and rejects
any mismatch with `STATIC_APPROVED_SHA`. Scheduled runs then read the canonical MSM
catalog and fresh upstream listings before restoring NPZ files, downloading GRIB,
decoding, building the candidate sandbox, or running Browser E2E. A complete newer
Run covering the producer's 25-hour horizon enables the existing publication path.
With no newer Run, a valid catalog with at least two hours remaining and matching
complete upstream discovery, plus current 24-hour coverage in its own retained
listings, produces `NO_NEW_RUN` and skips heavy work and upload.
The preflight never writes a catalog or renews forecast timestamps.

Failed reads, blank successful responses, malformed listings/catalogs, incomplete
newer Runs without a complete newer candidate, and regressed discovery fail closed.
An upstream directory HTTP 404 is known absence; other failed reads cannot become
absence. With no new Run, expired or less-than-two-hours-remaining catalogs fail.
An expired catalog with a complete newer Run may recover through all existing gates;
the summary explicitly records its expiry. Preflight retains dependency installation
and source checks, so a healthy no-op still incurs runner startup work.

Manual dispatch, including `publish=false`, retains the existing build/test path and
newer-Run requirement; preflight no-op semantics apply only to scheduled events.
All runs retain the shared `static-production` concurrency group with cancellation
disabled. Queued scheduled runs reread canonical metadata after acquiring the group;
the later restore/refresh check also rejects a Run already published after preflight.
Merging this cadence change affects an already-enabled schedule immediately; it does
not change the authorization variable or create a separate scheduler.

For a newer Run, the workflow restores recent immutable NPZ files from
production, verifies byte lengths/hashes, and seeds the existing producer. Seven-day
Run retention survives runner replacement and cache eviction. No Project data is sent.
A failed read/hash check/producer/build/test stops before upload. A retained-only
producer result with no newly prepared Run newer than the retained latest Run is rejected; renewing only the timestamp
is not a successful refresh. The previous deployment
is retained, and expired weather still fails closed in the browser.

The producer's six-hour catalog lifetime and each validator's 24-hour coverage check
remain unchanged. Hourly preflight offers another opportunity after delay/failure;
the independent hourly monitor warns with fewer than two hours remaining. GitHub cron
is best-effort and can be delayed/dropped; public inactive repositories can have schedules
disabled. Check Actions schedule status and notification delivery during the daily audit.
Neither this workflow nor GitHub cron promises weather availability during outages.

The workflow rebuilds the pinned source/configuration automatically, so the operator no
longer manually rebuilds/redeploys merely because the MSM catalog expires. Same-source
feed publication rejects configuration drift. All app/feed jobs share a non-cancelling
concurrency group. Static identity/source/artifact checks repeat immediately before upload.
Atomic Pages upload switches payload and catalog together. Post-upload checks compare
canonical **whole release inventory**, not just the version, and check freshness.
The post-upload verifier immediately checks canonical `release.json` and reads the actual
`weather/msm/catalog.json`. Its exact SHA-256 and byte length must match the candidate
inventory, and the same response must pass the existing MSM freshness monitor checks
(nonempty assets, at least two hours remaining, no future generation timestamp).
Publication freshness is evaluated at catalog receipt, including body-read time.
Failed checks retry after 10 seconds for up to 300 seconds (monotonic deadline).
Each check runs `check_candidate.py --remote` in a child process with at most 30 seconds,
clipped to the remaining overall budget; a hung or slowly streaming response cannot
extend the polling window. Inventory mismatch, HTTP/network errors, invalid JSON and
other checker failures are retried within that budget. Redirects remain rejected on
every attempt and are never followed; only exact inventory equality passes. Reads
retain the existing `Cache-Control: no-cache` header, origin and byte budget.
Deadline expiry fails closed, including a match returned at/after the deadline. Attempt
diagnostics go to the job log; only successful identity verification enters the summary.
Catalog results include receipt/check timestamps, observed hash/size, generation/expiry,
seconds remaining and, during candidate verification, expected hash/size and whether they
match. Mismatch/freshness errors keep these diagnostics on one line in the attempt log.
The independent MSM monitor still makes a separate request **after** identity and catalog
verification succeeds, never on timeout; its diagnostics describe that separate response.
Matching release metadata alone does not establish which catalog that request received.
These retries do not re-upload, change the approved SHA/configuration gates, or authorize
another deployment. Failure after upload needs investigation; it does not automatically
roll production back.

Investigation of [run 38575361551](https://github.com/Yuto-24/AutoNavLog/actions/runs/38575361551)
on 2026-10-14 found upload completion at 13:57:33 UTC, inventory match on the first attempt,
and a freshness failure at 13:57:34 UTC. The staged candidate expiry was 19:52:23.600805 UTC
after generation-age/six-hour-lifetime validation succeeded. Its expiry was therefore
about 5h55m away. The failed monitor response was not retained, so an old catalog read
is a hypothesis, not a confirmed incident cause. Regression tests establish the prior
verification gap: matching release metadata could pass with an old or different actual
catalog. The additional response checks and diagnostics address that gap without changing
publication timing, expiry policy, or authorizing recovery publication.

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

`static-monitor.yml` calls `collection_gate.py` on each enabled run, even if weather
failed. It collects current native fragments and checks official limits without an
operator-report fallback. Incomplete collection remains BLOCKED/UNKNOWN and does not
establish healthy monitoring, optional-service availability, or account-wide zero billing.

`static-production.yml` instead calls the independent `publication_gate.py`.
Google authentication and Workers/Firestore/shared-allowance missing counters do not
block a safe build-only run or unrelated static MSM renewal. The workflow records
`optional_usage: UNKNOWN` and `account_billing: UNKNOWN`; it does not disable TAF/Sync.
The strict schema-v2 monitor and its provider-period/freshness semantics remain intact.
The collector's diagnostic `--publication` option is no longer a live upload gate.

The static gate requires the public AutoNavLog repository, standard `ubuntu-latest`
GitHub-hosted runner metadata, exact canonical origin, full approved SHA and unchanged
six public connection settings. The producer and all validators come from the workflow
checkout. Every payload is decoded with the pinned real-MSM library, matched by Run/hash,
and new Runs must cover the current hour through the next 24 hours with all three variables.
The publication refresh requests 25 hours from the producer's current-hour start:
the whole job's 45-minute timeout permits at most one hour-boundary crossing before
the final check. The pinned MSM library includes the upper three-hour pressure
bracket automatically (for example, a 13:00 endpoint needs 15:00 pressure fields).
Do not add a separate three-hour horizon margin. Every validation still uses its
own current clock and requires 24 hours; the 30-minute generation freshness check
is unchanged. Revisit the generation margin if the job timeout exceeds one hour.
Catalog lifetime stays six hours; publication requires at least two hours remaining.

Install producer dependencies from `./tooling[test]` in the runner workspace root.
The bare `tooling[test]` names an unrelated PyPI package, not the checkout. CI exercises
the workflow's actual pip command in a clean virtual environment alongside the fixed
older app checkout, verifies the installed package's local origin/version and imports
the producer/validator with `--help`. This regression does not download MSM or publish.

Candidate pip/npm/build/production Browser E2E run in a dedicated Docker container with
only the application checkout (RW) and feed (RO) mounted. The container receives public
VITE settings, never provider tokens, runner output/metadata files, the tooling checkout
or Docker socket. Candidate code cannot replace host validators or the upload CLI.
The host checks bytes/hashes, all inventory entries, Local package hashes/version,
Functions/Worker/private files, private key material and Pages file/size limits.
Candidate root/web Functions and unapproved Wrangler configs/redirects are rejected.

Immediately before upload a new runner-temporary directory contains only checked dist
and a trusted minimal Wrangler JSON config. Wrangler runs there with explicit config,
project `navmate`, production branch `main`, and independent locked CLI. An authenticated
GET using the existing Pages credential requires the existing project, canonical domain,
production branch and absence of the prohibited production runtime bindings. Existing
Pages `env_vars` are allowed and left untouched: the isolated build uses the approved
GitHub public configuration, and static Direct Upload does not inject Pages variables
into prebuilt assets. The sealed upload context still forbids Functions/Worker code
and additional Wrangler configuration. Missing/incorrect target
information blocks upload; no project is implicitly created. This target read is not usage
or plan/billing evidence. Direct Upload is atomic; bounded canonical inventory polling and
freshness checks remain after upload. No Actions cache/artifact/Packages persistence is added.

### Schema v2

Start from [the deliberately incomplete example](static_quota.example.json). The Python
validator in `scripts/static_ops/operations.py` is the executable schema; it requires
exact metric names and variant fields. The example contains null readings and placeholder
evidence, and **must fail** until actual current observations replace them. Its free limits
are a 2026-10-01 documentation snapshot; recheck provider specifications at each release.

Report fields are `schema_version: 2`, `plans`, `plans_observed_at`, `limits_checked_at`,
`scopes`, and `metrics`. Scopes must match `CLOUDFLARE_ACCOUNT_ID`,
`VITE_FIREBASE_PROJECT_ID`, `GITHUB_REPOSITORY_OWNER` and `GITHUB_REPOSITORY`
(`owner/repository`). The diagnostic legacy publication validator also accepts the previous three-scope plan-only report;
this interface is not used by the static upload workflow.

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
# Diagnostic legacy validator only; not a live publication gate:
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
- After approval, publish app upgrades through the separate #121 procedure. Complete real-origin
  acceptance; update `STATIC_APPROVED_SHA` only after success, then resume the pinned MSM policy.
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

## WSL からのアプリ更新: prepare / publish

`scripts/static_ops/app_release.sh` は、既存 Python gate を呼ぶ薄い入口。
**prepare は provider を変更しない。publish は別途承認後だけ実行する。**
GitHub の release 作成、main の最新版、MSM-only dispatch はアプリ更新の承認を代用しない。
PR #259 の毎時 MSM 判定と共通 workflow は変更せず、既存の
`STATIC_AUTOMATION_ENABLED` 停止 → idle 確認 → 公開 → `STATIC_APPROVED_SHA` 切替 → 再開を使う。

実行場所は、既存認証のある **WSL/Linux の clean な tooling checkout**。
Python 3.12 の既存 `.[test]` と vendored MSM library、Node 24、Docker、Git、`gh` が必要。
認証は既存 `gh` session と、承認済みの `CLOUDFLARE_API_TOKEN`（対象 Pages の既存編集権限）、
`STATIC_GOOGLE_ACCESS_TOKEN`（対象 Firebase Rules の既存読み取り権限）を安全な環境経由で渡す。
値をコマンド、ログ、PR に貼らない。スクリプトは login、token 作成、権限追加をしない。
読み取りが拒否された場合も新規認証で迂回せず停止する。候補の build は Docker 内で実行し、
公開 VITE 設定と app/feed 以外に provider token、tooling、Docker socket を渡さない。

```sh
# WSL: review/merge 済み tooling commit の clean checkout で実行。
# 既存 Python venv を activate 済み。保存先の親だけ事前に用意する。
mkdir -p "$HOME/autonavlog-releases"
release_store="$HOME/autonavlog-releases/v1.22.7-$(date -u +%Y%m%dT%H%M%SZ)"
scripts/static_ops/app_release.sh prepare \
  --sha e0ad634a8fab2ed48310f48b08c4ec3961cad14b \
  --current-sha a4de2a226dfa205b8be42fb62a710a1b06271ed8 \
  --rules-transition-v1227 \
  --store "$release_store"
# plan.json / prepare-status.json / prepared/upload と検証結果を private に確認。
sha256sum "$release_store/plan.json"
```

上記は **候補 v1.22.7** と **現在 v1.20.1** の明示例で、公開承認ではない。
この切替では先に別途承認された定期停止・Rules反映と読み取り照合が必要。
下の「v1.22.7 の Rules / Pages 切替」に従い、automation=false、workflow=active、
稼働中Rulesが固定候補に一致した後だけ、このprepareを実行する。
v1.22.8 の merge 後 WebKit 失敗を理由に候補を最新版へ自動置換しない。
SHA は40桁を必須とし、main に含まれる exact commit / VERSION を確認する。
Firebase 4設定、TAF URL、Legacy migration URL、account、origin と repository variable を
canonical release に照合し、environment variable による shadowing も拒否する。
Migration URL は維持する。受付終了の検討（10/24〜25目安）は別の承認・変更として扱う。

prepare は現在の成功した Pages production ID/commit、完全な deployment inventory と idle、
Direct Upload の対象、Functions/設定混入、稼働中の Firestore Rules を読み取りで確認する。
通常は候補/current の Rules、indexes、Project schema の同一を必須とする。
`--rules-transition-v1227` は下記の固定SHA・project・元バイトRules hashだけを例外として
許可する。indexes / Project schema の同一条件は維持する。他の移行を許可する入力はない。
稼働中 Rules は [release GET](https://firebase.google.com/docs/reference/rules/rest/v1/projects.releases/get)
と [ruleset GET](https://firebase.google.com/docs/reference/rules/rest/v1/projects.rulesets/get) で確認し、
候補に一致しない場合は自動 deploy しない。これは実 Google login / Sync の受入を代用しない。
現在の plan/limits/共有枠確認と独立レビュー・候補 CI の確認は上の checklist と #121 を継続する。

producer は現行 feed の保持 asset を復元し、25時間分を実取得・decode して新たに生成する。
アプリ更新では同じ Run の実再取得を許すが、retained-only の timestamp 更新を認めない。
既存 `validate_feed` の30分 freshness、24時間 coverage、2時間残存、Run/hash 検証を再利用する。
候補の release/data/schema 検査、typecheck、static artifact tests、production Browser E2E と、
tooling による独立 inventory/private-material gate が完了してから plan を保存する。
保存先は checkout 外の新規0700 directory。plan と status は0600。
保存内容は公開 Firebase 識別子を含むので private のまま保管する。

**本番への書き込み、定期停止/再開、承認 SHA 切替を明示承認した後だけ**:

```sh
# 同じ WSL/tooling commit。レビューで確認した64桁ハッシュを文字列で指定する。
# この値を publish 時に再計算して自動採用しない。
scripts/static_ops/app_release.sh publish \
  --sha e0ad634a8fab2ed48310f48b08c4ec3961cad14b \
  --current-sha a4de2a226dfa205b8be42fb62a710a1b06271ed8 \
  --rules-transition-v1227 \
  --store "$release_store" \
  --reviewed-plan-sha256 REVIEWED_64_HEX_DIGEST
```

publish は30分以内の plan、reviewed hash、prepare の inventory/report、SHA/設定/Rules/tooling
を再照合する。古い prepare は新しい保存先で prepare し直してレビューする。
その後 `STATIC_AUTOMATION_ENABLED=false` を確認し、同じ `static-production.yml` を一時 disable
して新規 schedule/dispatch を止める。既に開始済み・queued・waiting の run は cancel せず、
状態別検索に分割しない全 run inventory で idle を確認できなければ、停止状態を保持して終了する。
ローカル呼出しは同じ git common directory の flock で直列化する。
これは remote lock ではないため、承認した公開作業中は別 WSL/host、Dashboard、手動 upload、
workflow enable/dispatch を行わない。Pages の GET inventory は atomic lock ではなく、
最後の照合直後に別 operator が公開する競合まで保証しない。

publish は **fresh/** に再生成・再テストし、prepare の dist を upload しない。
共有 workflow の idle、Pages ID/設定、SHA、Rules と fresh inventory/coverage を再確認する。
固定 lockfile の trusted Wrangler を最小 upload directory（dist-static と wrangler.json のみ）
から使い、`pages deploy dist-static --project-name navmate --branch main --commit-hash SHA` を実行。
Pages の未サポート `--dry-run` / custom `--config` は使わない。TAF Worker deploy は呼ばない。
正規 URL の release 全 inventory（既存 bounded poll）、freshness、HTML/manifest の内容・
HTTPS/no redirect/no-cache/ETag/MIME、欠落 asset の404、Pages commit を確認する。
成功後だけ承認 SHA を更新・read-backし、workflow を enable する。
開始時に automation が true だった場合だけ true に戻し、元が false なら false を維持する。
#121 の実 login/Sync、旧タブ、保存データ、plan/billing 等の production acceptance は別途必要。

両段階の `*-status.json` は stage、操作履歴、変更済み/未変更/UNKNOWN を原子的に保存する。
snapshot / plan / status の `rules_receipt` は外部反映済みRulesのrelease全体・source hash・
固定移行を記録する。Rules反映はこのscriptのmutationではない。下記の外部操作記録も保管する。
`UNKNOWN` は timeout/interruption により server 側が変更済みの可能性がある。
SIGKILL 等では RUNNING/ATTEMPTED が残るため、成功扱いせず remote state を確認する。
失敗後の finally で再開したり、自動 rollback したりしない。一度試行した publish の保存先は
再利用しない。失敗段階ごとの必要操作:

| 失敗した段階 | 確認・復旧操作（別途承認） |
| --- | --- |
| pause 前 | scriptによるPages/変数/workflow変更なし。外部Rulesは反映済みの場合があるためRules操作記録を確認。原因を直し新規 prepare。 |
| pause/disable/idle/build/pre-upload | 停止操作の read-back と既存 run の完了を確認。本番と承認 SHA を照合してから再開可否を判断。 |
| upload または canonical 検証 | 配置が変わった可能性を調査。新旧 inventory/ID と weather を確認し、停止を維持。自動 rollback なし。 |
| 承認 SHA 更新 | 新 app が配信済みでも旧承認 SHA の可能性あり。canonical と read-back を照合してから切替・再開。 |
| workflow/automation 再開 | 新 app/承認 SHA が成功していても定期更新状態は不明。workflow と variable を読んで確認し、必要な操作だけ行う。 |

今回の実装検証では publish、repository variable 更新、workflow disable/enable を実サービスへ
実行しない。`tests/unit/test_static_app_release.py` は隔離 inventory と provider mock で正常系、
設定変化、競合、改変/古い成果物、各書き込み前後の失敗、SHA更新・再開失敗を検証する。

## v1.22.7 の Rules / Pages 切替

この手順は **実施する対象・回数・停止/再開を別途承認した後だけ** 使用する。
本PRの実装・mockテスト・レビュー・mergeは本番操作の承認を代用しない。
固定候補のRulesそのもの、Auth/IAM/indexes/TAF/DNS、旧サーバーと移行URLは変更しない。
`--rules-transition-v1227` は旧Rulesを自動反映する機能でも、Rules検査を省く機能でもない。

| 固定項目 | 値 |
| --- | --- |
| 現行アプリ v1.20.1 | `a4de2a226dfa205b8be42fb62a710a1b06271ed8` |
| 候補アプリ v1.22.7 | `e0ad634a8fab2ed48310f48b08c4ec3961cad14b` |
| Firebase project / release | `navmate-prod` / `cloud.firestore` |
| 変更前Rules SHA-256（UTF-8元バイト） | `f27ee41870413f48b9ac0da609350cac3b3cc3d2d3a4fa0d30772b3312865246` |
| 変更後Rules SHA-256（UTF-8元バイト） | `574725b83fc0d0bca5df7311b970f97aeaaae068524eb1343bdc3912973757d8` |
| 事前確認済み旧ruleset | `projects/navmate-prod/rulesets/688d5522-783a-452c-bbaf-7981748d3ef0` |
| Pages / origin | `navmate` / `https://navmate.yuto24.com` |
| 維持する移行URL | `https://navlog.yuto24.com` |

任意のSHA・project・hash・移行manifestを受け取る一般基盤は追加しない。
例外はこの旧→新の一方向だけ。逆方向、未知の差分、他project、Rulesの空白/改行を含む
hash違い、indexes/Project schema差分を拒否する。通常の同一Rules更新は既存ルートを使う。

### 再利用する検証と残る保証

2026-10-14の固定SHA検証・GPT-6 Astra独立レビューを引き継ぐ。旧/新アプリ＋新Rulesの
通常保存/同期、別所有者拒否、cleanup中断/retry、offline端末再接続、明示再登録後の世代分離、
旧Legacy adapterによる200 ProjectとLast Calculationの引継ぎを合成データで確認した。
検証は既存Rules/auth/sync unit 46、切替/復旧の観測11、Chromium SDK 3、旧版タブ1、
Legacy Python 25（計86）とLegacy移行5状態。実Google認証・実機・本番受入とは区別する。
旧版タブは削除後もLocalコピーを表示し続ける。Remote writeは拒否されるが、全端末の
即時Local消去は保証しない。DELETING中はcleanup read/deleteを許可する。

同一所有者が手作りrequestで過去accountIdまたは別v1形式IDから旧保存先を再開できる限界は
[後続Issue #262](https://github.com/Yuto-24/AutoNavLog/issues/262) へ分離した。
通常の削除・再登録は成功しており、他所有者のデータ取得や普通の旧タブによる自動復活を
示す再現ではない。本人の任意requestに対する全履歴非再利用保証は残件であり、ユーザー合意に
より今回の公開条件にはしない。候補Rulesを黙って修正しない。

### 1. 排他・停止・変更前の読取り

cleanなレビュー済みtooling checkout、既存認可、固定旧/新SHAを用意する。最新版mainの
アプリへ候補を置換しない。public config、live app/Pages ID/full inventory、Legacy版と
移行URL、repo/env variables、workflow、現Rules releaseを実施直前に読み直す。
読取りがUNKNOWNまたは想定外なら停止する。新しいlogin/scope/IAM変更で補わない。

別operator/Dashboard/manual dispatch/uploadを停止窓から除外し、別途承認された
`STATIC_AUTOMATION_ENABLED=false` を1回変更・read-backする。`STATIC_APPROVED_SHA` は旧SHAを
維持する。queued/running/waiting runとPages deployがidleであることを完全inventoryで確認する。
workflowはactiveを維持する（prepareが要求する）。後のpublishがdisableを行う。

RulesはFirebase Rules APIのrelease GET→指定ruleset GET→release GETを行い、同じproject、
前後release完全一致、1ファイル `firebase/firestore.rules` の元バイトhashが変更前値であることを
確認する。旧ruleset IDだけでrollback安全とは扱わない。既に候補Rulesが適用済みなら再deployせず、
既存の反映記録と正確な変更後hashを照合して次段階へ進む。未知のRulesは上書きしない。

### 2. Rules反映（外部操作・別途承認）

新規privateな専用directoryへ固定候補のRulesを抽出し、hashを確認する。以下はローカル準備例:

```sh
rules_directory="$(mktemp -d /tmp/autonavlog-v1227-rules.XXXXXX)"
mkdir "$rules_directory/firebase"
git show e0ad634a8fab2ed48310f48b08c4ec3961cad14b:firebase/firestore.rules \
  > "$rules_directory/firebase/firestore.rules"
sha256sum "$rules_directory/firebase/firestore.rules"
```

本番反映は **Firebase Rules APIだけ** に限定する。汎用Firebase CLI deployは暗黙のAPI有効化や
default DB作成を行い得るため、このRules専用の承認で呼ばない。既存の対象Rules更新認可だけを
使用し、権限不足やAPI拒否は停止する。新login/scope/IAM/API有効化/DB作成は行わない。
ローカルの別Firebase project設定やweb/.env.localは流用しない。indexesは反映しない。

別途承認された外部操作は次の2段階。tokenは既存の安全な環境経由で渡し、値を引数/ログへ出さない。

1. [Ruleset create](https://firebase.google.com/docs/reference/rules/rest/v1/projects.rulesets/create):
   `POST https://firebaserules.googleapis.com/v1/projects/navmate-prod/rulesets`。
   request bodyは `source.files` を1要素とし、`name="firebase/firestore.rules"`、
   `content`は上でhash確認したファイルの元バイトをUTF-8 decodeした全文とする。
   応答の同project新ruleset名を保存し、そのrulesetをGETして正確なsource hash/filenameを確認する。
   これはruleset作成だけであり、稼働releaseはまだ変更しない。失敗/UNKNOWNで自動再POSTしない。
2. 旧releaseを再GETして事前記録と完全一致することを確認後、
   [Release patch](https://firebase.google.com/docs/reference/rules/rest/v1/projects.releases/patch):
   `PATCH https://firebaserules.googleapis.com/v1/projects/navmate-prod/releases/cloud.firestore`。
   bodyは `{"release":{"name":"projects/navmate-prod/releases/cloud.firestore",`
   `"rulesetName":"CREATED_AND_VERIFIED_RULESET_NAME"},"updateMask":"rulesetName"}`。
   placeholderを直前に作成・検証した同project名へ置換する。このPATCHが稼働Rulesの変更点。
   releaseが404や変化中なら新releaseを作らず停止する。自動retryや旧Rules復元はしない。

RulesとPagesは原子的に同時更新できないので **Rulesを先に反映** する。
旧アプリ＋新Rulesの通常保存は事前に検証済み。旧サーバーや移行受付は停止しない。

### 3. 反映後の読み取り照合とprepare

成功表示だけで完了にせず、同じrelease→ruleset source→releaseをGETする。新ruleset ID、
updateTime、変更後の正確なsource hash、前後release一致を記録する。失敗/UNKNOWNなら
Pagesへ進まず、追加deployを自動実行しない。`app_release` はRules反映を一切行わない。

workflow=active、automation=false、承認SHA=旧SHAで、上のprepare例に
`--rules-transition-v1227` を付ける。候補Rulesが未反映ならprepareは止まる。
prepareはlive Rulesに加え、旧/新git sourceの元バイトhash、indexes/schema、fresh MSM、
純静的成果物、build/E2E、独立inventory、Pages target/idleを検証する。
plan.jsonのsnapshotとprepare-status.jsonへrelease/source hash/固定移行の`rules_receipt`を保存する。
review済みplan hashを明示して承認する。30分超過・tooling/設定/Rules driftは新規prepareが必要。

### 4. Pages公開・機械検証・承認SHA更新

同一固定SHA・tooling・オプションで上のpublishを承認回数だけ実行する。scriptはworkflowを
disableし、idle確認後にfresh成果物を再生成して公開する。prepare成果物を再利用しない。
canonical inventory/headers/hash/404、MSM freshness/coverage、Pages ID/commit/config、
Rulesの前後照合を維持し、すべて成功した後だけ `STATIC_APPROVED_SHA` を候補へ更新する。
publish時にオプションを外す、先に承認SHAを変える、MSM-only workflowへ候補を流す操作は禁止。
publishはworkflowをenableへ戻すが、事前停止を必須としたのでautomation=falseを保持する。

### 5. 実受入後の定期再開

旧タブを閉じ新版を再読込みする案内を用意する。未同期Localを持つ端末へstorage消去を
指示しない。削除済み旧Localを新世代へ自動取込しない。配信するUI文言はユーザー提供または
Gemini CLIで確定する（このPRはproduct copyを変更しない）。
実Google認証、2端末保存/同期・削除retry・旧端末再接続・再登録・Legacy移行、実機Safariの
受入を別途承認された専用試験アカウントと合成Projectで実施する。現ユーザーデータで試験しない。
受入成功、canonical候補SHA、Rules source、workflow/variables/idleを再確認した後だけ、
別途承認された `STATIC_AUTOMATION_ENABLED=true` を変更・read-backする。元がfalseなら
勝手にenableしない。期限を跨ぐschedule受入は定期再開操作と別に記録する。

### 変更済み状態の記録と復旧

Rules更新前にprivateな `rules-update-status.json` へ操作時刻、project、旧release全体、
変更前/後source hash、停止前/停止後automation/workflow、Pages ID/SHAを保存する。
Ruleset POSTとRelease PATCHを別eventにし、それぞれwrite直前に `UNKNOWN` / `ATTEMPTED` を
記録する。新ruleset GET成功で作成eventだけを `CONFIRMED` とし、稼働Rulesはまだ未変更と扱う。
Release PATCH後のGETで正確な候補を確認した場合だけrelease変更を `CONFIRMED` にする。
未試行eventは `UNCHANGED`。同じ記録へPOST/PATCH各回数と作成ruleset名を保存する。
timeout、Ctrl-C、拒否応答だけで未変更とは決めない。操作回数と新ruleset ID/updateTimeを記録する。
この外部記録とapp_releaseのplan／両statusを同じ作業の証拠として保管する。

| 中断段階 | 判別と対応（必要な追加操作は別途承認） |
| --- | --- |
| Rules反映前 | 外部記録の未試行を確認。Pages/SHAも旧版のままなら停止理由を修正する。 |
| Rules反映中/後、Pages前 | Rulesが候補へ変わった可能性をGETで判別。新Rules＋旧アプリのまま停止可能。旧Rulesへ自動復元しない。 |
| Pages upload/verify | publish-statusのproductionがUNKNOWN/CONFIRMEDか、実Pages ID/releaseで判別。承認SHAは更新前の可能性がある。 |
| 承認SHA更新/再開 | journalのATTEMPTED/UNKNOWNとvariables/workflow read-backを照合し、必要な操作だけを判断。automationは実受入までfalse。 |

候補が利用者へ到達した、またはlifecycle/世代別writeの有無がUNKNOWNなら、
旧Rules/旧アプリへの一般rollbackは行わない。旧Rulesは削除済みデータの再read・旧Localの
再アップロードを許可し、旧アプリはv2データを開けずcleanup retryもできない。
新Rulesと世代管理を保持するforward fixを原則とする。候補公開前でも本人requestで親recordを
writeできるため、単にPages未公開という理由だけで旧Rules復元を安全と扱わない。
LegacyのNAVMATE_ACTIVE markerを手で戻したり、削除済みデータを旧backupから再移行しない。

公開時の承認対象は、固定候補/source、Rules反映回数、Pages公開とreview済みplan hash、
automation停止/再開、workflow disable/enable、機械検証後の承認SHA変更、専用試験データの
本番write/不可逆削除、障害時の停止/forward fixを明示する。merge・旧サーバー停止・新認証・
権限変更はこの手順に含めない。

## Acceptance still requiring production authorization

Repository verification proves build/gate logic, not remote operation. Activation must
record: environment protection and existing Pages credentials, static-only target/identity
checks, notification delivery, at least two successful scheduled renewals across the old
catalog expiry, canonical catalog/payload continuity, and controlled same-origin rollback
with scheduler paused and saved Local data retained. Update #123 and parent #116 only
with those results; do not mark the production automation gate complete from local tests.
Optional Free/Spark/shared-usage acceptance remains a separate UNKNOWN gate until observable.
It does not authorize paid processing, automatic upgrades or billing activation.

## Unattended collection investigation (#123, 2026-10-03)

The [latest #123 decision](https://github.com/Yuto-24/AutoNavLog/issues/123#issuecomment-5972103619)
supersedes periodic manual `STATIC_QUOTA_REPORT` renewal as the operating model.
**Complete unattended collection is not implemented or accepted yet.** The monitor
calls the collection gate directly, but required native mappings and Google unattended
authentication remain incomplete. Static-only renewal is now independently gated as above. They must not be activated on the strength of the probe. The schema example and migration commands remain diagnostic tools, not
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

### Initial Cloudflare credential handoff and capability run

The operator reports creating an account-scoped `AutoNavLog Quota Monitor ReadOnly`
token with Pages Read, Account Analytics Read and Billing Read, without an expiry.
This is a reported configuration, not API-verified access or quota evidence.
The exact application environment variable is `STATIC_CLOUDFLARE_READ_TOKEN`.
The operator can store its value themselves at repository **Settings > Secrets and
variables > Actions > New repository secret**, with that exact name. Use the Secrets
tab, not Variables; keep `CLOUDFLARE_PAGES_TOKEN` unchanged. Do not send the value in
chat, commit it, or include it in a workflow input. Repository secrets are not
automatically available to a local/cloud Codex checkout.

The prepared `.github/workflows/static-provider-probe.yml` is manual-only and performs
one Cloudflare capability read. It uses the repository `CLOUDFLARE_ACCOUNT_ID` variable
and supplies the read token only to the probe step. It has no environment binding,
so an environment-only secret will not be available there. Existing production jobs
do use environments; before eventual collector integration, check for environment
secrets shadowing the repository name without reading their values.

The workflow and probe were merged to `main` in #232. GitHub requires a
`workflow_dispatch` workflow to exist on the default branch. An authorized initial
run can use the Actions `static-provider-probe` page: select the reviewed branch, enter its full
40-character commit as `reviewed_sha`, and select `cloudflare_pages_projects` first.
The runner checks that SHA against its immutable `github.sha` before checkout or
the secret-bearing step; a branch advance causes a failure. Checkout is pinned to
that SHA. Merely pushing a new workflow on a feature branch does not make this initial
dispatch available. Credential storage by itself does not authorize execution. Initial Cloudflare read-only
diagnosis has since been authorized; no live result is inferred from that authorization.
If Codex cannot read the account variable or dispatch the workflow through its permitted
connection, an operator can perform these same steps in GitHub UI and share only run URLs.
Confirm the repository account variable against the intended account first; never display
or copy the secret. Do not bypass an access or network restriction.

The initial execution location is the GitHub-hosted Ubuntu runner, not the current
Codex environment. No secret retrieval or network workaround is needed. After each
authorized diagnosis, inspect only the fixed status output; further choices
are `cloudflare_subscriptions` and `cloudflare_worker_invocations`. Even reachable
endpoints produce `BLOCKED` and exit 1, so the job remains failed: this is capability
diagnosis, never healthy-monitor/Free-plan/quota acceptance. Missing data stays unknown;
raw responses, quantities and billing rows are neither printed nor uploaded. No
production schedule, deployment or existing monitor workflow is changed.

References: [GitHub repository secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets),
[manual workflow/default-branch requirement](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

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

The [2026-10-07 Workers diagnosis](https://github.com/Yuto-24/AutoNavLog/actions/runs/37619943920)
ran reviewed SHA `1b97d419566e7aa000a375092af829c9bdbff05d`, passed the identity
guard, and returned `invocations_empty` with BLOCKED / exit 1. This means exactly
one account result and an empty `workersInvocationsAdaptive` list, not zero requests.
The fixed query covered `2026-10-07T00:00:00+00:00` through
`2026-10-07T12:17:37.572613+00:00`, with only the account and time filters, `limit: 1`,
and `sum { requests }`; no script/status/dimension filter was requested. No September
or October 1–3 observations were queried. Missing/error/malformed responses and an
explicit numeric zero remain distinct; even a valid nonempty response never produces
quota evidence. Probe input times are normalized to UTC before the day boundary.

[Workers metrics](https://developers.cloudflare.com/workers/observability/metrics-and-analytics/)
exclude requests blocked by security features and distinguish cached subrequests.
The [GraphQL sampling contract](https://developers.cloudflare.com/analytics/graphql-api/sampling/)
permits estimated results for Adaptive datasets. Neither the empty result nor a
nonempty estimate proves complete quota accounting, plan, disabled billing, or a
particular cause for the empty list. The
[Workers query example](https://developers.cloudflare.com/analytics/graphql-api/tutorials/querying-workers-metrics/)
describes general history ranges, but
[account-specific node limits](https://developers.cloudflare.com/analytics/graphql-api/limits/)
must be checked before relying on a range; the probe did not establish those limits.

The read-only `cloudflare_worker_settings` check performs one fixed GraphQL query for this account's
[`settings.workersInvocationsAdaptive`](https://developers.cloudflare.com/analytics/graphql-api/features/discovery/settings/):
`enabled`, `availableFields`, `maxDuration`, `notOlderThan`, `maxPageSize`, and
`maxNumberOfFields`. Evaluate these privately for `sum_requests` and the requested
UTC-day window using the same UTC normalization as the invocation check. It emits only
the first blocking fixed outcome: `dataset_disabled`, `required_field_unavailable`,
`query_limits_not_supported`, `window_not_supported`, or `settings_invalid`.
`settings_compatible_not_quota_evidence` means all inspected capabilities support that
query, never zero usage, Free plan, billing controls, sampling accuracy or complete
quota coverage. Account/HTTP errors remain separate fixed statuses. Native limits and
field names stay private. The outer result stays BLOCKED / exit 1.

The existing manual workflow accepts this check with the same reviewed-SHA guard and
step-scoped token. This single read consolidates dataset, required field, query-size,
retention and UTC-day-window checks; it does not rerun Pages, subscriptions or invocation
analytics. Read-only diagnosis using existing credentials is authorized; new credentials,
permissions and billing changes are not. If the permitted Codex connection cannot
dispatch, use the normal authenticated GitHub environment with the reviewed branch/SHA
and `check=cloudflare_worker_settings`, then share only run URL/SHA and fixed statuses.
Do not widen credentials, upgrade a plan,
generate traffic, or repeatedly query wider history to turn no-data into zero.

`cloudflare_inventory.py` validates private supporting inventory before any future
plan/quota mapping. The collector attempts it within the existing shared 40-request /
180-second budget, after the Firebase/GitHub fragments. It uses only fixed account
endpoints. No raw response, contract row, quantity, build configuration or environment
variable is emitted in the summary. Successful checks say
`inventory_validated_not_evidence`; they do not fill any schema-v2 plan/metric or
renew `observed_at` / `limits_checked_at`.

- [Pages projects](https://developers.cloudflare.com/api/resources/pages/subresources/projects/methods/list/)
  and [deployments](https://developers.cloudflare.com/api/resources/pages/subresources/projects/subresources/deployments/methods/list/)
  have documented `page` / `per_page` inputs. The adapter requires explicit integer
  page/count/size/total-count/total-pages metadata, consumes every declared page,
  rejects duplicate IDs/names and observed count drift, and checks the final number
  of unique records against the declared total. Optional metadata being absent is
  an incomplete-evidence failure. Deployment project bindings and native timestamps
  are checked. Preview and skipped deployments are retained without converting them
  into build counts. Empty inventories stay unknown, including explicit total zero.
- [Subscriptions](https://developers.cloudflare.com/api/resources/accounts/subresources/subscriptions/methods/get/)
  documents response counts but no page-selection query parameters. Only an explicit,
  nonempty, complete first page is accepted; larger lists fail with
  `unsupported_subscription_pagination` without guessed follow-up queries. Consumed
  contract fields must be present and correctly typed, even where the provider schema
  marks them optional. Native price, period, state, scope and rate-plan fields are
  preserved privately, not interpreted as a Pages/Workers plan or billing control.

These are conservative acceptance rules, not evidence that a live account returns
all required metadata. Even consistent pagination does not establish an atomic
snapshot or retention of deleted projects/deployments. The official
[Pages API guide](https://developers.cloudflare.com/pages/configuration/api/#deleting-old-deployments-after-a-week)
explicitly supports deleting old deployments, and the
[limits table](https://developers.cloudflare.com/pages/platform/limits/#builds)
specifies the monthly allowance without a deployment-to-quota accounting mapping.
The inspected contracts do not
define which retained deployment records equal monthly quota-charged builds, provide
a verified Pages/Workers product mapping for every subscription, or attest automatic
billing is disabled. `free`, zero price, `is_contract=false`, an empty subscription
list, or a reachable endpoint cannot substitute for those missing facts. The gate
therefore remains BLOCKED. This adapter has offline fixture coverage; additional live
API calls and operational acceptance have not been performed.

`quota(..., publication=True, collected=True)` validates all nine schema-v2 metric
structures, scopes, periods, freshness and billing constraints before returning the
existing `free-plans-only` publication result. It ignores numeric usage warning
thresholds, so valid TAF/Sync exhaustion does not interrupt MSM publication. Unknown,
incomplete, stale or malformed collection and nonzero billing still fail. This flag is
an internal validator mode, **not proof that collection occurred**. The legacy CLI and
plan-only migration interface remain diagnostic tools. The usage monitor uses
`collection_gate.py`, which always uses `collected=True`; it cannot fall back to that
legacy interface when collection fails. Static-only publication uses the separate gate above.

The gate assembles an in-memory report only when all required plans, per-plan
observations, nine metrics and official-limit attestations are available. It preserves
native observation timestamps and uses the oldest plan observation. Only the monitor
invokes it with step-scoped read credentials. Collection failure
stops the gate without consulting `STATIC_QUOTA_REPORT` or cached reports. Missing native
mappings still block healthy usage-monitor acceptance; periodic manual data entry cannot
satisfy that gate. Recurring publication still needs separate policy approval and live acceptance. Live failure-notification delivery and
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
live HTML acceptance remains unverified; a successful fetch, digest, fixture,
account query or test run must never renew that timestamp by itself. The four Firestore
fragments have offline integration coverage against the existing schema-v2 validator;
fixtures do not prove API availability or explicit zero-series coverage on this account.

The full #123 implementation is still incomplete: Cloudflare plan/account-total adapters,
Firestore outbound's current three-surface finding, GitHub accrued SKU/shared allowance
and billing controls, live limit-verifier acceptance and unattended Google authentication
remain unverified or unsupported. In-memory report assembly, fixed-status audit output,
and direct monitor/publication workflow routing are implemented. Final validation uses
the collection completion time, so a provider reset during collection cannot keep an old
period valid; original evidence timestamps are preserved. These changes are not
unattended acceptance. Keep recurring publication disabled.

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

### GET-only Pages target observation (#250)

Select `cloudflare_pages_target` in `static-provider-probe` with the reviewed branch
and its full `reviewed_sha`. This opt-in check uses the existing diagnostic step's
`STATIC_CLOUDFLARE_READ_TOKEN`; it adds no credential or permission. It reads the
fixed `navmate` project, every retained deployment page (production and preview),
then the project again. Existing request/time limits apply. Missing pagination
metadata, empty results, duplicate IDs, scope mismatches and incomplete inventories
fail closed. Missing optional fields remain `UNKNOWN` (a missing canonical
deployment is `null`), never an inferred zero or absence.

Output contains the reviewed SHA, observation time, validated project/deployment
IDs and commits, fixed states and presence/match booleans. Domain, branch and
project comparisons target `navmate.yuto24.com`, `main` and `navmate`.
`account_scope_from_configured_path` means the configured account was used in the
request path; it does not independently establish account ownership. Raw settings,
binding values, environment values and commit messages are not emitted.

Deployment observations include the strict boolean `is_skipped`, an allowlisted
`skip_reason`, and provider creation/modification and latest-stage start/end times
normalized to UTC at Python microsecond precision (finer fractions are truncated).
Invalid/missing times remain `UNKNOWN`; explicit null stays null.
They are never replaced by the observation clock or used to declare old work safe.
`activity_counts` distinguishes `ACTIVE`, `PENDING`, `SKIPPED`, `FINISHED` and
`UNKNOWN`. Explicitly skipped rows are retained in `skipped`; pending/active/unknown
rows remain in `in_flight`. Conflicting skipped/active signals remain `UNKNOWN`.
A known skip reason alone is not proof that a row was skipped. These are observations
of the retained inventory, not a prediction of future queue activity.

`production_binding_state` distinguishes `missing`, `null`, `empty`, `nonempty`
and `invalid` map-of-objects shapes. An unavailable parent configuration leaves the
child states `UNKNOWN`, with its shape in `production_configuration_state`. Shape
checks do not validate resources or grant publication permission. The existing
presence booleans stay unknown for missing, null or invalid shapes.
`production_env_metadata` reports only the fixed build-variable names already in
`publication_gate.CONFIGURATION`, their `plain_text`/`secret_text`/`missing`/`invalid`
classification, and the count of other variable names. It never emits variable
values or unknown names, and does not establish value equality with approved config.
An empty map can establish zero other names; an unavailable map cannot.
The publication gate allows existing nonempty production `env_vars`, including
unknown names and secret types, without reading their values into the build or
changing the Pages settings. They do not prove Functions code is deployed or
replace the approved public build configuration. Other binding and static-artifact
checks remain enforced. No additional API calls are made.

The production identity comes from `canonical_deployment`, not the latest preview.
A complete retained inventory and unchanged project reread are observations, not
an atomic snapshot or assurance against a deployment starting afterward. Functions
and binding presence do not prove which code is deployed. The command still exits
1 with `BLOCKED`: it cannot authorize publication, renew evidence timestamps or
change quota/plan gates. See Cloudflare's
[project API](https://developers.cloudflare.com/api/resources/pages/subresources/projects/methods/get/)
and [deployments API](https://developers.cloudflare.com/api/resources/pages/subresources/projects/subresources/deployments/methods/list/).

Before a separately approved publication, record the current successful production
deployment ID and full commit, resolve unknown/mismatched target facts, and check
again for in-flight work. Preserve the approved app/configuration when regenerating
fresh MSM data; a Pages rollback to expired data alone does not restore freshness.
The existing rollback procedure above remains applicable.

This Pages permission does not establish Workers access. TAF's current deployment
version, traffic allocation and settings require the operator's existing authorized
Workers dashboard/API read. Compare those settings with `services/taf-proxy` and
`docs/taf_proxy.md`, without exporting secret values. Do not create credentials or
reuse the Pages token to discover Workers privileges. A separately approved TAF
rollback uses a recorded eligible Worker version; it does not roll back connected
resources. See [Workers rollback limitations](https://developers.cloudflare.com/workers/versions-and-deployments/rollbacks/).
