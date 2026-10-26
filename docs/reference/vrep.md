# Registered VREP selection (#169)

The source of this initial catalog is [the confirmed Issue comment](https://github.com/Yuto-24/AutoNavLog/issues/169#issuecomment-6415872164), transcribed in [the source record](vrep-169-confirmed-source.md).
`data/reference/vrep.json` contains exactly 115 points from 16 airports. The 17th scoped airport, RJFY, has no Visual REP table in the supplied AD2 and contributes no inferred positions. RJDU is excluded; its points shared with RJFU must not be registered a second time. The airport catalog is unchanged.

Run `python scripts/reference/build_vrep_catalog.py` to reproduce the JSON from that approved record. Tests validate counts, source schema, and WGS84 direct solutions from each original true bearing and NM distance. A table bearing of 360 degrees is retained verbatim. File dates and table/ARP effective dates are independent.

These are derived reference positions, not published latitude/longitude coordinates. Six decimals preserve reproducibility, not meter-level precision. The rounded source bearings/distances bound their usefulness. No magnetic variation is added. No source PDFs need re-uploading.

## Selection and persistence

- Only route creation mounts candidate layers. Unselected airports and VREPs use the same 40-screen-pixel clustering algorithm; clicking a cluster zooms without appending a point.
- The approved candidate symbols are a 20 px double circle for VREP and a 28 px airplane circle with a 2 px outline for airports (both sizes include the outline), centered in a 28 px click target. Dev acceptance restored the airport's original size and increased VREP from 16 px to 20 px for readability; its inner ring is 10 px so both circles have the same CSS pixel alignment rather than a half-pixel offset from mixed odd/even diameters. Unselected symbols are white; selected VREPs are teal and selected airports navy, with white inner marks. Selection persists while any route occurrence remains. Avoid a square-plus-diamond symbol or a wide text badge: both were rejected during dev acceptance for map readability or similarity to navigation-facility symbols.
- A selected reference is removed from candidate clustering and rendered individually. Clicking it appends a new route occurrence. A plain background click remains an arbitrary waypoint with no snapping.
- An empty route can start only at an airport, and confirmation requires airport endpoints.
- Draft IDs and Project RouteNode UUIDs identify occurrences. `reference_snapshot.reference_id` identifies the common reference. The full source record, selected coordinates, bearing, distance and derivation are copied with each occurrence, including consecutive repeats.
- A snapshot is never resolved against the current catalog while restoring a draft or Project. Existing Projects with no snapshot remain valid. This addition is optional in the existing schema; it does not perform a migration or rewrite saved files.
- The existing arrival-plan `VISUAL_REPORTING_POINT` role remains the penultimate arrival point. Registered VREP identity is a separate snapshot property, so en-route VREPs do not acquire arrival calculation semantics.
- Consecutive repeated selections are retained. Existing route/readiness checks still apply to zero-distance legs; selecting a point again does not promise a calculable leg of zero length.

## Pending acceptance and boundaries

Main currently provides new MAP routes, but no confirmed-route re-edit entry point. #175 / #283 re-edit acceptance (redisplaying candidates and retaining snapshots through re-edit) is pending their integration. This partial implementation does not close #169. #282 / #283 are not included.

UI copy was determined by Gemini CLI (`gemini-3-flash-preview`, 2026-10-26). The caution, cluster accessible title, VREP title, and release note use that output.

Use the isolated WSL worktree runtime for dev acceptance. Preserve all existing volumes. Merging and applying to matsu require confirmation after dev acceptance; this change does not alter Cloudflare publication, MSM configuration, authentication, or server retirement.
