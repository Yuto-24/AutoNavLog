# GitHub Release version lines

| Push branch | Required VERSION major | Release title | Latest |
| --- | --- | --- | --- |
| `main` | 2 | `NavMate X.Y.Z` | yes |
| `release/1.x` | 1 | `AutoNavLog X.Y.Z` | never |

## Ordinary development and explicit releases

Ordinary feature/fix PRs use `Release: not-required` and a `Reason:` line on either
branch. Keep `VERSION` and `CHANGELOG.md` unchanged, including published notes.
Collect pending user-visible changes in the PR body's `## Pending release notes` section.
CI requires visible notes or an explicit `None`; HTML-comment placeholders do not count.
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

At preparation time, `release/1.x` does not exist. Its base must be selected from the
confirmed v1 runtime/release history; **do not create it from current v2 main**.
Once that base is approved, backport these files together before publishing v1:

- `.github/workflows/publish-github-release.yml`
- `.github/workflows/test.yml` (merge the branch trigger and standalone test entry;
  retain checks appropriate to that v1 checkout)
- `scripts/publish_github_release.py` and its `scripts/release_content.py` dependency
- `tests/unit/test_publish_github_release.py`,
  `tests/integration/check_publish_github_release.py`, and this procedure

Run the focused release tests and source validation on the actual v1 checkout.
The workflow on main alone does not install a workflow on an older release branch.
No v1 branch base, VREP backport, deployment, or release publication is performed by
this workflow-support PR.

MSM/provider workflows remain main-only. The optional main-based
`static-app-release.yml` completion bridge accepts stable Releases from both lines
and uses the same Serverless production target. It is disabled until separately
configured and activated; ordinary PR merges do not publish the app. See
[automatic_app_releases.md](automatic_app_releases.md) for monotonic version gates,
Rules approval boundaries, replay/recovery and first-1.x-publication prerequisites.
Release creation itself does not change matsu, credentials or production switches.
