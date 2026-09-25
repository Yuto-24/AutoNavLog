# Forecast model selection (#161)

The application owns the priority `MSM → GSM` and explicit refresh intent.
The weather library owns model specifications, discovery, acquisition, cache,
decode, interpolation and post-prepare altitude coverage. A successful discovery
with missing source files or an unavailable static payload is an acquisition
failure, not evidence that a model is out of coverage.

`application/forecast_selection.py` evaluates candidates in adapter-provided
newest-first order. It advances only on an affirmative coverage exception with
nonempty reason codes. All other exceptions stop selection. `REQUIRES_HGT` must
proceed to prepare; only `ALTITUDE_OUTSIDE_HGT_RANGE` from the public post-prepare
API permits an altitude fallback. `SOURCE_VALUE_UNAVAILABLE` is a processing
failure. Adapters must check the complete request before accepting a candidate.
Native adapters expand the library's default data crop to include requested route
and weather-sample coordinates. That crop is not a model-domain definition; the
library still decides specification coverage, grid cropping and interpolation
halos. Local delivery boundaries remain explicit: a payload missing requested
points is a processing/delivery failure, not a model exclusion.

A fixed MSM Run remains selected until explicit refresh if another MSM Run can
satisfy the request. If no MSM candidate satisfies it, selection immediately
tries GSM. A fixed GSM Run never moves back to MSM without explicit refresh.
An update-discovery/processing failure blocks the current attempt, retaining
the last successful calculation; it does not silently assume no update exists.

The dedicated `forecast_selection_fingerprint` includes flight timing, airports,
route, altitude/phase/manual inputs, TGL, descent rate and relevant planning
state. It excludes the selected Forecast and save/revision bookkeeping. Refresh
intent must only be consumed against the same fingerprint, and must be discarded
after intervening input changes. It is distinct from the calculation fingerprint,
which also identifies the model and Run actually used.

Project schema 5 adds `selected_forecast_model` independently of the existing
Run ID. Schema 4 Run-only records migrate to MSM; the FTD fixed-weather identifier
does not represent a forecast model. CalculationOutcome and Last Calculation
carry model identity as well. Migration uses the existing Local/Legacy validation
and transaction boundaries; it must not replace a historical calculation or
delete an invalid record.

## Integration prerequisite and current status

Legacy selection, session-scoped dispatch, two-click refresh, final-requirement
restart, persistence and provenance presentation are implemented with focused
integration tests. Local GSM integration and final delivery checks remain in progress
pending the upstream public API merge. Existing AutoNavLog changes are preserved.

Inspection on 2026-09-25 found that the vendored `jma-gpv-weather 0.5.0` and the
upstream GSM client expose desktop prepare only. Unlike MSM, GSM has no public
portable-data codec or prepared-data injection, and its discovery has no acquired
listing injection. Its prepare path performs filesystem acquisition/native
decode. The existing Local adapter cannot call that path in Pyodide.

The upstream library therefore needs the GSM equivalents of the public MSM
portable boundary before the Local path can be completed without duplicating
meteorological logic or creating an AutoNavLog-specific weather format. The
existing static feed architecture and its source-CORS evidence are documented
in [Local Weather](local_weather.md). Raw GRIB processing must remain upstream;
adding a server-calculation fallback would violate Local-first semantics.

Sources: [AutoNavLog #161](https://github.com/Yuto-24/AutoNavLog/issues/161),
[library GSM support #13](https://github.com/Yuto-24/jma-gpv-weather/issues/13),
[MSM browser boundary #16](https://github.com/Yuto-24/jma-gpv-weather/issues/16),
[upstream GSM client](https://github.com/Yuto-24/jma-gpv-weather/blob/main/src/jma_gpv_weather/gsm/client.py).


## Acceptance coverage

| Contract | Regression evidence |
| --- | --- |
| Newest actually covered MSM; older MSM before GSM; both outside; fail closed on acquisition/processing | `test_forecast_selection.py`, `test_forecast_candidates.py` |
| Public post-prepare HGT vs unavailable source values; no weather implementation in core | Adapter contract tests and upstream portable runtime tests |
| Fixed model/Run, two-click update, input changes invalidate intent | `test_forecast_calculation.py`, `test_project_fingerprints.py`, Local replay integration |
| Final requirements restart the entire calculation or wait for explicit Run update | `test_forecast_calculation.py` |
| Model/Run migration and persistence; last-good survives failure/wait | `test_local_persistence.py`, `test_storage_import.py`, Local/Legacy integration |
| Real portable MSM regression and synthetic GSM canonical parity | `test_local_forecast.py`, `local-weather.spec.ts` |
| Model identity, informational reason, workflow order at 1100/1440px | `web.spec.ts`, `local-weather.spec.ts` |

The Local transport exposes a bounded acquisition suspension at the Worker
boundary. Python replays the same user action after each requested catalog or
Run; suspension is not a processing exception or a second user click. No partial
calculation is persisted. An earlier action's arrays are cleared before the next
one; warm acquisition remains subject to the ordinary disposable browser cache.

The source catalog may advertise more Runs than the static publisher has built.
Do not filter discovery to published assets: an unevaluated older Run can still
satisfy the requirement. Missing delivery stops selection; publishing enough
candidate data is an operator responsibility, documented in Local Weather.
