# Stable Releases to Serverless production

After explicit activation, a published stable `vX.Y.Z` GitHub Release is the app
publication authorization for unchanged access/data contracts. Ordinary PR merges
do not publish the app. Both `release/1.x` and `main` target the existing
`https://navmate.yuto24.com` Pages project `navmate`, production branch `main`.
The Pages branch is a delivery target, independent of the application source branch.
Validate this delivery first with 1.x. After moving production to 2.x, end 1.x
maintenance/releases; the global version comparison also prevents a late 1.x
Release from replacing 2.x. No legacy server stop is part of this mechanism.

## Events and identity

`static-app-release.yml` has three entrances, all gated by repository variable
`STATIC_APP_RELEASE_ENABLED == true` (absent is disabled):

- `workflow_run: completed` of successful `publish-github-release` pushes on
  `main` / `release/1.x`. This is the default bridge for Releases created with
  `GITHUB_TOKEN`; those Releases do not themselves trigger `release` workflows.
  No PAT, dispatch step or deployment tool backport is needed in the v1 publisher.
- `release: published` for Releases created through the UI or another authorized
  publisher. Drafts and prereleases are rejected. This entrance needs the workflow
  present in the released ref and environment permission for its tag (see setup).
- Manual `workflow_dispatch` **on main**, with an existing numeric `release_id`,
  for a missed event, first selected Release or a safe retry. It is not a rollback.

The bridge re-reads the publisher run (path, push event, success, same repository,
supported branch, full SHA), obtains VERSION from that commit, and requires the
actual stable Release and immutable tag ancestor. Every entrance peels annotated
tags, compares API resolution with the fetched tag, checks exact VERSION and
ancestry in the matching source branch, and rechecks identity at every snapshot.
It never uses `latest`, release titles or `target_commitish` as app identity.
Trusted tooling is checked out from main once, retained at that SHA in the plan;
candidate release scripts only execute inside the existing credential-free Docker
build. No publisher-run artifacts or caches are downloaded or executed.

## Publication and failure behavior

Both feed and app workflows hold **workflow-level** `static-production` concurrency,
`cancel-in-progress: false`, `queue: max`. This retains up to 100 pending runs;
GitHub does not promise ordering. If capacity is exceeded, inspect cancellations
and replay the required Release. At the time each run acquires the lease:

1. Canonical clean `release.json.commit` must equal `STATIC_APPROVED_SHA`. Compare
   numeric X.Y.Z tuples to the canonical version: older Releases skip, the same
   version and SHA skip, the same version with a different SHA blocks. Only a newer
   version can publish. A queued older Release cannot roll production backward.
2. Reuse `app_release.prepare`: pin application SHA, tooling SHA, configuration,
   Pages identity and exact Rules/indexes/Project schema contracts; compare live
   Rules against source, build and test in isolation, and retain the plan digest.
   After the container hands back only the writable inventory/hint paths, trusted
   tooling seals `freshness.json` into that inventory so MSM fallback keeps working.
3. Refresh short-lived Rules read authentication, then reuse `app_release.publish`
   with that policy-authorized digest. This does **not** claim a human reviewed or
   approved the generated plan. Existing 30-minute plan freshness/drift gates apply.
4. Pause feed automation and its workflow, rebuild fresh, validate inventory and
   fresh forecast coverage, upload static bytes, and verify canonical inventory,
   serving headers, HTML/manifest, feed and Pages identity. Only then update
   `STATIC_APPROVED_SHA`; verify again before restoring the previous scheduler state.

The Actions operator allows pending work behind its own shared lease. The manual
operator rejects every unfinished feed **and app** run, so it cannot overlap this
publisher. Manual operations still require their existing explicit operation window.
Keep all production app/feed publication workflows in this concurrency group.

Rules are compared as exact UTF-8 bytes (including whitespace/line endings), and
indexes/schema by exact git blob identity. A Rules change hidden in an application
Release, unknown/missing contracts, changed indexes/schema, live Rules drift or
unknown provider state blocks **before any upload**. The automatic route has no
Rules-transition input, compatibility override, Firebase write API or automatic
approval. Changed Rules require individual authorization, current/candidate
compatibility evidence and the existing separate migration procedure in
[static_operations.md](static_operations.md#rulesが異なる更新だけに必要な検証記録).
An environment approval alone does not establish compatibility. Reconcile and
publish that reviewed transition through the manual path; a replay then sees the
same canonical version/SHA and skips. Index migrations remain blocked. A separately reviewed manual one-way schema
transition is documented in [schema_transition.md](schema_transition.md); the automatic
route has no schema-transition input.

On pause/upload/verification/approved-SHA failure, keep the existing journal's
UNKNOWN/CONFIRMED distinctions. There is no unconditional resume or automatic
rollback. A new run uses a new private runner-temp store and prepares again; it
never resumes an old partial upload. If upload succeeded but approved SHA did not
advance, canonical/approved mismatch blocks replay until separately authorized
reconciliation. If the scheduler was paused or its workflow disabled, inspect and
recover under the existing runbook before retrying. Do not simply re-enable it.
Intentional rollback uses the separate reviewed rollback procedure, never this
monotonic automatic route.

Each prepare/publish step has a 50-minute timeout and a fresh one-hour read token.
Token expiry, cancellation or timeout fails closed; inspect remote state before
any retry. The safe stage/status/mutation summary goes to the Actions summary.
Detailed plan and journals are private runner-temp files, not uploaded as public
artifacts; they may be unavailable after runner teardown, so use read-only provider
state for recovery when necessary. Do not log tokens or candidate command output.

## Activation prerequisites and first publication (separate authorization)

This implementation does not change any live setting, credential, protection,
Rules, Release, deployment or scheduler. Before enabling it, review and prepare:

| Setting | Requirement |
| --- | --- |
| `STATIC_APP_RELEASE_ENABLED` repository variable | Remain absent/false until setup and the first selected 1.x Release are authorized. Set true only for activation. Removing/setting false stops future jobs and subsequent snapshots. |
| `STATIC_APP_RELEASE_GH_TOKEN` secret in `static-production` | Existing dedicated repository credential able to read Releases/contents, Actions runs/workflows, repository/environment variable inventories, and write Actions variables plus enable/disable the feed workflow. A fine-grained token needs Contents read, Actions write, Variables write, Environments read, Metadata read on this repository. Do not grant release creation, Rules write or environment-administration rights. `GITHUB_TOKEN` cannot write Actions variables. |
| `CLOUDFLARE_PAGES_TOKEN` | Reuse the existing environment credential; app release needs the existing Pages read/upload capabilities, no new Pages project or domain. |
| `STATIC_GOOGLE_WORKLOAD_IDENTITY_PROVIDER`, `STATIC_GOOGLE_SERVICE_ACCOUNT` repository variables | Reviewed Google Workload Identity Federation for this repository/workflow and production environment, impersonating an account with Rules **read only** (`firebaserules.releases.get`, `firebaserules.rulesets.get`). OAuth `firebase.readonly`; no key file, stored access token, Rules deployment role or lifetime-extension policy. Establishing trust/permissions is a separate authorized setup task. |
| `static-production` environment | Keep unattended protection for compatible releases. Its current main branch policy already supports the default workflow_run/manual route for both version lines. Direct `release: published` jobs also need separately reviewed tag allowances, limited to official v1/v2 tags; otherwise use the default completion bridge/manual main replay. Do not weaken existing protections. |
| Existing public configuration and `STATIC_APPROVED_SHA` | Preserve exact canonical configuration; require complete repository/environment inventories without shadowing. `STATIC_AUTOMATION_ENABLED=true` and active feed workflow are required before starting an unattended release. |

Read-only setup inventory on 2026-10-28 found only `CLOUDFLARE_PAGES_TOKEN` in the
`static-production` environment and only `STATIC_CLOUDFLARE_READ_TOKEN` at repository
scope. The new opt-in, GitHub credential and WIF variables were absent; environment
deployment policy allowed only branch `main`. No setting was changed. Refresh this
inventory before activation; names/existence are not credential capability proof.

First-publication checklist:

1. Merge reviewed tooling only after normal CI/reviews. Do not create a release or
   enable the opt-in as part of merging this implementation.
2. Select a new stable 1.x release-preparation commit on `release/1.x`, validate the
   candidate/current contracts and existing runtime evidence. VERSION/CHANGELOG
   follow the established explicit release-preparation policy. If Rules differ from
   current production, finish the separately approved transition first.
3. Under separate setup authorization, confirm credentials/WIF permissions,
   canonical/approved alignment, Pages identity, preserved configuration and the
   feed's enabled/idle state. Authorize enabling the opt-in and that first Release.
4. Create the Release through the existing v1 release publisher, or replay the
   explicitly selected existing Release ID on main. Verify the exact workflow run,
   release ID/tag/SHA, canonical app version, approved SHA and fresh MSM inventory.
   A passing PR, accepted event or uploaded artifact is not production acceptance.
5. Confirm the next natural forecast-only publication retains the new app SHA and
   configuration. Later compatible Releases need no per-release approval. A future
   Rules migration still needs its own authorization and evidence.

References: [GitHub event/token behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow),
[workflow_run](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run),
[concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency),
[Google auth action](https://github.com/google-github-actions/auth),
[Rules read scopes](https://firebase.google.com/docs/reference/rules/rest/v1/projects.releases/get).
