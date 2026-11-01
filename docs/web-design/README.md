# NavMate Web design system

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
  move any workflow region. At 430 px and below, brand, actions and Project identity use separate rows to keep Save and Delete independently reachable. The dialog explains Local-first storage and account synchronization. Conflicts and Latest import collisions use a blocking modal; normal sync progress is not shown.
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
  Reference/performance revisions and the FTD internal run ID are available in a native
  Details disclosure. Weather mode, real forecast run IDs, warnings and other required sources remain visible.
- NAV LOG Summary above the main table with canonical TTL DIST and TTL TIME, plus
  destination TAF wind as reference data.
- NAV LOG result table aligned to the 19-column 別添8-1 layout. FROM and TO stay
  visible while the table scrolls horizontally; VOR/DME remains scrollable.
- TIME / FUEL PLAN below the main table, outside its horizontal scroll region.

## Container and responsive rules

- The Header uses one explicit row above 1320 px and two from 431 through 1320 px.
  At 430 px and below, explicitly place the brand, actions, and Project controls on three separate rows;
  independent controls must not wrap into additional rows. On two rows, put actions beside the brand
  and give the Project row all remaining width. Project label/revision remain nowrap and non-shrinking; only the name input flexes.
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
- Do not hide blockers, confirmation controls, required sources, or action reasons
  at any viewport width. Keep revision provenance accessible through Details at every width.
  Keep linked OpenStreetMap attribution visible even in
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

The dialog subtitle is `NavMate のお知らせ`. Show `既知の不具合` first (hide the entire section at zero),
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
JSON download/copy actions follow the NAV LOG and its applicable guidance, outside the
horizontal scroll region, with a 1rem top margin and the same content inset and
maximum width as NAV LOG. Buttons retain their 0.5rem gap and wrap on narrow screens.

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

## Provenance disclosure and map credits

Readiness keeps the weather mode visible. Reference/performance revisions and the
`ftd-fixed-v1` result ID are available in its native `詳細` disclosure; the stored values,
calculation fingerprints and real forecast run IDs are unchanged. The duplicated footer
revisions are removed. Static map-precision/date, altitude-rule and ground-preparation
explanations are removed from the normal view; conditional warnings and calculation
conditions remain independent of this display choice.

Map credits are separate from revision metadata. The base layer still displays its
OpenStreetMap copyright link on the map, as required by the
[OSM tile policy](https://operations.osmfoundation.org/policies/tiles/).
The live `kokuarea_minkankunren` GeoJSON is fetched from GSI, validates/extracts the
approved KS4 features, and renders them over the base map. While those layers are
displayed, their credit joins the existing bottom-right Leaflet attribution, keeping
the linked MLIT source page and GSI layer page plus the processing indication.
There is no separate explanation row below the map. The combined attribution uses
12 px text and wraps within the map at narrow widths. The legend sits above the
measured attribution height, including after resizing; the OSM credit stays visible
when airspace is unavailable. Source links retain the platform external-link behavior.
This credit is not moved behind Details: both the
[GSI terms](https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html) and
[MLIT terms](https://www.mlit.go.jp/link.html) require source identification, with a
processing indication for edited content. The current GSI tile-list page did not expose
the individual layer entry to the text reader; this was not treated as permission to
omit a credit or as proof of a layer-specific exception.

The compact credit text was determined with Gemini CLI (`gemini-3.8-flash-medium`):
`国土交通省・国土地理院データを加工`.
This live GSI source is unchanged; the attribution exemption for Shapefile/KML
from the AIP ZIP does not apply to this implementation.


## New MAP routes (Issue #174)

New work uses an input rail followed by a large MAP (at least 360 px on narrow
screens), with airport selection and an ordered Route Strip. Planning and Readiness
appear only after route confirmation. Above 1240 px the draft uses two columns;
confirmation restores the existing input / route / readiness three-column workspace.
At 1240 px and below both stages preserve the downward input → map → readiness
flow. Draft edits do not auto-fit or recenter the MAP. Existing KML input remains an
explicit compatibility entry; it is not a Map Draft import in this issue.

Confirmation moves the viewport and keyboard focus to the flight-plan DATE input
after the confirmed Project's Planning DOM is committed. A frame callback may run
before React mounts that panel, so the focus request is retained until the panel
exists and consumed once. A committed route with a storage error keeps the error
message in view while focusing the Planning input. Existing MAP regressions cover
the frame-before-commit order without replacing the Local Worker or calculation.
The Local test helper holds renderer host tasks until it observes the requested
focus frame, including when asynchronous confirmation schedules that frame after
an earlier renderer task. Deferring a task by one frame alone does not establish
this order.


Checkpoint entry (#224 / #272) uses the same validation and save path for the
name input's Enter key and the existing Add/Save buttons. Composition state,
`isComposing`, `keyCode === 229` and repeated keydown events do not submit.
A synchronous save lock covers Enter/click mixtures until the asynchronous save
settles; failures retain the draft and permit retry. Each MAP coordinate pick
focuses the mounted name input once and scrolls it into view; ordinary draft
updates and manual focus changes do not focus it again. Opening or editing a
checkpoint without a MAP pick does not request name focus.
`web/e2e/checkpoint-enter.spec.ts` covers Local and Legacy, including 390/1100/1440px
MAP-to-name-to-Enter flows. Composition events are simulated; these checks do not
establish physical IME behavior or mobile software keyboard activation.
