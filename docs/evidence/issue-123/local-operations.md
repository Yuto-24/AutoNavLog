# Issue #123 implementation and activation gates

## Scope

The implementation preserves #121's portable production artifact/manual rollback and
#145's independent Worker configuration/deploy/rollback. The existing GitHub Release
workflow and Application runtime are unchanged. Release: not-required (CI/operations/docs).

## Local evidence

- Operational tests cover unknown/non-finite/stale/exhausted quota, wrong account/period,
  paid/billing state, retained payload corruption/traversal, expired catalog restoration,
  freshness warnings, canonical source mismatch after rollback, and retained-only producer
  output rejection. Optional-service quota exhaustion does not stop weather publication.
- Production artifact tests cover inventory/hash/runtime/dirty/expiry/budget rejection.
- Existing version, producer retention, runtime/reference/performance/schema validations
  and Web typecheck are exercised. GitHub workflow syntax is checked with actionlint 1.7.7.
- A real read-only restore from `navmate.pages.dev` recovered four retained assets; a local
  producer run on 2026-09-24 prepared two Runs for the next 24 hours, including
  `20260924090000`. No production catalog was changed.
- Worktree Compose project `autonavlog-wt-2f23dd26`, port 28033, was rebuilt/started without
  deleting a volume. `/healthz` returned `{"status":"ok","version":"1.20.1"}`.

Independent review identified retained-only catalog renewal, unsafe default TAF allowlist
publication, and quota account/window misattribution. The publication wrapper now rejects
no-new-Run results, TAF remains on #145's manual production contract, and quota evidence
is checked against configured account/project/owner and current provider windows.

## Separate production gates (not executed)

No environments/secrets/variables, provider configuration, scheduler settings, production
uploads, rollback, DNS, Firebase Rules or billing settings were changed. The new schedules
are opt-in and remain inactive until explicitly authorized/configured after merge.

Required operator evidence remains: current target-bound Free/Spark plan and quota audit,
protected recurring-publishing authorization, least-privilege credential installation,
live notification delivery, two scheduled renewals spanning the old catalog expiry,
canonical inventory/cache/real browser checks and a paused-scheduler rollback with Local
storage retention. Quota polling evaluates supplied operator/collector evidence; this PR
does not claim that unavailable provider counters were automatically measured.

#123 and parent #116 must remain open/incomplete for these production gates. Local build,
CI, review, merge and production acceptance are separate states. See the task PR for the
final commit, clean artifact/browser results and hosted CI conclusions.
