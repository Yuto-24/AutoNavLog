# Issue #235: inbound UMK completion, local partial implementation

Verified 2026-10-30 JST on `codex/issue-235-inbound-umk-matsu2`, based on
`e669b8b9`, in the dedicated `646e/AutoNavLog` worktree. No existing Issue #235
branch or PR was found in the initial local worktree / GitHub inspection.

## Cause and scope

The inbound policy previously required physical OMARU, UMK and a final VREP in
that order; it did not add missing UMK. Normalization now inserts one reference
UMK after coordinate-matched OMARU when returning to RJFM with a separate final
VREP. The existing arrival plan, VREP, operational-descent rule and physical
segment geometry remain authoritative. Explicit UMK nodes and their user/Point
names, coordinates and manual inputs are retained. Reapplication and restoration
preserve generated identities; deactivation removes generated UMK and fixed
altitude. Original direct-leg geometry overrides are restored only when the
original node adjacency is restored.

This addresses OMARU → ARITA → RJFM operation consistency. Direct OMARU → RJFM
completion and its single parent NAV LOG row remain deferred: Issue #235 has no
final decision on the descent endpoint altitude and ETE/FUEL. No general VREP
waiver, aircraft policy, UI copy/layout, VERSION or CHANGELOG change is included.
The reporter's actual KML/Project was unavailable; regression uses a fixed KML
fixture and the equivalent MAP route.

## Validation

- Related Python regression: **91 passed** across `test_rjfm_inbound_plan`,
  `test_rjfm_departure_plan`, `test_rjfm_route_name_slots`,
  `test_rjfm_inbound_calculation`, `test_rjfm_departure_calculation`,
  `test_issue_235_inbound_route`, and `test_map_route`.
- Existing Legacy HTTP regression: **2 passed, 1 deselected** in
  `test_rjfm_inbound_web.py -k 'not production_available_reference'`.
  The sandbox attempt stalled and was stopped (exit 130); the same two HTTP
  tests passed outside that sandbox. The expensive, unrelated production
  west-extension numeric solver test was deliberately excluded.
- Actual Pyodide **0.27.7**, Node 22, Python 3.12, Pydantic 2.10.5:
  **20 passed** using the final inbound unit and new KML/MAP integration tests.
  A temporary Node harness installed the ordinary wheel/data in the WASM
  filesystem and ran pytest; this validates the calculation runtime, not browser
  UI interaction. Node initialization/Buffer conversion issues in initial harness
  attempts were corrected before the passing run. Final application wheel
  SHA-256: `5d6874a12de913a85895bdab15fc0389f17b2f7cd91c3c8a91ee9dc54c9b34dd`.
- KML and MAP integration checks include physical TC/DIST, UMK EOC, VREP identity,
  save/restore fingerprint, stale calculation after editing and recalculation.
  The separate west-extension diagnostic is stubbed in these focused tests.
- Ruff and formatting checks passed for changed Python files; full
  `mypy src/autonavlog` passed for **77 source files**.
- `python scripts/validate_release.py`, `pytest tests/unit/test_version.py`
  (**2 passed**) and `npm --prefix web run typecheck` passed. The shared Python
  environment's old installed package metadata initially failed the version
  check. An isolated `/tmp` package install fixed that verification environment;
  shared environment and version source files were unchanged.

## Independent review

**GPT-6 Astra / high**, separate read-only context, reviewed the Sol / high
implementation. Initial review found one P2: replacing generated UMK with an
explicit UMK could restore the original OMARU → ARITA TC/DIST onto the wrong
segment. The fix records the original successor and adds a regression with
manual 190° / 20 NM input. Astra re-reviewed this and the generated-UMK-as-VREP
cleanup guard, independently exercised seven focused cases, and reported no
remaining findings. Astra made no edits or runtime changes.

## Local runtime and delivery boundary

Worktree setup created project `autonavlog-wt-f34e8230`, image
`autonavlog:wt-f34e8230`, port **33636** and volume
`autonavlog-wt-f34e8230_autonavlog-data`. The final source was rebuilt/recreated
with that same volume. Health is `ok`, version **2.1.0**, restart policy `no`;
worktree MSM prewarm remains disabled. Live HTTP import/confirm/update verified
one UMK, ARITA VREP preservation, 4,500 ft policy and stable node identities.
Final runtime image ID:
`sha256:01c8a5b08cba15290e24c84028bf7eaa53d96ceb1c64a1feb07f05258cf3b144`.

Push, PR creation, CodeRabbit PR review and hosted CI were not performed, as
requested. No merge, production publication, scheduler, permission or primary
runtime change occurred. Local Compose health and fixture/WASM results do not
establish Static Web production acceptance or physical-device/browser acceptance.

## 2026-10-31: parent/child presentation and clickable OMARU followup

Continued from `7107a36d` in the same branch/worktree, retaining its UMK
completion and explicit-input protection. The inbound OMARU-to-final-VREP span
now has one parent; physical source section IDs and all calculation zones remain.
Both exception parents explicitly display adopted child TC/MC. Inbound parent
TC/MC are blank rather than presenting one segment as the entire route.
Mixed cruise/operational-descent ETE sums the displayed child durations while
retaining the once-rounded operational-descent allocation. Intermediate physical
endpoint altitudes remain visible. No manual course override was introduced.

The draft map uses the existing bundled OMARU coordinate and the user-provided
label 小丸. It adds an ordinary MAP point without inventing AIP provenance or a
VREP snapshot. Explicit repeated clicks remain separate occurrences. Docker's
frontend stage now copies the imported existing reference JSON.

Final local validation:

- Related Python regression: **99 passed**, including both parent spans in one
  roundtrip, physical child courses, descent rules, displayed ETE sums, manual
  UMK protection, KML/MAP confirmation and working-state restoration/recalculation.
- Actual Pyodide 0.27.7: **20 passed** on the final ordinary wheel for inbound
  unit and KML/MAP integration tests. This is WASM runtime evidence, not browser
  Worker/UI or physical-device acceptance.
- Legacy HTTP regression: **2 passed, 1 deselected**, excluding the unrelated
  expensive production west-extension numeric solver case.
- Release validator, version tests (**2 passed**), web typecheck, changed Python
  Ruff and full mypy (**77 source files**) passed.
- Chromium **1 passed** at the default 1280×720 viewport: click 小丸, restore draft
  after reload, click the selected marker again, and confirm list occurrences.
  Page title and absence of Vite error overlay passed; screenshot at
  `/tmp/issue235-omaru-map.png` inspected. Two generic HTTP 404 console messages
  were observed; target click/restoration flow passed. No layout was changed;
  mobile/1100px workflow-position testing was not performed for this followup.
- Browser plugin initialization failed with `sandboxCwd is not a local file URI`;
  repository Playwright on existing host Chromium supplied the local click
  regression. The initial E2E attempt connected before the replacement runtime
  finished rebuilding and did not find the new marker; reruns after rebuild passed.
- The same isolated Compose runtime was rebuilt with its existing named volume,
  and `/healthz` returned `ok`, version 2.1.0. Primary runtime was untouched.

GPT-6 Astra independently reviewed the read-only followup. It found one P2:
returning the inbound grouping early skipped outbound grouping in a combined
roundtrip. Prefix composition fixed it; a dedicated regression and Astra's
lightweight reproduction confirmed both parents coexist with no missing or
repeated source sections. Final review reported no remaining findings.

One required clarification remains pending: outbound OMARU-first inputs currently
use a straight physical RJFM-to-OMARU route with virtual RCA. Distinct courses via
real UMK require changing geometry, DIST, weather and cruise ETE/fuel. The user
has been asked whether to use real UMK (recommended) or retain the existing
straight route. That conversion is not implemented or represented as completed.
The already-physical outbound UMK route has explicit child courses. The separate
direct OMARU-to-RJFM unresolved descent policy also remains excluded. Push, PR,
merge, production publication and permission changes remain unperformed.
