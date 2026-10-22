# MSM Forecast refresh (#277)

The existing calculate action keeps a saved, compatible MSM Run on its first
click and reports the existing `FORECAST_UPDATE_AVAILABLE` warning. A second
calculation with unchanged forecast inputs explicitly resolves the newest
compatible Run. This is shared by the Legacy facade and Local/Pyodide, without
a new button, model field, schema version, or GSM selection policy.

## Intent and persistence

`forecast_selection_fingerprint` binds refresh intent to the working Project,
flight timing, airports, route, section altitude/phase/manual inputs, TGL,
descent rate, aircraft settings, and relevant planning/reference state. It
excludes the selected Run, save/revision bookkeeping, and acknowledgements.
The calculation fingerprint remains separate and includes the actual Run.

Intent lives only in the working application session. Every presented input
mutation invalidates mismatched intent, including an edit away and back.
Calculation also checks its normalized inputs before consuming intent.
FTD, opening/restoring a Project, and recovery never inherit pending intent.
A restored first-click warning therefore requires a new first calculation
before a second click can refresh. Explicit Save keeps pending intent only for
the same working Project and unchanged forecast fingerprint; its internal
`adoptSavedProject` operation is distinct from reopen/reload bootstrap.

The calculation service defaults to pinned selection; only an explicit
`refresh_forecast=True` uses the provider's existing compatible Run resolver.
It does not implement model specifications, discovery, or coverage itself.
Local acquisition uses the same intent before downloading the portable payload,
so the second click acquires the new Run rather than preparing the old payload
and then trying to calculate with another Run.

Successful calculation uses the existing readiness and persistence boundaries
to synchronize Project, CalculationOutcome, Last Calculation and forecast
metadata. Coverage, acquisition and processing failures remain fail-closed:
they do not replace the last successful snapshot. No failure selects FTD or a
server-calculation fallback. Existing DATE/ETD pin invalidation is unchanged.

## Regression coverage

- `test_project_fingerprints.py`: relevant input changes and excluded bookkeeping
- `test_local_calculation.py`: first/second click, matching snapshot provenance,
  restored first-click warning, input edits and edit-back, failure continuity,
  FTD, and production portable-MSM acquisition before calculation
- Existing CalculationService tests retain default pinned-run semantics
- `local-weather.spec.ts`: real portable MSM in the browser, first-click pin,
  second-click refresh, IndexedDB Last Calculation and reload

The GSM/model-persistence work in #161 / PR #217 remains separate. Its shared
two-click design informed this MSM-only implementation; future integration
should reuse this state transition instead of layering another click counter.
