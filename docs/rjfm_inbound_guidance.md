# RJFM inbound west-extension guidance

This is a diagnostic for an RJFM inbound route whose authoritative NAV LOG has already applied the `OMARU → UMK = 4,500 ft` operational-descent profile. It never adds a virtual route node or changes NAV LOG physical distance, course, ETE, fuel, project status, or issue severity.

The solver derives its duration, TAS, wind, UMK, VREP, selected descent rate, and adopted VREP altitude from that completed authoritative profile. The card is transient: it is generated for the current calculation-input fingerprint and is not saved in project UI state. A stale authoritative plan prevents numeric guidance; an unavailable solver/reference produces only a warning diagnostic.

## Sources and current availability

`data/reference/rjfm-inbound-guidance/manifest.json` pins the payload SHA-256 for revision `2026-08-31-rjfm-inbound-west-guidance-v2`. The pack is `AVAILABLE` only after all of the following checks succeed; any mismatch fails closed and the card remains a non-blocking unavailable diagnostic.

- KS4-3 horizontal boundary: the vendored AIP Japan ENR 5.3-21 PDF is SHA-256 `91eba1411e896d1b62451fac298e0afa6131194a95736a181be251b91942c90a`. Its reviewed PDF page 747, effective 2024-02-22, supplies the eight published DMS vertices and the 10 NM MZE arc. The normalized artifact fixes the WGS84 minor-arc procedure and its generated 49-vertex ring. It declares a 0.02 NM maximum model error: a conservative sum of endpoint radial residual and arc-chord approximation. This record verifies the vendored bytes and their reviewed provenance fields; it does not claim an independently verified publisher signature chain.
- MZE DME: the same vendored PDF is used for AIP Japan ENR 4.1-15, PDF page 531, effective 2025-09-04. It records `315243.42N 1312614.88E`, 16.3 m / 54 ft MSL. The loader checks the primary artifact SHA-256, page field, dates, position, and elevation before admitting it.
- GSI KS4-3 GeoJSON remains `DISPLAY_ONLY_LIVE_REFERENCE` / `CROSS_CHECK_ONLY`. It is never a solver boundary. Its cross-check values are provenance diagnostics, not a replacement for the vendored primary source.
- Magnetic sector 250–290°, 5° coarse scan, and outward 0.5 NM DME rounding are `USER_DECISION` implementation policy dated 2026-08-31. They are separate from source-backed aeronautical data.
## 2026-08-31 Issue 89 observations

- A route that has enough time or distance does not thereby prove KS4-3 avoidance. The solver must search for a feasible route under the boundary constraint, including beyond an infeasible candidate.
- Outward DME rounding changes the turn point. The rounded point must retain the selected UMK magnetic-sector constraint and be revalidated for profile altitude, travel time, and airspace clearance before display.
- Raw and rounded diagnostics are useful only for an available, current solution. Failures, reference mismatch, or stale inputs suppress numeric guidance without changing the authoritative NAV LOG outcome.
- With calm air and fixed TAS, a time constraint is equivalent to a total-distance lower bound, so the raw-minimum bearing is not unique; tests that require a unique bearing must use a non-degenerate wind or obstacle fixture.
- GSI live geometry may encode coordinates as [longitude, latitude, altitude], while older fixtures use two elements. The display parser accepts finite two- or three-element tuples, discards altitude, and remains independent of solver input because GSI is DISPLAY_ONLY_LIVE_REFERENCE.
- The primary 49-vertex KS4-3 ring is checked exactly as 0.5 NM WGS84 chords. Do not shorten this path by reducing the search budget or clearance/model-error margins. Only the real RJFO inbound E2E fixture has a 90-second completion wait; product UI timeouts and mock/general waits remain unchanged.

## Geometry domain and conservative clearance

The shared geometry utility accepts sampled route polylines only when every WGS84 sample interval is at most 0.5 NM, the complete route is at most 240 NM, and all coordinates are between 10°S and 45°N. The combined route and boundary latitude extent and raw longitude extent must each be at most 12°. In particular, a short WGS84 leg that crosses the antimeridian (for example, 179.999°E to 179.999°W) is rejected because its straight latitude/longitude chord would leave the local model. This local domain covers the RJFM search envelope and the equatorial synthetic fixtures; an unsupported geometry is rejected instead of silently switching projection regimes.

Route intervals are sampled along the WGS84 geodesic and then checked as straight latitude/longitude chords against straight latitude/longitude polygon edges. For each interval, the clearance reported by the local equirectangular diagnostic is reduced by the source-declared boundary model error (0.02 NM for the normalized KS4-3 arc) plus `d²/(8 × 3400 NM) + 0.01 NM`, where `d` is the interval length. The first term is a scale estimate for the three-dimensional chord sagitta; it is not a strict upper bound for the projected latitude/longitude chord. The fixed 0.01 NM term is the declared blocking numerical rejection buffer for this sampling approximation and local arithmetic. A guarded clearance at or below zero is treated as an intersection, including a near-tangent. The 0.01 NM margin does not assert source-chart positional accuracy, which remains a prerequisite for a verified primary boundary.

The clearance value is a local planar approximation for diagnostics. Intersection checks preserve the required straight latitude/longitude edge model, but a future source with materially larger geographic extent or a different edge interpretation must be rejected or receive a separately reviewed geometry model.
