# MSM artifact ownership handoff — 2026-10-27

## Failure and scope

[Static production run 40427467953](https://github.com/Yuto-24/AutoNavLog/actions/runs/40427467953)
detected Run `20261026210000`. Its build job completed feed generation, static artifact
checks and all 10 Production Browser E2E cases. At 00:43:35 UTC the host-side trusted
`freshness.py` failed creating `app/web/dist-static/weather/msm/freshness.json` with
`PermissionError: [Errno 13]`. Upload and canonical verification were skipped.
This is a failed publication, not successful freshness migration.

The build image has no `USER` override and the workflow did not pass `--user`, so
Docker runs the build as UID/GID `0:0`. The app is a writable bind mount. Default
0755 directories and 0644 files permit the nonroot Actions host to read outputs,
but not create the hint in the root-owned MSM directory or update root-owned
`release.json`. The failed run did not log `id`/`stat`; its exact runner numeric
UID/GID and historical mode bits cannot be recovered from that log. The regression
records those values explicitly instead of hardcoding a presumed GitHub runner UID.

Existing #288/#290 are merged into the checked main `8d31d06e`. No duplicate open
freshness-permissions PR was found. Worker activation, broader Rules migration and
unrelated app work remain separate.

## Minimal change and regression

Pass only the host's numeric UID/GID to the existing credential-free build. After
successful build/E2E, use non-recursive `chown --no-dereference` on `release.json`,
`weather/msm` and, when present, `weather/msm/freshness.json`. Do not change mode
bits or ownership of payloads, other directories, checkout sources or dependency
outputs. No publication credential, tooling mount, Docker socket or runner command
file is added to the candidate build.

`tests/integration/check_static_artifact_handoff.py` uses the actual build image
and the actual workflow handoff tail. Root Docker creates synthetic valid-inventory
outputs at 0755/0644; the nonroot host must fail before handoff and succeed after it,
for both absent and existing hints. The test asserts the exact changed owners,
unchanged modes/bytes and payloads, full inventory equality, fixed application
identity and deterministic repeat writes (no forecast lifetime renewal). It prints
its real runner UID/GID. Local execution passed as UID/GID `1000:1000`, with Docker
outputs `0:0`. This is a permissions fixture, not real upstream/NPZ acceptance.

`test_static_publication_gate.py` additionally verifies that the independent gate
accepts the extended inventory and rejects unrecorded/missing files, incorrect
hashes/sizes and modified hint bytes. The PR CI runs the Docker/nonroot test in the
existing `static-tooling-install` job, without dispatching publication workflows.

Approved application selection, six-hour catalog lifetime, complete new Run
preflight, producer `--expected-run`, immediate pre-upload Run verification,
independent candidate checks and isolated static upload are unchanged. VERSION
is unchanged (`Release: not-required`). No merge, publication rerun, Worker
placement or Cron restart is performed by this fix.
