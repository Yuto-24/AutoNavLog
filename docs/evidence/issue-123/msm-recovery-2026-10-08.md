# MSM renewal recovery — 2026-10-08 JST

This audit and local build did not merge, deploy, enable schedules, change credentials,
provider settings or the independent TAF Worker. The current task explicitly separates
mandatory static-only publication safety from optional-service usage monitoring. The
historical #123 statements coupling both gates are superseded for this renewal path.

## Current production reads

Observed at 2026-10-08 01:16 JST (2026-10-07 16:16 UTC), using existing GitHub
authentication and canonical HTTPS reads. Secret endpoints were used for **names only**;
no secret value was retrieved or printed. Public configuration was compared internally.

- Repository is public. PR #239 remote HEAD was exactly
  `7e3d44101212439cf5346f01509cd3d7d0b2e175`; no advance from the supplied HEAD.
- Origin is `https://navmate.yuto24.com`; expected Pages project is `navmate`.
- Production is clean version `1.20.1`, commit
  `a4de2a226dfa205b8be42fb62a710a1b06271ed8`, matching `STATIC_APPROVED_SHA`.
- All six public `VITE_*` configuration entries match repository variables. The three
  publication/validation environments have no variable overrides. Existing Pages-token
  metadata is present in production/publication, absent from validation.
- Served `release.json` SHA-256:
  `518bc7cf5d8e18775597d1a757ff238cbe85bfb4e2afb8fad196a8d7ace42782`.
- Catalog matches its release inventory, but is **expired**: generated
  `2026-09-28T14:55:46.203544Z`, expires `2026-09-28T20:55:46.203544Z`, six assets.
  These dates were obtained in this audit; their old freshness is not a current pass.
- `STATIC_AUTOMATION_ENABLED` / `STATIC_MONITOR_ENABLED` are unset.
- Pages management GET with the existing local OAuth returned **HTTP 401**. Actual
  project/deployment ID/production branch/runtime settings/in-flight work are UNKNOWN.
  Public release identity is not a substitute for this management evidence. No token
  renewal, new auth, permission or billing change was attempted.

## Local real-MSM build-only evidence

The trusted existing producer obtained Runs `20261007060000` and `20261007090000`.
The expired production catalog had no assets within the seven-day retention window;
restoration retained zero assets for this run. Rolling retention remains implemented
and covered by isolated tests; this particular live sample does not prove retained reuse.

- Newly generated catalog: `2026-10-07T16:26:36.310554Z`, six-hour expiry
  `2026-10-07T22:26:36.310554Z`. These are **time-bound local evidence**, not production.
- Hash/byte checks and native payload decoding pass for both Runs. The native library
  verifies wind, aloft temperature and surface temperature coverage from
  `2026-10-07T16:00:00Z` through the next 24 hours.
- A clean checkout of the currently approved application SHA and the matching public
  settings built successfully inside the candidate sandbox (Python 3.12 / Node 24).
  Production Browser E2E passed **10 tests**, including served real-MSM calculation,
  weather-failure retention, saved NAV LOG/reload and browser-process restart.
- Independent host inventory/configuration/private-material validation and isolated
  upload staging pass. The candidate has 142 files; `release.json` SHA-256 is
  `dc33cfe2f0c3c220a2cab201b79181fa7823e0b57874e6809af08476f272eda9`.
- Real Docker boundary checks confirm no tooling mount, Docker socket, provider token or
  runner output-command files; the mounted feed rejects writes. Candidate pip/npm/build
  code runs in that container, while validators/upload tooling stay on the host.
- Scoped operations/collection/retention/verification tests: **358 passed**. Independent
  model review confirmed runtime/private-material rejection and resolved the initial
  candidate-can-replace-validator finding by container isolation and stdlib validation.
- Full local Docker CI: **1,491 passed / 1 warning**, Ruff, mypy (77 files), release,
  performance/runtime data, schema and sdist/wheel checks pass. Web Application tests
  **89 passed**, static artifact/release-notes tests, typecheck and build pass. Six
  additional sealed-Worker/secret/expired-artifact regressions pass after that full run.
- Isolated Compose runtime `autonavlog-wt-84b51acb` is healthy at
  `http://127.0.0.1:45174`, version `1.22.5`. Its generated named volume is retained;
  the primary working directory, `.codex/` and primary runtime/volume were untouched.

## Remaining gates

Pages project/current deployment/in-flight state need a current read through existing
authorized management access before any production approval. The upload gate independently
reads the existing project with the existing Pages credential and rejects wrong
project/branch/domain or production runtime bindings; it does not create a project.

Hosted PR CI, actual publication and at least two real scheduled renewals are distinct
evidence. Local build/E2E do not prove the scheduled path or authorize activation. Usage
collection remains incomplete/UNKNOWN, including Google unattended authentication and
Workers/Firestore/GitHub shared allowance. UNKNOWN does not mean zero usage, healthy
services or account-wide zero billing. TAF and Sync stay enabled in public configuration.

After reviewing the PR and latest CI, seek separate approval for merge, a current
build-only dispatch and bounded publication, then separately for the recurring policy.
Rebuild fresh feed for an approved publication; this local catalog must not be reused
after expiry. New authentication, permissions, billing, FastAPI fallback and legacy-server
shutdown remain outside this task.
