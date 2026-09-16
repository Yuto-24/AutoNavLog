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

- App header with app version, Information, project identity, storage state, Save, and New actions.
  Local mode adds a compact Account icon opening the login/logout dialog; account controls do not
  move any workflow region. The dialog explains local storage and the absence of remote sync.
  Information opens notices and the full release history without changing Project state. Use an Info icon
  and an unread dot at every width; only Known Issue additions/body changes use amber. The label is optional
  and appears only when the entire Header has ample room on one row (currently 1600 px and above).
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
- NAV LOG Summary above the main table with canonical TTL DIST and TTL TIME, plus
  destination TAF wind as reference data.
- NAV LOG result table aligned to the 19-column 別添8-1 layout. FROM and TO stay
  visible while the table scrolls horizontally; VOR/DME remains scrollable.
- TIME / FUEL PLAN below the main table, outside its horizontal scroll region.

## Container and responsive rules

- The Header uses one explicit row above 1320 px and two below it; never allow independent controls to
  wrap into a third row. On two rows, put actions beside the brand and give the Project row all remaining
  width. Project label/revision remain nowrap and non-shrinking; only the name input flexes.
  At 630 px and below, Save/New show icons with their accessible labels and titles retained.
  Verify 390/820/1100/1440 px and 1273/1242/997/996/631/630/629 px boundaries; also cover 320/1600 px.
- Above 1240 px, use input / route workspace / readiness columns and allow the
  route map height to be adjusted from 320 to 900 px.
- At 1240 px and below, keep one downward workflow: input, route and map,
  readiness, NAV LOG, then contextual guidance.
- At 820 px and below, stack every region, keep actions full-width, and make data
  tables horizontally scrollable. In NAV LOG, Summary precedes the scroll region;
  TIME / FUEL PLAN follows it at the page width. Route input and flight-plan fields come first;
  the map, map confirmation, and route-confirm action then continue in one downward flow.
- Do not hide blockers, confirmation controls, provenance, or action reasons
  at any viewport width. Keep linked OpenStreetMap attribution visible even in
  the empty-map overlay.

## Copy lock

Above the fold may contain only the product identity, the three workflow steps,
the imported route controls, flight-plan labels, `準備状況`, its next action,
reference provenance, and the current primary action. Marketing copy, claims,
metrics, badges, and unrelated navigation are prohibited.

Information is a low-priority utility in the Header. Its compact unread indicator is the only
copy-lock exception; it must not displace the workflow rail or move a workflow region.

The generated Information snapshot uses a namespaced SHA-256 content ID over every user-visible
entry, excluding internal IDs, GitHub Issue references and parser source-line metadata, using stable key ordering. The browser stores that
ID under `autonavlog.information.lastSeenUpdate`. The legacy `lastSeenRelease` value is retained and
migrates only when the immutable `a4a92da` v1.10.0 baseline ID matches exactly; unknown, malformed,
or same-version-changed values remain unread. `autonavlog.information.knownIssuesSeen` separately stores
Known Issue IDs and visible-body hashes. Additions/edits warn in amber and in the accessible name;
removals/reordering produce only the normal dot. Opening the dialog marks everything read immediately.

The dialog subtitle is `AutoNavLog のお知らせ`. Show `既知の不具合` first (hide the entire section at zero),
then `更新履歴`, with all historical entries expanded. Known Issues use amber panels, distinct from runtime
errors. User-facing content comes from `KNOWN_ISSUES.md` and the 利用者向け section of `CHANGELOG.md`, with no developer Issues,
distribution section or implementation jargon. Release dates are stored by GitHub Releases and are not
displayed in Information. See [the release workflow](../../README.md#開発).

## NAV LOG layout revision (Issue #132)

The historical calculated-review.png concept predates the NAV LOG Summary and
separate TIME / FUEL PLAN requirement. The current reference flow is NAV LOG
Summary, horizontally scrollable main table, TIME / FUEL PLAN, then guidance.
The 2026-08-13 fidelity ledger below remains historical evidence for the
earlier calculated view; current Playwright coverage verifies this revised
layout at 390 px, 1,100 px, and 1,440 px.

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
| Header | Navy product bar, version, Information, project identity, save/new actions | Explicit one/two-row Header; unread dot at every width, amber only for Known Issue additions/edits | Match |
| Workflow | Three numbered stages directly below header | Route, flight plan, review/calculation rail with completed states | Match |
| Desktop layout | Input / route / readiness columns | 24% / fluid / 25% three-column workspace | Match |
| Route workspace | Map over compact POINT/ROLE/ALT/PHASE table | Leaflet/OSM route, airport/VREP/RCA/EOC markers, editable Leg table | Match |
| Readiness | Provenance, next action, blockers/confirmations, primary calculation action | Same order; duplicate causes collapsed by code/Leg/segment | Match |
| Calculated view | Dense NAV LOG, destination TAF wind, and fuel strip | 19-column NAV LOG plus reference wind and TIME/FUEL tables | Historical; superseded by Issue #132 above |
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
