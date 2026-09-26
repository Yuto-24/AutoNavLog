# #123 production activation audit — 2026-09-26 JST

This is an in-progress audit. Do not use it as evidence that production automation
or the #123 acceptance criteria are complete. The #121 production acceptance
remains recorded separately in `../issue-121/remote-production-2026-09-24.md`.

## Initial state and configured GitHub settings

- At 2026-09-26 02:51 JST, `navmate.pages.dev/release.json` reported clean
  v1.20.1 from `a4de2a226dfa205b8be42fb62a710a1b06271ed8`, matching #121.
  The MSM catalog reported generation at 2026-09-24 11:53:06 UTC and expiry
  at 17:53:06 UTC. The read-only `operations.py monitor` correctly failed on
  this expired catalog. There is no evidence of a newer production feed.
- GitHub's repository variables, environments, and secrets were initially empty.
  The latest observed `static-production` and `static-monitor` schedule runs
  were all `skipped`; they do not count as scheduled refresh or monitoring.
- The repository is public and Actions and both workflows are enabled.
  Repository variables now contain `STATIC_ORIGIN`, `STATIC_APPROVED_SHA`,
  `CLOUDFLARE_ACCOUNT_ID`, and the six public `VITE_*` values from the canonical
  release. No secrets were put in repository variables.
- GitHub environments `static-production`, `static-publication`, and
  `static-validation` now restrict deployments to branch `main`.
  `static-production` has no reviewer so an approved recurring schedule can run
  unattended. `static-publication` requires owner review for one-off publishing.
  The workflow change routing one-off publication to that separate environment
  is pending review and merge; schedules remain disabled.
- Neither environment has `CLOUDFLARE_PAGES_TOKEN`. `STATIC_QUOTA_REPORT`,
  `STATIC_MONITOR_ENABLED`, and `STATIC_AUTOMATION_ENABLED` are unset. No
  production upload or rollback was attempted. The existing Wrangler OAuth
  credential expired on 2026-09-24 and the Cloudflare API returned HTTP 401.

## Evidence still required

- Current target-bound Cloudflare Free, Firebase Spark and disabled billing,
  plus account-shared usage observations for every quota metric in
  `docs/static_quota.example.json`. #121's 2026-09-24 observations do not
  establish current-period usage. The Cloudflare account dashboard and
  GitHub owner billing data are not readable with the current credentials.
- Install least-privilege Pages Edit token in both production environments;
  confirm GitHub Actions failure notification delivery with an intentional
  failed monitor run. A workflow failure alone does not prove message receipt.
- Build-only and production publication checks; at least two **actual**
  successful scheduled MSM renewals spanning the old catalog expiry; canonical
  whole inventory, catalog and payload continuity after deployment.
- Pause the scheduler, wait for uploads to finish, perform same-origin Pages
  rollback, verify saved Local Project and Last Calculation on the same origin,
  then restore canonical deployment and the intended scheduler state.

Official limits checked on 2026-09-26: [Pages](https://developers.cloudflare.com/pages/platform/limits/),
[Workers](https://developers.cloudflare.com/workers/platform/pricing/),
[Firestore](https://firebase.google.com/docs/firestore/quotas),
[Firebase plans](https://firebase.google.com/pricing/), and
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
These public limits do not substitute for current account plan and usage evidence.
