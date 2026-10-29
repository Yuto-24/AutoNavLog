# One-way storage transition implementation validation

These are local implementation results, not a production PASS attestation. The
fixed 1.x application is `eeea630f831ecb213e6de46518d70133cf215f3c`
([PR #307](https://github.com/Yuto-24/AutoNavLog/pull/307)), based on `release/1.x`.
The old source is `a4de2a226dfa205b8be42fb62a710a1b06271ed8` (1.20.1).
The unfixed v1.23.0 `000ac6ecd85b18ae060cfb711070252c6c6d5af8` is excluded.
This fix does not create a stable Release. A release-preparation commit will have a
new identity and version: rerun applicable validation, then pin that exact SHA and
clean reviewed main tooling HEAD in the private transition record. Never copy a
PASS attestation to another candidate without checking its evidence.

## Old records and calculation freshness

The first 15 parser/hydration probes used different source trees with the **same
installed AutoNavLog metadata (1.22.2)**. Their parser behavior is valid within that
environment. They do not establish that a calculation stays current, or survives
explicit conflict cleanup, across a real application version update.

A replacement matrix installed the old distribution as 1.20.1 and the readers as
1.23.0 and 2.1.0 in separate temporary Python environments. It generated actual old
FTD and Forecast calculations, with Saved checkpoint/distinct draft, Latest, and
v1 envelope variants. All six variants passed on both new lines (12 cases):

| Operation | Result |
| --- | --- |
| Validate and hydrate old records | IDs, coordinates, distinct checkpoint/draft and normalized LastCalculation retained; original input and file bytes unchanged |
| Restore session | No automatic calculation; historical results retained |
| Present readiness | `calculationIsCurrent=false`; displayed outcome changes only `status` from READY_FOR_COPY to MANUAL_INPUT_REQUIRED |
| Isolate installed version in the probe | Restoring only the fingerprint service's old version makes all 12 calculations current again |
| Explicit `prepareSyncResolution` | Returns a copy with stale LastCalculation removed; input record unchanged |

The fingerprint already includes AutoNavLog version in
`src/autonavlog/application/project_fingerprints.py`; this update does not weaken
freshness rules. `restore_working` retains historical calculation state. Ordinary
account `receive` validates records without conflict cleanup; only explicit
`AccountProjectRepository.resolve` invokes `prepareSyncResolution`. Do not interpret
its stale-calculation cleanup as ordinary sync discarding the original snapshot.
Acceptance must distinguish retained historical results from permission to use them
as current NAV LOG output. No backward reader or rollback condition is added.

The matrix reuses current Python dependency libraries and fixture weather. It does
not reproduce archived production wheels or an old deployed Pyodide worker. The
browser test harness independently uses frozen old TypeScript repositories, real
IndexedDB and the actual Firebase SDK, but its small old-domain snapshot rejection
check is not a complete old Python application. Keep these evidence scopes separate.

Temporary evidence from this run is under
`/tmp/navmate-schema-study-7iq5g8hg/actual-version-evidence/`, with the runners in its
parent directory. Retain them in the authorized private release store before using
them as an operational attestation; temporary files alone are not durable publication
evidence. SHA-256 identities:

| File | SHA-256 |
| --- | --- |
| `version_probe.py` | `84fd78654518081acaf6660dffac1b0ee0f844793dc8f74f7bfdcb6e6b41f9dd` |
| `version_matrix.py` | `392667feaf9ac87d78ad71711967cfa6cbcafc5e4f7126fdcaef7a3be108b260` |
| `actual-version-evidence/results.json` | `f8136df69d8878c9168a5c27bf54492bd8ca7c70b5b6ba9746ff252c9b36f59c` |

## Other local checks

- Chromium and WebKit: 16 storage/SDK cases on each application line (12 initial
  cases plus four real Commit overlap cases). Old Commit RPCs are intercepted only
  after the SDK read and update-time precondition, then released after a new save.
  Stale cloud writes fail and different-profile sent recovery remains available.
- Current-DB inspection helpers: five auth, two weather persistence and one TAF
  browser cases passed after replacing fixed version-1 inspection opens.
- Actual Local/Pyodide UI: process restart, pinned Forecast provenance restore, and
  corruption/failed-write original preservation passed on 1.x (three cases).
- Main tooling: 358 related schema/static release/automation/version tests, including
  36 new schema transition tests; 95 existing adapter/application tests passed.
- 1.x: 91 existing adapter/application tests and focused persistence/version checks
  passed. Both lines passed the 69 CI source-contract checks, release validation,
  TypeScript checking and applicable build checks.
- GPT-6 Astra independently reviewed Sol's implementation. Its request for a real
  already-started SDK transaction was fixed and re-reviewed; no remaining findings.
- Isolated worktree Compose runtimes reported healthy at versions 1.23.0 and 2.1.0;
  data volumes were retained. This does not establish Static Web production serving.

Hosted CI and remote reviews are tracked on the PRs. Production public Legacy
configuration/redirect/write gates, live Google identity, actual Firebase Rules,
canonical serving, final Release identity and publication acceptance remain separate
operation-window checks in [the runbook](schema_transition.md). No production PASS
record, credential, permission, scheduler or publication change was made here.
