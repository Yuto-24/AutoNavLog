# Rounding-mode preparation (#234)

Status: implementation notes and open product decisions, not a completed mode
specification. The current change retains weather evidence for a future comparison
operation; it does not add the button or change calculation/display rounding.

## Confirmed request and existing behavior

The requested interaction starts after NAV LOG is displayed. An explicit action
recalculates with rounded numbers, presents a calculation modal, and aims to show
the result within about ten seconds. The comparison should use the same weather
conditions as the original result rather than acquiring fresh weather.

The confirmed numerical requirements are:

- Wind direction in ten-degree increments, with those effective directions used
  in the MH/GS calculation itself.
- The VOR/DME column's distance display in 0.5 NM increments.
- TOAT display in whole degrees Celsius.

These do not specify rounding MH to ten degrees, GS to ten knots, wind speed to
ten knots, or temperature/distance inputs used by the calculation. Do not infer
those additional transformations.

`calculation_rules.md` remains the existing calculation authority. #95 established
the distinction between internal precision and coordinated display values:
displayed MH is derived from displayed MC and WCA, each at one degree; GS/CAS/TAS
are displayed at one knot, DIST/ETE at 0.5 NM/min, and fuel at 0.1 gal. Phase totals,
REM, and the fixed physical parent-leg DIST policy must retain their invariants.
The current NAV LOG temperature formatter actually uses 0.1 Celsius degrees.
The frontend VOR distance formatter currently uses 0.1 NM.

The shared decimal rounding helper uses half-up (negative ties away from zero).
The existing direction formatter uses modulo 360 internally and displays north
as `360`. Any future rounding-mode boundary policy should be specified explicitly
against these conventions, including 5/355 degrees, negative temperatures, and
0.25/0.75 NM. This preparation does not create a different boundary policy.

VOR distance is the horizontal WGS84 station-to-point distance. RJFM inbound
guidance has a separate slant-DME, outward-ceiling policy that affects route
geometry. A display request for VOR/DME does not authorize changing that solver.

## Why saved results and Weather caches were insufficient

Legacy and Local already retain the calculation-time Project, outcome, destination
TAF, Forecast Run metadata, and calculation fingerprint. Local additionally caches
catalog/prepared MSM payloads. The catalog expires after five minutes; prepared
payloads expire after seven days and are subject to quota/size eviction. Decoded
provider state is temporary worker memory. These caches are not durable Project
weather evidence.

Outcome sections contain only the used zones. Weather acquisition also considers
CLIMB/CRUISE/DESCENT samples that may be unused in the original result. Rounded wind
can move RCA/EOC boundaries and make an unused sample necessary. Values reconstructed
from used sections or displayed cells are therefore incomplete. Selecting the same
Run also does not fix sampled values when representative times change.

The new optional `forecast_metadata.weather_snapshot` retains the complete final
set of normalized request/result pairs, including unused candidate phases,
departure surface temperature, and the exact final-arrival destination surface
sample. It retains raw provider values before manual overrides, full precision,
availability, warnings, original sampling times/locations, and provenance. Manual
overrides remain in the associated calculation-time Project. FORECAST and FTD use
the same retention path.

Schema version, sampling policy, Weather mode, Forecast Run, calculation
fingerprint, and a full-precision SHA-256 checksum bind the evidence. Both Legacy
and Local recovery validate it. Historical results without the optional key
remain displayable. A present but invalid snapshot fails validation; no missing
values are synthesized from caches. The checksum detects accidental inconsistency,
not an authenticated source or a trusted calculation.

This is a fixed set of scalar samples, not a forecast field that can answer queries
at new times or locations. Reusing it must preserve original sample provenance
separately from the newly calculated representative/arrival times.

## Next implementation steps

1. Add an explicit fixed-sample calculation operation that accepts the saved
   calculation-time inputs and validated snapshot. It must not call catalog,
   provider prepare/query, or TAF acquisition. Reuse the core phase/geometry/fuel
   calculation, with effective wind direction rounded after interpolation and
   after manual overrides. Preserve the original source values for comparison.
2. Rebuild dependent RCA/EOC, phase boundaries, arrival, ETE, fuel, display rows,
   and numeric RJFM guidance. VISUAL ARRIVAL's fixed CALM policy and the separate
   inbound slant-DME ceiling require their existing handling. Keep geometric and
   fuel validation rather than skipping it for the time target.
3. Keep original and rounded results separately. Returning to the original result
   must use the original cached result or original fixed samples, never repeatedly
   round the already rounded result. Reject unsupported/missing snapshots or
   mismatched inputs/reference/performance identities instead of acquiring new
   weather silently.
4. Add the post-NAV-LOG action and modal through the Application/worker contracts.
   The action must render the modal before queued calculation work and reject
   repeated clicks. Input edits, in-flight work, failures, and returning to the
   original need explicit state transitions. Use Gemini CLI for final UI wording,
   except exact user-provided wording, under `AGENTS.md`.
5. Extend tests for FORECAST, FTD interpolation, manual overrides, CALM/unavailable
   data, phase-boundary changes, old/corrupt snapshots, modified drafts, cache
   eviction, and OFF→ON→OFF stability. Verify UI flow order around 1,100 px and
   above 1,240 px if adding the action changes layout.

## Decisions still required

- Whether DME/TOAT display changes apply only to the selected rounded result or
  also to the ordinary result. Internal DME/temperature quantization is not
  authorized by the display requirement.
- Exact boundary and north-display conventions for the new wind transformation;
  preserve existing half-up/north conventions only after confirming the mode rule.
- The result selected by Save/autosave, JSON/download/clipboard export, restoration,
  and account sync; whether the chosen mode itself persists. Do not overwrite
  existing last-good data just by viewing a comparison before deciding this.
- Whether comparison uses the saved historical calculation or requires the current
  draft to match it. A silently changed draft cannot inherit the older samples.
- Final button/return labels and progress/failure text. No global default-on mode
  has been requested; the interaction is explicitly triggered after a result.

## Ten-second verification

Ten seconds is an end-to-end target, not a current guarantee. Measure click to
modal paint, worker queue wait, snapshot validation, calculation/guidance, and
fully rendered NAV LOG with a monotonic browser clock. Test warm-worker FORECAST
and FTD, manual overrides, ordinary and RJFM inbound routes, repeated comparisons,
reload/cache eviction, and blocked external networking. Report median, p95, and
maximum with device/browser/route/sample count. Native Python timing alone does
not establish iPad Safari latency; expensive inbound geometry must be measured.

The current snapshot tests establish capture/validation/persistence correctness.
They do not establish replay equivalence or click-to-result performance because
the replay operation and UI are not implemented yet.
