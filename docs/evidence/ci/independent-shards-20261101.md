# Independent CI shards

## Purpose and boundary

Reduce the elapsed time of Local browser and Python checks without removing tests or checks.
Account Sync is a separate change; this PR alone does not establish a ten-minute workflow.
No paid/larger runner, repository permission, merge rule, release version or deployment changes.

## Hosted baseline

The latest completed main run when implementation began was
[41256028478](https://github.com/Yuto-24/AutoNavLog/actions/runs/41256028478)
on `aa8dd0a90b24d49bf22279706fdacea0285175bb` (2026-11-01):

- Workflow elapsed: 13m46s.
- Account Sync: 13m31s; intentionally unchanged in this PR.
- Python: 12m45s, including Docker build 79s and checks 681s; pytest itself 655.39s.
- Local browser: 10m37s, including dependency installation variability.

A separate [PR326 run](https://github.com/Yuto-24/AutoNavLog/actions/runs/41266357658)
had successful Python in about 9m26s and Local browser in about 13m33s.
Its failed Account Sync run did not execute migration, so its shorter duration is not a
successful full-suite measurement. These different runs demonstrate variability, not a paired
speedup estimate.

## Partitioning and verification

Local uses the existing regression command on one runner; auth, scroll, all browser/device
acceptance projects and TAF run on another. Every existing command and worker setting remains.

Python collects the whole suite on each runner, then partitions files deterministically by
recorded duration. Keeping whole files preserves fixture scopes and test order. New files are
assigned automatically. Every shard exports the complete and selected node IDs; the aggregate
requires equal complete collections and exact-once coverage of that collection. It also requires
both coverage databases and combines them into one report. Existing non-pytest checks run on
both shards, and original `pytest` / `local-browser` check names remain required-style aggregates.

To refresh timings, run the unsharded suite with `--junitxml=<path>` under the same environment,
run `python scripts/pytest_timing_weights.py <path> > scripts/pytest_file_durations.json`.
The converter resolves unittest class names to their existing source file and fails on unknown
sources rather than silently recording class names as files. Recheck
the deterministic assignments and complete test/coverage union. Timing weights do not determine
which tests are eligible. Hosted full-run success and elapsed time must be recorded separately
from local timings; setup/queue time and CDN variability remain outside the partition estimate.


## Timing weights

The committed per-file weights sum JUnit testcase durations from a 2026-11-01 local unsharded
run of main `8d1fc040b1128ba3c85a766462ea88f5e1c588ea`: 2,360 collected tests, Python 3.12.14,
pytest 8.4.2; testcase time sum 260.421s and elapsed 265.42s. This run used an existing environment
with installed package 2.1.1, so the version assertion failed; the runtime-contract assertion also
failed against the in-progress matrix workflow. The remaining 2,358 passed. These are timing
weights, not a successful baseline validation. Final checks use an isolated 2.1.2 installation
and the updated contract that still enforces Python 3.12 while allowing test-suite matrices.

The largest file, `tests/unit/test_rjfm_inbound_guidance.py`, measured 145.99s locally and about
398s between file-summary timestamps in the hosted baseline. File-level sharding preserves its
order but cannot subdivide that critical path. Unmeasured new files receive the median measured
file weight and remain included automatically. The weights guide balancing only; final hosted
runtime remains the acceptance evidence.
