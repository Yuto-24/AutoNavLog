# MSM recovery publication preparation — 2026-10-21 JST

Scope: #123 MSM restoration using the existing Cloudflare Pages / Direct Upload
whole-artifact path. The application remains **v1.20.1**, approved SHA
`a4de2a226dfa205b8be42fb62a710a1b06271ed8`. No VERSION/CHANGELOG, application,
Rules, TAF Worker, provider settings, production deployment or scheduler changes
were made. #270 delivery separation and #273 independent monitoring are excluded.

## Canonical state

Read at 09:34 JST, using the existing `static_request` HTTPS/no-cache/no-redirect
contract and existing GitHub access. Values of secrets were never retrieved.

- `https://navmate.yuto24.com/release.json` is clean v1.20.1 at the approved SHA.
- All six public connection settings match repository variables. All three
  static environments have zero variable overrides; complete inventories checked.
- `STATIC_AUTOMATION_ENABLED=false`; `STATIC_MONITOR_ENABLED` is unset. The
  publication workflow is active but scheduled jobs skip under the false switch.
- The catalog matches the release inventory: 555,017 bytes, SHA-256
  `5aa1cc614e01b871767024c6a11644576339f26b4fc6bd12100b94dcfbfc7384`, 22 assets.
  Generated `2026-10-15T07:35:46.791957Z`, expired
  `2026-10-15T13:35:46.791957Z` (October 15, 22:35 JST). It remains expired;
  successful build-only work does not restore production.
- Existing Pages-token names are present in `static-production` and
  `static-publication`, absent in `static-validation`. Presence does not prove
  current edit-token validity. `static-publication` requires reviewer `Yuto-24`
  and permits branch `main`; protection was not changed.

## #250 management observation

[GET-only diagnostic run 39521438591](https://github.com/Yuto-24/AutoNavLog/actions/runs/39521438591),
reviewed tooling SHA `ae606ff3dde9ad55d2fea7032369838207642de6`, observed
`2026-10-21T00:27:39.362990Z` (09:27 JST). Existing
`STATIC_CLOUDFLARE_READ_TOKEN` was used only inside the diagnostic step.

- Configured account path, project `navmate`, production branch `main` and
  canonical domain match. Project ID `79bb24b3-39de-4eb5-ab84-729b995c25e2`.
  The configured account path is not independent proof of account ownership.
- Current canonical production deployment:
  `9c4851d5-9dd2-4062-92f6-0f0421e72a0f`, successful `deploy`, approved application
  SHA above, created `2026-10-15T07:41:03.194692Z`. Project and deployment both
  report `uses_functions=false`.
- Pagination complete: 181 retained deployments, 18 FINISHED and 163 explicitly
  SKIPPED; zero ACTIVE, PENDING or UNKNOWN activities. No in-flight entries.
  Canonical deployment is present in the inventory; project reread is unchanged.
- Production configuration is present. `env_vars` is nonempty: the known TAF
  variable is plain text and there are two other names, whose names/values are
  not reproduced. Five known build variables are missing from Pages settings;
  the isolated build obtains its matching public settings from GitHub instead.
- All twelve inspected non-env binding keys are **missing**, not explicit empty
  maps. Their presence remains **UNKNOWN** in the report. Do not claim that this
  observation proved every binding absent. The existing publication gate and
  sealed static-artifact checks must still pass; resolve material UNKNOWN target
  facts through the existing authorized management view before publication.

This diagnostic intentionally exits 1 / reports BLOCKED even when the observation
succeeds; it never grants publication or quota acceptance. The hosted run is
therefore recorded as **failure with a completed observation**, not successful CI.
The old local-OAuth 401 no longer blocks this observation route. #250 remains open:
repeat the inventory immediately before publication, resolve unknown target facts,
and record that observation. A GET inventory is not a lock against another operator.

At 17:34 JST, [repeat diagnostic 39570276909](https://github.com/Yuto-24/AutoNavLog/actions/runs/39570276909)
observed the same canonical deployment, target/configuration and missing binding
states. Pagination covered 182 deployments (18 FINISHED, 164 explicitly SKIPPED),
with no ACTIVE/PENDING/UNKNOWN activities, no in-flight entries and an unchanged
project reread. At 17:28 JST, canonical catalog/configuration and disabled switches
also remained unchanged. These later reads still do not authorize publication.

## Build-only verification

[Run 39521789417](https://github.com/Yuto-24/AutoNavLog/actions/runs/39521789417)
completed **successfully** at 09:36 JST, on tooling `ae606ff`, with
`source_sha=a4de2a226dfa205b8be42fb62a710a1b06271ed8`, `publish=false`.

- Existing identity/configuration/source isolation gates passed.
- Nine recent immutable assets were restored; fresh real MSM was generated and
  decoded/validated by the existing producer and 24-hour coverage gate.
- Static build, release/data/schema checks and artifact tests passed. Candidate:
  151 files, 77,892,232 bytes; catalog expiry `2026-10-21T06:32:10.531989Z`.
- Production Browser E2E: **10 passed**, including real-MSM FORECAST and Local
  persistence scenarios. This is an isolated production-artifact test, not live
  canonical-origin Auth/Sync/TAF acceptance.
- Independent post-build artifact/feed validation and build identity recording
  passed. Upload CLI installation, staging, upload and post-upload verification
  were all **skipped**. This run does not exercise #264's post-upload check.
- The runner does not retain a reusable production artifact. A later approved
  publication generates fresh weather again; never reuse this expired evidence.

PR #264's catalog fix was integrated with current main locally without changing
its behavior. The regression verifies an old or different catalog cannot pass
even when `release.json` matches, and evaluates freshness on the same received
catalog. Existing 300/30/10-second polling budgets remain unchanged.
The implementation chat uses GPT-6 Astra. Its separate Astra read-only review
found no actionable issue but is same-model review, not independent acceptance.
Explicitly selected **GPT-6.1 Sol** subsequently reviewed all six changed files
and the relevant request, polling and workflow boundaries: no actionable findings,
289 focused tests passed, and no checkout changes. Full local Docker CI passed
1,904 tests, Ruff, mypy (77 source files), release/data/schema checks and package
build; Web typecheck and the release/PR-body contract also passed. Historical
PR #264 HEAD `687709f` has GPT-6.1 Sol review, CodeRabbit approval, no review
threads and passing hosted checks; those do not substitute for final updated-HEAD
CI/review. Final test/review results are recorded on PR #264.

The isolated local Compose project `autonavlog-wt-07e29e1c` was rebuilt and is
healthy at `http://127.0.0.1:42376/healthz`, version 1.22.8 (tooling checkout).
Its named data volume is retained. Primary checkout/runtime/volume were not
changed. Local Compose health is not Static Web production acceptance.

## Publication handoff and remaining gates

1. Review updated PR #264 and explicitly authorize its merge after final hosted
   CI, independent review, CodeRabbit and unresolved-thread checks. No merge was
   performed during preparation. Do not change the approved application SHA.
2. Immediately before an approved one-off publication, reread canonical identity,
   GitHub switches/configuration, complete Actions run history, and #250 Pages
   inventory. Resolve unknown target facts and any concurrent work; prevent other
   operators from publishing during this window. Old observations are not a lease.
3. Authorize one `static-production.yml` dispatch on reviewed merged `main`,
   `source_sha=a4de2a226dfa205b8be42fb62a710a1b06271ed8`, `publish=true`.
   Reviewer `Yuto-24` approves the `static-publication` environment. This rebuilds
   fresh MSM and the existing app, then uses Pages Direct Upload. Leave automation
   false and monitor unset. No app-release/Rules transition is required.
4. Check canonical release inventory and actual catalog hash/bytes/freshness with
   #264, then independently monitor. Verify HTTPS/cache/ETag/404 and one live
   FORECAST with destination TAF, saved Project/Last Calculation and reload on
   the canonical origin. Auth/Sync remain enabled and unchanged; verify live
   behavior without treating isolated E2E as a substitute.
5. If post-upload checks fail, the deployment may already have changed. Inspect
   the recorded deployment/catalog and keep recurring publication paused. Do not
   automatically rollback or renew timestamps. An old Pages deployment containing
   expired MSM does not itself restore weather.
6. Recurring publication/monitor activation requires separate authorization and
   acceptance. Healthy unattended optional-service collection remains incomplete;
   UNKNOWN usage/billing is not zero. Keep the historical notification and
   same-origin rollback/Local retention evidence from October 3, without claiming
   a new test. Observe at least two actual scheduled renewals across the previous
   catalog expiry before completing #123/parent #116; #146 remains gated.

Official policy rechecked October 21: [Pages limits](https://developers.cloudflare.com/pages/platform/limits/)
still specify 20,000 files and 25 MiB per asset for this Free-path budget;
[static asset requests](https://developers.cloudflare.com/pages/functions/pricing/)
are free/unlimited when no Function is invoked. The repository is public and uses
standard `ubuntu-latest`, consistent with [GitHub's public-runner policy](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
These public limits and the artifact checks do not establish current account-wide
plans, shared usage or billing controls. No subscription, permission or billing
change is proposed for this MSM-only restoration.
