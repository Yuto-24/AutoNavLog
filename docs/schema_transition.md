# One-way Project schema publication

This is a manual, separately authorized transition, not an automatic publication
exception. Ordinary and automated releases still require identical schema/index
blobs and Rules bytes. The only supported schema difference is the optional
`RouteNode.reference_snapshot` plus its two new definitions; all prior fields,
types and required constraints must remain identical. Index changes stay blocked.

## Two distinct migrations

`https://navlog.yuto24.com` to `https://navmate.yuto24.com` is the existing Legacy
account activation. Reuse its owner lock, `NAVMATE_ACTIVE`, server-side 423 rejection,
303 navigation redirect and polling redirect. Confirm actual public configuration
and behavior during authorized acceptance; repository defaults and local WSL
settings do not establish that it is active on the public Legacy host.

The same-origin NavMate 1.20.1 `a4de2a226dfa205b8be42fb62a710a1b06271ed8` to the
selected fixed 1.x application is a separate update. Legacy redirect does not retire
an already open NavMate tab. Older code can tombstone an unreadable, same-device
account Latest while opening another Saved Project; it uses the current sync base,
so CAS alone cannot stop that automatic cleanup. The fixed application upgrades
IndexedDB access to version 2 without rewriting record bodies. Old fixed-version-1
clients then fail storage/outbox operations. In-flight old cloud mutations still
need the existing remote CAS; already completed writes remain valid predecessors.

The original v1.23.0 `000ac6ecd85b18ae060cfb711070252c6c6d5af8` does **not** include
this fence and must not be accepted as the fixed candidate. Do not move its tag or
label old test results as evidence for a new commit. Finalize the actual fixed
application SHA after review, and pin it in every record and command. An ordinary
fix PR does not create a new Release: follow the separate release-preparation
contract before selecting a new stable Release. main also opens DB version 2, so
the subsequent 1.x to 2.x forward update can retain the same storage.

## Required evidence

Copy [the deliberately failing example](schema_transition.example.json) into a
private release store outside the checkout. It is not a production PASS record.
Fill the exact canonical/current app SHA, fixed candidate SHA, clean trusted tooling
SHA, verified Firebase project, canonical origin and public-configuration digest.
The digest is `app_release.fingerprint(app_release.public_configuration(variables))`
over the complete existing GitHub configuration inventory; never log credentials.
Use `git rev-parse <SHA>:data/schemas/project.schema.json` for `before_blob` and
`after_blob`, and `<candidate>:web/src/localProjectRepository.ts` for `writer_blob`.
A schema-identical ordinary release needs no schema transition record.

Each of the three checks needs `PASS`, a nonblank independent reviewer and summary,
and a retained evidence file with its exact SHA-256. The record binds all checks to
its current/candidate/tooling/configuration identities. Hashes bind evidence; they
do not prove compatibility. Cover:

- `old_to_new`: actual old Saved checkpoint/distinct draft, Latest and LastCalculation
  read/hydrate into new code, retaining IDs, coordinates, results and pinned Forecast
  provenance. Apply existing calculation freshness policy: retaining a historical
  calculation does not make it current in a different application version. Use the
  actual-version results and limitations in [implementation validation](schema_transition_validation.md),
  rather than treating the earlier shared-metadata probes as version-transition proof.
- `old_writer_fenced`: real old/new browser tabs, unreadable new Latest cleanup,
  pending/sent mutations, a delayed old SDK transaction, DB upgrade overlap and blocked
  upgrade failure. Preserve Project bodies, old session edits and outbox recovery;
  no old tab may tombstone new work. Verify remote CAS on a different old device.
- `storage_sync_roundtrip`: real IndexedDB reload/process restart and real SDK plus
  emulator Rules for Saved/Latest/LastCalculation and a fresh device. Record synthetic
  identity and emulator transport separately from live Google/production acceptance.

The complete candidate must contain the version-1 to version-2 writer fence, not
just declare it in a JSON record. The tool checks its source identity and narrow
prerequisites, while independent source review and browser/SDK evidence establish
behavior. Both application lines must retain compatible DB access. This transition
is one way: new-to-old reading and an old-app rollback are not acceptance conditions.
Recover with a compatible forward fix, preserving Project data and pending work.
Do not clear IndexedDB, strip snapshots, restore an entire older backup over newer
writes, or roll Rules backward automatically.

## Prepare and publish

Use current reviewed main tooling, not the older scripts inside the 1.x candidate.
Refresh canonical/approved alignment, Pages identity, exact public configuration and
live Rules with existing authorized read access. Missing access or UNKNOWN is a
blocker, not permission to create credentials or change protections. If Rules differ,
reuse the separately reviewed `--rules-transition` record and external Rules procedure.
This tool only reads Rules. Do not combine Rules compatibility attestations with
schema read/write evidence or infer either from the other's hashes.

Before prepare, obtain the separately authorized operation window and pause recurring
publication as required by the existing Rules transition runbook. No scheduler/config
mutation is part of preparing this implementation. In that window, for a new private
`STORE` directory and explicit full `CURRENT`/`CANDIDATE` values:

```sh
scripts/static_ops/app_release.sh prepare \
  --sha "$CANDIDATE" --current-sha "$CURRENT" --store "$STORE" \
  --schema-transition "$SCHEMA_RECORD" --rules-transition "$RULES_RECORD"
```

Omit `--rules-transition` when Rules are identical; omit `--schema-transition` when
schema is identical. Review the exact plan, schema/Rules receipts and isolated build
results. The existing 30-minute plan expiry applies. After **separate publication
approval covering that plan's digest**, use the same arguments and records:

```sh
scripts/static_ops/app_release.sh publish \
  --sha "$CANDIDATE" --current-sha "$CURRENT" --store "$STORE" \
  --schema-transition "$SCHEMA_RECORD" --rules-transition "$RULES_RECORD" \
  --reviewed-plan-sha256 "$REVIEWED_PLAN_SHA256"
```

Record bytes, paths, evidence, source/configuration or tooling changes invalidate
the plan; prepare and review again. The existing pause/idle checks, fresh rebuild,
sealed upload, canonical asset/serving verification, approved-SHA handoff and failure
journal remain in force. No guard deletion or independent Direct Upload bypass is
permitted. A mixed/uncertain state stops; no automatic rollback/resume is added.

Once the manually reviewed transition has passed actual production acceptance,
subsequent releases with identical contracts use the unchanged ordinary/automatic
path. Activation, credentials/WIF/permissions, Rules deployment, environment policy,
first publication and automation enabling remain separately authorized operations.
