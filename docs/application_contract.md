# Frontend Application boundary (#118)

`web/src/application.ts` defines `AutoNavLogApplication`, operation inputs, progress,
and `ApplicationError`. `main.tsx` composes the chosen implementation and supplies it
as an App prop. UI code only calls operations; it does not construct endpoint paths,
choose HTTP methods, inspect session status, or invoke Worker RPC.

- `LegacyApplication` owns HTTP serialization, Cookie credentials, session bootstrap,
  a single retry after 401, and calculation job polling. The same mutation payload is
  retried once after session recreation, preserving the existing Legacy behavior.
- `LocalApplication` uses `LocalClient` and the serialized Comlink Worker queue.
  Python `LocalApplication` dispatches operation names to the existing facade.
  FTD and fixed-fixture FORECAST still use the same Python CalculationService.
  No failure switches to the Legacy implementation.
- `ApplicationError` exposes `code`, `message`, and `details`. Domain codes and
  messages are preserved. KMZ selection candidates live in `details.candidates`.
  Request validation is `VALIDATION_FAILED`, with neutral message and field issues
  (`location`, `message`, `type`). HTTP's body/path/query prefix and Python traceback/context
  are excluded. Connection/runtime failures use `APPLICATION_UNAVAILABLE`.
  HTTP status and retry policy are internal to Legacy. Local Python failures are
  serialized explicitly because exception transport would discard custom fields.
- Calculation progress is a callback with percent/message. Legacy forwards existing
  job progress; Local reports start and completion only, without invented intermediate
  percentages. Failures reject and never emit successful completion. Finer progress
  UI (#159) and cancellation (#160) are not implemented here.

## Preserved operation semantics

`updateProject` commits a validated draft. `updateAndRecalculate` remains one facade
operation under the existing lock: persist the validated draft, then calculate.
A calculation exception retains that draft and the previous last-good outcome; it
**does not roll back the draft**. UI debounce, stale-result generation guards, draft
flush before save/load/calculation, and explicit reset/reload behavior stay in place.
Renaming, checkpoints, and acknowledgements keep their existing facade behavior.

`saveProject`, `loadProject`, `deleteProject`, and `newWork` belong to the same
contract. Local save/load/delete reject with `LOCAL_PERSISTENCE_UNAVAILABLE` and
`details.issue = 124`; they never pretend to save or invoke Legacy. Existing Local
save controls remain disabled and the saved list stays empty. The reused repository
on Pyodide MEMFS remains transient, with no IndexedDB, OPFS, or persistent mount.
New work disposes the transient runtime; the existing UI reload completes the flow.

`ApplicationSnapshot` aliases the current `WebState` solely as a **transitional
snapshot**. This is not the permanent session/store contract. #119 owns session and
lifecycle redesign; #124 owns Local persistence; #120 owns platform capabilities.
File/base64 conversion remains a small existing helper, without a capability framework.
The existing development-only mode label/default weather and persistence affordances
remain unchanged. There is no UI redesign, new default mode, or calculation rewrite.

## Verification

- `npm --prefix web run test:application`: no-browser contract/adapter tests, also in CI.
- `pytest tests/integration/test_local_calculation.py`: Python dispatch, errors,
  last-good/draft behavior and the existing FTD/FORECAST regressions.
- Legacy Browser suite: `npm --prefix web run test:e2e` (see existing runtime setup).
- Production Local Browser suite: `npm --prefix web run build:local`, serve dist-local,
  then `AUTONAVLOG_LOCAL_URL=http://127.0.0.1:4174 npm --prefix web run test:local`.
  Existing differential tests retain canonical result/Golden tolerances, API blocking,
  Python validation failure, Worker failure, and corrupt/failed asset coverage.
