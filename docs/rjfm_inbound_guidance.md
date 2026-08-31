# RJFM inbound west-extension guidance

This is a diagnostic for an RJFM inbound route whose authoritative NAV LOG has already applied the `OMARU → UMK = 4,500 ft` operational-descent profile. It never adds a virtual route node or changes NAV LOG physical distance, course, ETE, fuel, project status, or issue severity.

The solver derives its duration, TAS, wind, UMK, VREP, selected descent rate, and adopted VREP altitude from that completed authoritative profile. The card is transient: it is generated for the current calculation-input fingerprint and is not saved in project UI state. A stale authoritative plan prevents numeric guidance; an unavailable solver/reference produces only a warning diagnostic.

## Sources and current availability

`data/reference/rjfm-inbound-guidance/manifest.json` pins payload SHA-256 `8bfbadcbb75d0406229e25b0e3c5d34159c271dcdc9912d9bd256cd4f2e0655d` for revision `2026-08-31-rjfm-inbound-west-guidance-v1`.

- MZE position and 54 ft MSL antenna elevation: [AIP Japan RJFM AD 2.19 public mirror](https://nagodede.github.io/aip/japan/documents/RJFM_full.pdf), effective 2018-11-08, retrieved 2026-08-31, SHA-256 `e493984c5cbd0340a421b2e049077e1bd5cafeb0b5a21d456ea7c65bb8d318d6`. This is honestly labeled `PUBLIC_AIP_MIRROR`.
- KS4-3 vertical notice: [MLIT notice](https://www.mlit.go.jp/koku/content/001913941.pdf), retrieved 2026-08-31, SHA-256 `b4b8b81a851a724d5b94cc59a0882a91c2e9f1a76987258f479a25abc9a3911a`. It identifies vertical limits but supplies no verified, versioned horizontal boundary usable by the solver.
- Magnetic sector 250–290°, 5° coarse scan, and outward 0.5 NM DME rounding are `USER_DECISION` implementation policy dated 2026-08-31. They are separate from source-backed aeronautical data.

The shipped reference is therefore `UNAVAILABLE` with `KS43_HORIZONTAL_BOUNDARY_UNVERIFIED`. The production card must remain a non-blocking warning until a versioned, validated primary horizontal KS4-3 boundary is vendored. The live GSI/MLIT display overlay is explicitly `DISPLAY_ONLY_LIVE_REFERENCE`; it must never enter NAV LOG calculation or this solver.

## 2026-08-31 Issue 89 observations

- A route that has enough time or distance does not thereby prove KS4-3 avoidance. The solver must search for a feasible route under the boundary constraint, including beyond an infeasible candidate.
- Outward DME rounding changes the turn point. The rounded point must retain the selected UMK magnetic-sector constraint and be revalidated for profile altitude, travel time, and airspace clearance before display.
- Raw and rounded diagnostics are useful only for an available, current solution. Failures, reference mismatch, or stale inputs suppress numeric guidance without changing the authoritative NAV LOG outcome.
- With calm air and fixed TAS, a time constraint is equivalent to a total-distance lower bound, so the raw-minimum bearing is not unique; tests that require a unique bearing must use a non-degenerate wind or obstacle fixture.

## Geometry domain and conservative clearance

The shared geometry utility accepts sampled route polylines only when every WGS84 sample interval is at most 0.5 NM, the complete route is at most 240 NM, and all coordinates are between 10°S and 45°N. The combined route and boundary latitude extent and raw longitude extent must each be at most 12°. In particular, a short WGS84 leg that crosses the antimeridian (for example, 179.999°E to 179.999°W) is rejected because its straight latitude/longitude chord would leave the local model. This local domain covers the RJFM search envelope and the equatorial synthetic fixtures; an unsupported geometry is rejected instead of silently switching projection regimes.

Route intervals are sampled along the WGS84 geodesic and then checked as straight latitude/longitude chords against straight latitude/longitude polygon edges. For each interval, the clearance reported by the local equirectangular diagnostic is reduced by `d²/(8 × 3400 NM) + 0.01 NM`, where `d` is the interval length. The first term is a scale estimate for the three-dimensional chord sagitta; it is not a strict upper bound for the projected latitude/longitude chord. The fixed 0.01 NM term is the declared blocking numerical rejection buffer for this sampling approximation and local arithmetic. A guarded clearance at or below zero is treated as an intersection, including a near-tangent. The 0.01 NM margin does not assert source-chart positional accuracy, which remains a prerequisite for a verified primary boundary.

The clearance value is a local planar approximation for diagnostics. Intersection checks preserve the required straight latitude/longitude edge model, but a future source with materially larger geographic extent or a different edge interpretation must be rejected or receive a separately reviewed geometry model.
