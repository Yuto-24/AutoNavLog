# GitHub Release version lines

| Push branch | Required VERSION major | Release title | Latest |
| --- | --- | --- | --- |
| `main` | 2 | `NavMate X.Y.Z` | yes |
| `release/1.x` | 1 | `AutoNavLog X.Y.Z` | never |

## Ordinary development and explicit releases

Ordinary feature/fix PRs use `Release: not-required` and a `Reason:` line on either
branch. Keep `VERSION` and `CHANGELOG.md` unchanged, including published notes.
Collect pending user-visible changes in the PR body's Pending release notes section.
No Unreleased section or new version is added for each development PR.

Only an explicit release-preparation PR uses `Release: required`: select the next
version, increment `VERSION`, and add finalized notes collected from the included
PRs as a new CHANGELOG section. Do not rewrite published sections. CI checks this
separation; a feature/fix does not itself require an immediate GitHub Release.

`publish-github-release.yml` runs only for pushes changing `VERSION`, validates `VERSION` and `CHANGELOG.md`, then publishes
only the matching line. Tags retain the shared historical `vX.Y.Z` format. Release
notes contain only the matching CHANGELOG section. CI runs on pushes to both lines;
PR checks remain enabled for every target branch.

Publication is serialized across both lines with `queue: max` (up to 100 pending
runs), rather than replacing the sole pending run on a new push. Inspect and rerun
any run cancelled because the queue is full. Existing tags are never moved or
deleted. An existing tag must resolve (including annotated tags) to an ancestor of
the triggering commit with the same version and release notes. This permits later
maintenance pushes without republishing, while failing on conflicting tags. An
existing stable release is left unchanged only after these checks. A tag without a
release can be published at its verified original commit. API errors other than an
explicit 404 stop publication; draft/prerelease collisions also require inspection.

## Establishing the v1 maintenance branch

`release/1.x` was created from pre-branding v1.22.9 commit `f364b142`.
The v1.23.0 candidate backports VREP #292 and release-line support #296 without
merging v2 main or #282 / #283. This is a repository baseline, not confirmation of
the image currently running on matsu. Keep these release-support files together:

- `.github/workflows/publish-github-release.yml`
- `.github/workflows/test.yml` (merge the branch trigger and standalone test entry;
  retain checks appropriate to that v1 checkout)
- `scripts/publish_github_release.py` and its `scripts/release_content.py` dependency
- `tests/unit/test_publish_github_release.py`,
  `tests/integration/check_publish_github_release.py`, and this procedure

Run the focused release tests and source validation on the actual v1 checkout.
The workflow on main alone does not install a workflow on an older release branch.
Preparing the candidate does not authorize merge, tag/Release creation, or deployment.

MSM/provider and Pages deployment workflows remain main-only and unchanged. GitHub
Release publication is not an app deployment, does not update matsu, and does not
change Pages, credentials, branch protections, or production switches.

## VREP v1 upgrade and rollback boundary

Old v1 Projects without `reference_snapshot` remain readable by the candidate.
Selected VREPs keep selection-time coordinates and source provenance in the saved
Project; loading never refreshes them from the current catalog. Repeated selections
remain distinct route occurrences. Confirmed-route editing is not included.

**An image-only rollback to v1.22.9 is not data-compatible.** Its RouteNode model
uses `extra="forbid"` and has no `reference_snapshot` field. Candidate serialization
adds that field even as `null` on ordinary nodes, so the risk is not limited to VREP
routes. Do not strip fields or silently restore an older volume to make it load.

Before any separately authorized matsu update, identify its actual image digest,
commit, VERSION, Compose project and volume; take a consistent backup with writes
stopped, and retain the old image and configuration. Verify old Projects, new VREP
routes, calculation, save and reload on a **copy** in an isolated Compose project.
Keep both the pre-upgrade backup and any post-upgrade writes. If rollback becomes
necessary, stop writes and preserve the candidate data first; obtain explicit
approval for restoring the matching pre-upgrade backup (which omits newer writes),
or keep the candidate until a separately reviewed reverse migration exists.
Never use `docker compose down -v` against the running service.
