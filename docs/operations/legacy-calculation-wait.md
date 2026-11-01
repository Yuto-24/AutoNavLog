# Legacy calculation progress and pinned runtime

Legacy weather preparation can download additional GRIB files when the flight crosses
an interpolation or file boundary. The 48-hour prewarmer can select a different run
than an individual flight. A warm prewarmer therefore does not guarantee a warm
calculation. Do not extend HTTP timeouts or delete production caches as a substitute
for responsive job polling.

## Request and state boundaries

- Job reads authenticate the signed Access identity and session, then check committed
  migration state without taking the calculation lock. Expired migration leases are
  represented as LINKED on reads; lease repair is deferred to an owning write.
- Mutations acquire owner then session locks without waiting and return
  `409 OPERATION_IN_PROGRESS` when busy. Calculation completion and migration
  snapshots remain serialized. The existing last-good result is retained on failure.
- `GET /api/calculation-jobs/active` returns a snapshot only for the requesting owner
  and session, or 204. It is declared before the variable job-id route.
- A tab checks for an existing job before saving calculation inputs. Unacknowledged
  start POSTs are not automatically replayed. Temporary polling transport failures
  retry GET with the original session and job. Each tracking attempt is limited to
  ten minutes; expiration does not cancel the server job. Reconnection across page
  reload or server restart is not promised.
- Busy autosaves retain the raw UI draft and report that it is unsaved. Recovered
  results do not replace edited inputs, and those inputs remain stale until calculated.
- `calculation_stage` timing events contain only a fixed stage, start/end, outcome,
  and seconds. They must not include Project bodies, routes, owner or session tokens.

## Production startup and restoration

A plain `docker compose up --force-recreate` in an older checkout can recreate the
service using its old mutable `autonavlog:local` image. It does not mean “restart the
currently deployed release.” For a restart only, restart the existing named service.
For recreation, use the deployment's saved startup script and frozen Compose file:

```sh
/path/to/deployment/start-current.sh
```

That script must identify the exact image ID and use `--no-build --pull never`, the
existing production project, loopback port, actual authentication settings and retained
named volume. Keep the file containing environment values private and out of Git.
Record package version, image ID and source commit together; a health version alone
cannot distinguish a patched image from its release base.

Before a release restoration, establish that calculations have finished, stop only the
intended service cleanly, and archive the entire stopped volume with SHA-256 and a
manifest. Never overwrite newer writes with an older backup. Never downgrade a
v1.23.0 volume to v1.21.0: RouteNode reference snapshots are not supported by that image.
Retain the previous compatible image and the current data separately for recovery.

Validate both the PR source and any v1.23.0 backport. Use isolated storage and synthetic
identity for authenticated calculation acceptance; do not weaken production Access or
reuse an actual user's session. Verify the production image/health/mount independently.

## UI wording verification (PR #332)

The nine affected or reused UI strings were checked through the existing WSL agy
route with `--model gemini-3.8-flash-medium` in plan/sandbox/print mode on
2026-11-01, 11:32:17.719982–11:32:54.259251 UTC. The parent task supplied the
verified result: exit 0, `SUCCESS`, empty stderr; conversation
`c162416d-e5a0-4fd9-9248-22e186dee86e`. The response did not separately expose a
backend model identifier.

The approved changes distinguish operation contention from calculation-only locks,
tracking timeout from cancellation, missing response data from absent calculations,
and result retrieval from a new calculation. The other five strings remain unchanged.
No lock, retry, session, migration, server-error precedence or draft-retention behavior
is changed by this wording follow-up.

Evidence SHA-256 values supplied by the parent:

- Prompt: `237bea036f8601d1c0af8a6e8d9b96ecb0c33a4ef3bf9d7964f2e0b9e4fc60eb`
- Raw output: `db1f59741a8942f8060e8d6cfa2f76d72206e9b7b0d9b32b653ec166a555d325`
- Final JSON: `c6e1b894576f731dfb493050f8403c19012562c9b9a6a5a6d44d48375ec7f6a7`

The same Gemini route additionally checked response-body transport failures on
2026-11-01, 11:39:21.835653–11:39:48.975466 UTC (exit 0, `SUCCESS`, no separately
reported backend model ID). It selected
`サーバーとの通信に失敗しました。再試行してください。` for transport failures and
retained `処理結果を読み取れませんでした。` for malformed JSON.
Prompt SHA-256: `dcc92fd11889d3ae69685ecc2d3137693c82a8e6e58d8a9b9e446096910f4ad5`;
raw output: `ca4658495a8fa2fa5daad38a8b7da0997776f5c2a5d2fafc63635fa14ac464de`.

Pre-merge review follow-ups also cover body-read disconnects/timeouts (retry GET or
look up an unacknowledged POST, never replay it), deterministic closure of read-only
SQLite connections, preservation of the three locally editable VREP arrival fields,
and discarding a cached job after a successful Project-context switch. Busy/failed
switches and same-Project saves/edits retain the job for explicit reconnection.
