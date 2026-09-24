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
  `workers/settings.free_tier=true`. Account-wide GraphQL analytics reported
  **6 Worker requests**, 0 runtime errors and 2 subrequests for the current UTC
  day at the time of the query; all reported requests belonged to `autonavlog-taf`.
  Metrics can lag. The Pages project has no Functions, and the account has one
  Pages project with 15 September deployments after the rollback checks. The project remains on `main`
  with automatic production and preview deployment disabled.
- Firebase project `navmate-prod` reports `billingEnabled=false`, no billing
  account, and `Firestore (default).freeTier=true`. Cloud Monitoring returned
  70 document reads and 20 writes over the preceding 24 hours. A delete metric
  returned no series, which is not proof of zero deletes. These Monitoring
  figures can lag and are not the billing report.
- The current official [Pages limits](https://developers.cloudflare.com/pages/platform/limits/),
  [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/),
  and [Firestore free quota](https://firebase.google.com/docs/firestore/quotas)
  were checked. The artifact and observed request counts fit the documented
  Free-tier limits. No paid feature or billing change was made.

**Still unverified:** the Cloudflare account subscription API returns HTTP 403,
and the browser dashboard is not signed in, so the Pages plan and its exact
account-shared monthly build usage are not directly confirmed. Fifteen
deployments are a conservative observed activity count, not the provider's
  build-quota reading. Cloud Monitoring returned no Firestore storage series and
did not provide a usable outbound-transfer metric; the current storage and
monthly outbound usage require the Firebase Usage dashboard or another
authoritative account-specific reading. These gaps leave the #121 Free-tier /
current-usage production gate open. #121 should remain OPEN, and #122 / #123
should retain their #121 dependency until the readings are confirmed.
