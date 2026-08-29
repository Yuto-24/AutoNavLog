# AutoNavLog Web design system

This directory freezes the implementation reference for the responsive Web UI.

## Concept images

- `primary-workspace.png`: KML/KMZ import, flight-plan input, route map, and
  readiness status in one desktop workspace.
- `calculated-review.png`: calculated NAV LOG and confirmation items.

The images are visual references only. All labels, inputs, tables, map markers,
and actions remain code-native and accessible.

## Tokens

| Token | Value | Use |
| --- | ---: | --- |
| `--color-navy-950` | `#0b1f33` | top bar and primary actions |
| `--color-navy-800` | `#173b5e` | headings and route line |
| `--color-teal-700` | `#0c7c78` | selected/VREP/automatic success |
| `--color-amber-700` | `#a8660d` | confirmation items |
| `--color-red-700` | `#b42318` | blockers and unavailable values |
| `--color-slate-50` | `#f7f9fb` | utility rails |
| `--color-slate-200` | `#d8e0e8` | borders and dividers |
| `--color-white` | `#ffffff` | primary canvas |
| `--radius-sm` | `6px` | fields and compact controls |
| `--radius-md` | `10px` | purposeful panels |

The primary background is true white. There are no gradients, glow effects, or
decorative cards. Shadows are reserved for temporary overlays.

## Typography

- UI and content: `Inter`, `Noto Sans JP`, system sans-serif fallback.
- Page title: 22–24 px / 700.
- Section title: 16–18 px / 700.
- Controls: 14 px / 600.
- Table/body: 13–14 px / 400–600.
- Supporting text: 12 px / 400 with at least 1.5 line height.

## Component inventory

- App header with app version, project identity, storage state, Save, and New actions.
- Three-step progress rail: Route, Flight plan, Review/calculation.
- KML/KMZ drop zone and paste dialog.
- Shape candidate list with a required route-use confirmation placed directly below the map.
- Flight-plan fields: DATE, ETD JST, read-only KML-derived FROM/TO, FUEL
  (default 90 gal), RUN UP, nose fairing, A/C, VAR, and TGL.
- Route map with airport, waypoint, VREP, RCA, EOC, and CP markers.
- Check Point creation from a map click or coordinates, with abeam projection and CRUD controls.
- Desktop map-height separator with pointer and keyboard operation.
- Editable route/leg table with ALT and PHASE controls.
- Readiness rail showing the next action, data provenance, blockers, confirmation
  items, and acknowledgement controls.
- NAV LOG result table aligned to the 19-column 別添8-1 layout plus INFO and
  TIME/FUEL summary.
- Destination TAF wind strip, shown as reference data and kept outside calculation inputs.

## Container and responsive rules

- Above 1240 px, use input / route workspace / readiness columns and allow the
  route map height to be adjusted from 320 to 900 px.
- At 1240 px and below, keep one downward workflow: input, route and map,
  readiness, NAV LOG, then contextual guidance.
- At 820 px and below, stack every region, keep actions full-width, and make data
  tables horizontally scrollable. Route input and flight-plan fields come first;
  the map, map confirmation, and route-confirm action then continue in one downward flow.
- Do not hide blockers, confirmation controls, provenance, or action reasons
  at any viewport width. Keep linked OpenStreetMap attribution visible even in
  the empty-map overlay.

## Copy lock

Above the fold may contain only the product identity, the three workflow steps,
the imported route controls, flight-plan labels, `準備状況`, its next action,
reference provenance, and the current primary action. Marketing copy, claims,
metrics, badges, and unrelated navigation are prohibited.

## Implementation fidelity ledger (verified 2026-08-13 JST)

### Render and inspection method

- Accepted concepts: `primary-workspace.png` and `calculated-review.png`.
- Verification execution date: 2026-08-13 (`Asia/Tokyo`).
- Implementation renders: Playwright Chromium against the built FastAPI-served SPA.
- Native desktop viewport: 1440 × 1000.
- Native mobile viewport: 390 × 844; the mobile image is a full-page capture.
- Screenshot evidence is written to the Playwright test output and is not
  version-controlled in this directory.
- The requested `agent-browser` binary was not installed, so the documented Playwright
  Chromium fallback was used. Its screenshot artifacts were inspected at native aspect ratio.

### Concept-to-implementation comparison

| Area | Concept | Implementation | Result |
| --- | --- | --- | --- |
| Header | Navy product bar, version, project identity, save/new actions | Same hierarchy and action placement | Match |
| Workflow | Three numbered stages directly below header | Route, flight plan, review/calculation rail with completed states | Match |
| Desktop layout | Input / route / readiness columns | 24% / fluid / 25% three-column workspace | Match |
| Route workspace | Map over compact POINT/ROLE/ALT/PHASE table | Leaflet/OSM route, airport/VREP/RCA/EOC markers, editable Leg table | Match |
| Readiness | Provenance, next action, blockers/confirmations, primary calculation action | Same order; duplicate causes collapsed by code/Leg/segment | Match |
| Calculated view | Dense NAV LOG, destination TAF wind, and fuel strip | 19-column NAV LOG plus reference wind and TIME/FUEL tables | Match |
| Visual language | White canvas, navy/teal, amber/red status, thin borders, no gradients | Same token family and restrained radii | Match |
| Mobile | Single-column stack with all safety/status content retained | 390 px stack, full-width controls, no body horizontal overflow | Match |

### Copy and data differences

- Concept project/route values were illustrative; implementation copy comes from the real
  Project, reference manifest, performance manifest, CalculationOutcome, and Issue models.
- The initial implementation render shows a purposeful empty map state because no KML has
  yet been selected; the primary concept showed a populated example route.
- Destination labels show the 100 ft rounded circuit altitude (`1,000 ft`) and verification
  status. The VREP row shows the standard 5 NM altitude (`1,500 ft`).
- The fixed development weather mode and bundled unverified airport references intentionally
  add fail-closed blockers that were not present in the polished concept.

### Intentional remaining deviations

- The concept map is a generated static geography; implementation uses live OpenStreetMap
  raster tiles and remains usable when tiles are unavailable.
- At desktop height, long readiness lists scroll inside the right rail so the calculation action
  remains reachable.
- The current bundled RJFM/RJFO reference rows remain `UNVERIFIED`; visual fidelity does not
  override the primary-source release gate.
- Fake weather remains the local default and prevents a release-ready status by design.
