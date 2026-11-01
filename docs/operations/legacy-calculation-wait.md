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
