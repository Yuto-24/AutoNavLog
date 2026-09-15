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
- `ApplicationError` exposes `code`, `message`, and `details`, plus optional `committedState`
  when an operation fails after committing a draft. UI applies that canonical recovery
  without replacing raw drafts, while still showing the error. Domain codes and
  messages are preserved. KMZ selection candidates live in `details.candidates`.
  Only request-model validation is `VALIDATION_FAILED`, with neutral message and field issues
  (`location`, `message`, `type`). HTTP's body/path/query prefix and Python traceback/context
  are excluded. Connection/runtime failures use `APPLICATION_UNAVAILABLE`.
  HTTP status and retry policy are internal to Legacy. Local Python failures are
  serialized explicitly because exception transport would discard custom fields.
- Unexpected execution failures, including internal Pydantic validation, never expose
  exception messages as input errors. Both runtimes use `CALCULATION_JOB_FAILED` /
  `計算に失敗しました。` for calculation and `REQUEST_FAILED` / `処理に失敗しました。`
  for synchronous operations. Explicit application errors keep their code/message/details.
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
contract. Local persistence now uses the IndexedDB Local Project Repository from #124;
see [durable storage semantics](local_persistence.md). The Pyodide facade remains a
transient working runtime. New work disposes that runtime and clears tab recovery;
its previously persisted Latest follows the Repository's replacement/explicit-open rules.

`ApplicationSnapshot` aliases the current `WebState` solely as a **transitional
snapshot**. This is not a durable storage contract. The reload lifecycle below is shared by both adapters;
#124 owns Local persistence and #120 owns platform capabilities.
The composition root injects [Browser Platform capabilities](platform_capabilities.md).
File bytes and metadata converge on the existing Importer contract; Application receives
no filesystem paths or DOM File objects. Local and Legacy both support KML/KMZ.
The existing development-only mode label/default weather remain unchanged.
Local save/name controls are enabled by #124. There is no UI redesign, new default mode, or calculation rewrite.

## Verification

- `npm --prefix web run test:application`: no-browser contract/adapter tests, also in CI.
- `pytest tests/integration/test_local_calculation.py`: Python dispatch, errors,
  last-good/draft behavior and the existing FTD/FORECAST regressions.
- Legacy Browser suite: `npm --prefix web run test:e2e` (see existing runtime setup).
- Production Local Browser suite: `npm --prefix web run build:local`, serve dist-local,
  then `AUTONAVLOG_LOCAL_URL=http://127.0.0.1:4174 npm --prefix web run test:local`.
  Existing differential tests retain canonical result/Golden tolerances, API blocking,
  Python validation failure, Worker failure, and corrupt/failed asset coverage.

## Frontend Application Session / lifecycle (#119)

applicationSession.ts defines a versioned, ephemeral working snapshot. It contains
the Application's working Project and last-good result, parsed importer content (including
full coordinates needed for confirmation, not just the map preview), and raw UI drafts:
planning text, section altitude/phase edits, NAV LOG edits, route name/Check Point input,
VOR selection, pasted KML, and pending KMZ bytes/document choice. Validity of an input is
not a condition for retaining it. The canonical validated Project remains separate from
raw edits; existing autosave and update/update-and-recalculate semantics remain in use.

Browser recovery uses only the dedicated sessionStorage key
autonavlog.working-session.v1. It is a temporary copy, not the source of truth for saved
Projects or Last Calculation. The session codec itself introduces no durable repository or calculation core.
#124 supplies the separate IndexedDB repository described in local_persistence.md.
Storage denial/quota exhaustion is visible to the user; a failed write is not reported as
saved. Large imports/results remain subject to the browser's sessionStorage quota.

Only navigation classified by the browser as **reload** adopts a stored snapshot.
A fresh navigation clears only this key, including the sessionStorage copy browsers may
give an opener-created/duplicated tab. Tabs have separate UI snapshots and separate
Adapter working runtimes. Tab-close or browser-restart recovery is not guaranteed.
Browser history/focus/scroll and modal visibility are not session data. Pending KMZ and
Check Point inputs have explicit continuation controls while their editors start closed.

On startup, bootstrap(recovery) hydrates the Application without replaying operations:
Legacy creates a new private working session, Local hydrates its new transient Python
runtime. Existing domain/importer models validate the recovery and existing readiness
evaluation computes the presentation. Snapshot hydration performs no calculation, autosave,
last-good write, marker update, or index repair. The existing Legacy saved-Project listing
may still repair its own index or retry pending cleanup while assembling the presentation.
Legacy's working handle stays inside its Adapter and travels in its own request header;
Cookie-based endpoints remain for Legacy compatibility, but do not select the UI's work.
Owner checks remain Legacy authorization behavior, not a new frontend identity contract.
A 401 retry creates a private session using the Adapter's latest working copy.

Frontend Legacy sessions keep edits, autosave updates, and calculated results in their
own working runtime. They do not implicitly write the shared repository, its autosave,
last-good file, or owner marker. An abandoned calculation may finish on the server, but
can only update its abandoned runtime. No cancellation protocol is required for this
isolation (#160 still owns cancellation). Two tabs may independently edit/calculate the
same Project without either tab's unsaved work becoming the other's saved input.

Frontend drafts are therefore not published as the old owner-wide Latest entry in the
saved-Project list. Reload uses the tab snapshot; explicit load lists shared saved data.
Only explicit Legacy save commits this work to the existing repository. Its existing
revision check runs before canonical autosave or last-good writes; a stale save reports
PROJECT_REVISION_CONFLICT and leaves the tab's working copy intact. Checkpoint and
associated snapshot writes are serialized across explicit saves. The recovery also
retains the last successful calculation's original Project and forecast metadata, so
saving a subsequently edited draft does not relabel its old result as a new calculation.
The old Cookie-session API retains its compatibility autosave behavior; Frontend
Application sessions do not use that path. This is Legacy adapter behavior, not #124
durable Local storage.


Recovery priority is: **same-tab ephemeral session, then #124 durable data, then new work**.
Absent recovery starts blank; #124 durable Local Projects are opened only by explicit selection.
Legacy saved Projects remain available by explicit load; a fresh tab does not implicitly
adopt another tab's owner-wide Latest. Existing server repository storage is unchanged.

Restored execution is always idle: no busy/progress, dialogs, notices, errors, focus, or
scroll state are saved. NAV LOG and destination-pattern automatic recalculation timers
are not armed by hydration. Reload during calculation abandons that execution and keeps
draft plus last-good; it never starts or resends a calculation. The next user edit or
explicit calculation follows the normal workflow. New page execution and a new Legacy
working handle prevent old responses/jobs from writing into restored UI state. Existing
input generations and autosave guards still reject superseded edits; action results do
not canonicalize input edited while they were in flight.

Explicit new work drains/discards pending autosave, clears only this tab's recovery,
then resets the Adapter and reloads. If storage refuses removal, it reports the failure
before disposing the working runtime; a failed Adapter reset restores the recovery copy. It does not delete saved Projects, Last Calculation, another
tab, or #124 data. Malformed/version-incompatible UI snapshots or invalid Application
recovery start a safe blank session with a visible recovery-failure notice. Connection
failures keep the retry screen and the recovery data, rather than silently losing work.

Verification: shared e2e/session.spec.ts runs against Legacy and production Local
(npm --prefix web run test:local -- session.spec.ts).
Local blocks /api/**; KMZ document selection and reload continuation run in both modes.
Adapter/codec tests run in test:application; Python integration verifies parsed import
continuation, last-good hydration without calculation, separate Legacy handles and no
persistence writes during restore.
