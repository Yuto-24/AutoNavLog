# #121 remote production acceptance audit — 2026-09-24

The private source record is
`.autonavlog-data/release-artifacts/issue-121/v1.20.1-msm-20260924/acceptance.json`.
This note records safe identifiers and results only. It does not include Firebase Web
configuration, credentials, tokens, cookies, or account data.

## Confirmed delivery

- `https://navmate.pages.dev` serves v1.20.1 from clean commit
  `a4de2a226dfa205b8be42fb62a710a1b06271ed8`. The final production
  deployment is `17d42f3c-8f82-4d31-8af9-9015a8a06824` on `navmate/main`.
- `check:static` passed. Artifact tests: 9 passed. Ordinary static and local Pages
  browser acceptance: 10 passed each. The artifact has 144 files and its largest
  asset is 10,064,649 bytes, within the checked Pages Free limits.
- Production HTTPS served the expected `release.json`, Local manifest and MSM
  catalog. The manifest hash and catalog bytes matched the artifact. Same-origin
  responses used `Cache-Control: no-cache`; a conditional ETag request returned
  304, and a missing Local asset returned 404.
- The 2026-09-25 14:10 JST FORECAST calculation succeeded on the production
  browser using real MSM Run `20260924060000`. The real RJFM TAF request returned
  HTTP 200, and destination information displayed `110/10`. No `/api/**`
  request or browser page error was observed.
- The catalog was fresh during acceptance and covered the requested forecast
  instant. Its six-hour catalog lifetime ends at 2026-09-25 02:53 JST. Future
  feed renewal belongs to #123; that later expiry does not invalidate this
  acceptance result.

The operator additionally reported completed production Google/Legacy owner
linking, migration `2/2`, post-`NAVMATE_ACTIVE` redirect, fresh-browser Project
restore, and Last Calculation Account Sync restore. The old Legacy Last
Calculation had a fingerprint mismatch and was correctly excluded. These
account-specific observations are not contained in the private weather/artifact
record above; they are operator-supplied acceptance evidence.

## Failure isolation evidence

- The production artifact browser test in `web/e2e/static-production.spec.ts`
  injects a TAF 503 and an MSM catalog 503 in the browser. It verifies real-feed
  FORECAST, Local Save/reload, and retention of the prior NAV LOG after the MSM
  acquisition failure, without changing a shared production service. Both
  ordinary static and local Pages runs passed on this artifact.
- #145's [production acceptance record](https://github.com/Yuto-24/AutoNavLog/issues/145#issuecomment-5733318942)
  separately verified that a real TAF Worker failure left calculation, Project,
  checkpoint, and Last Calculation usable, then verified recovery. That Worker
  rollback does not count as a Pages rollback.
- Account Sync backend/quota failure and offline Local writes are covered by
  `web/e2e/sync-unit.spec.ts` and `web/e2e/sync.spec.ts`; cached authentication
  startup failure is covered by `web/e2e/auth.spec.ts`. These establish the Local
  failure contract. No shared production Firebase service was stopped for this
  audit.

## Same-origin Pages rollback

An isolated browser profile created and saved an FTD Project with a Last
Calculation at `https://navmate.pages.dev`. To avoid changing the user-facing
artifact, the **same checked artifact** was uploaded again as production
deployment `a79c4e89-41c4-44b0-beb9-154c940c507a` (0 new files). The Pages
rollback API then returned HTTP 200 and restored deployment
`17d42f3c-8f82-4d31-8af9-9015a8a06824` as the project's
`canonical_deployment`.

After a new browser process opened the same profile and origin, the Local
Project ID and the SHA-256 of its draft, checkpoint, and Last Calculation all
matched the pre-rollback values. The saved NAV LOG opened without recalculation;
Legacy API requests remained zero. This tests Pages rollback and origin storage
retention between identical application artifacts. It does not establish schema
compatibility with an older application release.

An additional still-open production tab was reloaded after a second upload of
the identical artifact (deployment `5918f869-306b-45d2-8e21-7fbdf698b0f2`,
0 new files). It stayed on
`https://navmate.pages.dev` and retained the `autonavlog.projects` IndexedDB
database. The Pages rollback API again restored `17d42f3c-8f82-4d31-8af9-9015a8a06824`
as the canonical deployment. The separate process-restart check above verified
the stored Project and Last Calculation content; the open-tab check verified
reload and database continuity, not a second content-hash comparison.

## Free-tier and current usage audit

- Cloudflare account `cae366c3bb569163773b80f4bdff2d8e` reports
  `workers/settings.free_tier=true`. The operator confirmed the account plan is
  **Free**. The dashboard showed 6 / 100,000 Worker requests today, 58 requests
  for September 1–24, 80 ms of CPU and 0 minutes of Workers Builds. The earlier
  account-wide GraphQL query reported 6 requests, 0 runtime errors and 2
  subrequests for the queried UTC day; metrics may have different windows or lag.
  The Pages project has no Functions. The account has one Pages project and 15
  September deployment records after rollback checks. The project remains on
  `main` with automatic production and preview deployment disabled.
- NavMate's active Pages deployment path is Wrangler **Direct Upload** of a
  prebuilt static artifact. Cloudflare's [Direct Upload guide](https://developers.cloudflare.com/pages/get-started/direct-upload/)
  describes this as uploading assets built before deployment; this path does
  not invoke a Pages build. The 0 minutes of **Workers Builds** is a separate
  dashboard metric and is not presented as a Pages build-quota reading. The 15
  deployment records are also not a Pages build-quota reading. The confirmed
  Free plan, prebuilt Direct Upload workflow and asset-limit check establish
  the initial Pages Free operation. Future automated build/deploy volume and
  account-shared quota monitoring belong to #123.
- Firebase project `navmate-prod` reports `billingEnabled=false`, no billing
  account, and `Firestore (default).freeTier=true`. The operator confirmed the
  project is on **Spark** and that Firebase Usage displayed **61 reads, 18
  writes and 0 deletes**; the allocation view showed reads and writes below
  0.1% of their daily limits. An earlier Cloud Monitoring query returned 70
  reads and 20 writes over its preceding 24-hour window; these are different
  sources/windows and are not substituted for the operator's Usage reading.
- The `(default)` database was newly created during this acceptance, with no
  earlier stored data. Even if all 18 writes created distinct documents at the
  [1 MiB document maximum](https://firebase.google.com/docs/firestore/quotas)
  and each reached the 8 MiB maximum sum of index entries, document-plus-index
  storage would be about **162 MiB**, versus the 1 GiB free quota. Firestore
  can include additional storage overhead, so this is a conservative
  application-data estimate, not a measured billable-byte count. Similarly,
  61 reads of maximum-size documents represent about **61 MiB** of document
  payload, far below the 10 GiB monthly outbound allowance; serialized
  responses and other request overhead are not measured by this estimate.
  Exact stored bytes and outbound GiB were unavailable in the console. The
  large margin, new database, Spark plan and disabled billing support the
  initial Free-tier acceptance; ongoing usage checks belong to #123.
- The current official [Pages limits](https://developers.cloudflare.com/pages/platform/limits/),
  [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/),
  and [Firestore free quota](https://firebase.google.com/docs/firestore/quotas)
  were checked. The artifact and observed request counts fit the documented
  Free-tier limits. No paid feature or billing change was made.

**Gate result:** the Free/Spark plan and observed initial usage, combined with
the conservative Firestore size estimates, satisfy #121's requirement that
normal initial operation not require a paid Cloud service. The missing exact
stored-byte and outbound-GiB counters remain measurement limits, not evidence
of zero usage. The account-shared Pages build counter was not read directly;
the current Wrangler Direct Upload path uses a prebuilt artifact and does not
consume a Pages build. #123 owns future feed/deployment automation and ongoing
Free-tier monitoring. The #121 remote production acceptance is complete.
