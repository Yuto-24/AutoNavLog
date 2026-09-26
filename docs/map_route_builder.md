# MAP Route creation (#174)

New work begins with the airport markers on a large MAP. The first airport is FROM;
subsequent map clicks and airport selections append occurrences in flight order.
The Route Strip identifies occurrences independently, including repeat visits to the
same airport. Confirm requires at least two points with airport endpoints; the final
airport becomes TO and the penultimate intermediate point becomes VREP. A direct
FROM → TO route has no synthetic VREP. Existing Readiness rules still apply.

`ConfirmRouteRequest` accepts `candidate_kind=map` and ordered `map_points`.
The common Python facade resolves airport identities against the active catalog,
uses canonical airport coordinates, removes consecutive equal coordinates (retaining
the destination airport), rejects routes with fewer than two distinct consecutive
positions, and installs the route through the existing
Project/arrival/phase normalization and Readiness boundaries. Geometry is never
converted to KML; generated MAP labels never become imported Point provenance. Planning, Check Points, Calculation, Last Calculation, and the
Local/Legacy Repository contracts remain shared with existing routes.

Draft occurrences live only in the optional `mapRouteDraft` field of the existing
version-1 Application Session. Old sessions without this field remain compatible.
Same-tab reload restores the draft without confirming, calculating, or writing a
Project. New work, clear, import replacement, Project open and user-initiated account
changes ask before discarding it. External authentication revocation still follows
the existing account boundary. Confirmed routes reopen in Planning.

Viewport preferences (center/zoom) use Platform persistence values, scoped by account
and Project UUID, with a last-used view for new work and RJFM as the initial fallback.
They are best-effort device preferences, not synchronized Project or Calculation data.
Opening a confirmed Project without a saved device viewport fits its route once.
Draft confirmation retains its current view. Selecting FROM never fits/recenters the map. Resizing invalidates the Leaflet canvas
without fitting the route. Existing KML candidate preview retains its fit behavior.

The KML compatibility workflow is still available through `KML/KMZから開始` and the
existing import controls. It is not integrated into Map Draft in this issue (#176).
Confirmed-route editing (#175), undo, search, reorder, reference-point catalogs and
marker dragging are not implemented here.

Verification: `test_map_route.py` exercises validation, repeated airports, direct
routes, existing calculation Golden and recovery. `map-route.spec.ts` exercises the
real browser workflow at 1100/1440 px, occurrence deletion, discard confirmation,
viewport restoration, saved Project reopening and Local storage failure recovery.
