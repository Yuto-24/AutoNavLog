# MAP Route creation (#174)

New work begins with the airport markers on a large MAP. The first airport is FROM;
subsequent map clicks and airport selections append occurrences in flight order.
The Route Strip identifies occurrences independently, including repeat visits to the
same airport. Confirm requires at least two points with airport endpoints; the final
airport becomes TO and the penultimate intermediate point becomes VREP. A direct
FROM → TO route has no synthetic VREP. Existing Readiness rules still apply.

`ConfirmRouteRequest` accepts `candidate_kind=map` and ordered `map_points`.
The common Python facade resolves airport identities against the active catalog,
uses canonical airport coordinates for new selections, preserves every occurrence
(including repeated coordinates), rejects routes with fewer than two distinct
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

The existing KML/KMZ import controls stage candidates as new Map Drafts (#176).
Import uses unsimplified route coordinates, not display-only preview geometry;
routes above the existing 500-point MAP limit are explicitly rejected.
Undo, search, reorder and marker dragging are not implemented here.

Verification: `test_map_route.py` exercises validation, repeated airports, direct
routes, existing calculation Golden and recovery. `map-route.spec.ts` exercises the
real browser workflow at 1100/1440 px, occurrence deletion, discard confirmation,
viewport restoration, saved Project reopening and Local storage failure recovery.


## Confirmed-route editing (#175)

Planning can reopen a confirmed Project as a MAP draft. `editingRouteProjectId`
identifies its owner in the existing ephemeral Application Session; each copied
occurrence carries `original_node_id`. Neither entry nor draft editing replaces the
confirmed route. Cancel clears only the draft after discard confirmation. Same-tab
reload restores the editing state, including an empty draft after route clear.
Project switches retain the existing discard confirmation boundary.

Reconfirmation sends `edit_project_id` through the common confirmation operation.
The facade rejects a different Project and duplicate or foreign occurrence IDs.
An unchanged identity/geometry/endpoints sequence is a no-op, including customized
arrival policy. Otherwise unchanged occurrences retain identity, names and source;
unchanged directed section pairs retain manual planning inputs. If the destination
or VREP role changes, descent/visual-arrival phases are reassigned to the new tail
using the normal route phase rules; retained leg IDs, altitude/speed inputs and
Check Points are preserved wherever compatible. No-op reconfirmation does not
reassign phases. Adopted airport and
VOR snapshots remain tied to unchanged airport identities, not a newer catalog.
User-renamed reference occurrences retain their source snapshot and current name.
Only Check Points
linked to replaced sections are removed. New sections use normal installation
and readiness rules. Last Calculation remains its existing snapshot and changed
route inputs become stale until recalculated.

The MAP retains Check Point display while editing, but hides Check Point/leg editors
and KML replacement controls. Entry invalidates and drains pending recalculations
before taking the draft; asynchronous state updates use the current edit owner.

Verification adds backend identity, no-op, calculated snapshot, selective planning
and Check Point tests; pure frontend occurrence/session tests; and browser
reload/cancel/reconfirm plus region-order coverage at 1100/1440 px. Browser tests
must run in an environment permitting the browser/server sockets; adding them is
not evidence that they passed. Release version/changelog are owned by integration.

## KML/KMZ Draft integration (#176)

`importCandidateToMapDraft` in `web/src/importRouteDraft.ts` adapts the common
parser's `RouteCandidate` to ordered `MapRoutePoint` occurrences without changing
Application state, confirming a route, or writing a Project. Candidate coordinates
are already latitude/longitude pairs. Line, connected-line, Point and Polygon
candidates keep their supplied order and repeated occurrences; no winding reversal,
rotation, deduplication or synthetic VREP is performed here. Only the first and last
coordinates are snapped to the canonical airports identified by the parser's existing
5 NM endpoint matching. Rounded display distances do not drive another match policy.

The result distinguishes `ready`, `unmatched-endpoints` and invalid coordinate/count
input. An unmatched endpoint is kept in the preview but cannot be confirmed. In
particular, a missing departure airport does not satisfy the existing `validMapDraft`
session contract: retain the pending Import candidate rather than storing that preview
as `mapRouteDraft`. Unknown catalog IDs are treated as unmatched. New imported
occurrences do not carry confirmed-Project `original_node_id` references.

New Route creation stays on the large MAP. Platform file/Clipboard operations stage
parser candidates without erasing the previous Map Draft. One valid candidate is
adopted immediately; multiple candidates use a compact selection dialog. Cancellation,
parser failure or unusable geometry preserves the previous Draft. A replacement
requires the existing discard confirmation, and never merges two drafts. Polygon
order uses the same explicit Map confirmation as any other route, with no separate
Polygon checkbox. Import is not available for a confirmed Project or route re-edit.

The optional `pendingImportDraft` and `selectedImportCandidate` session fields retain
unmatched endpoint previews and candidate selection through same-tab reload without
weakening `validMapDraft`. Actual points are derived from the recovered parser payload,
not persisted as an invalid Draft. Pending import may be cancelled to return to the
previous Draft. A recognized legacy pending import resumes in the new Map workflow.

Verification includes all parser candidate kinds, occurrence order, endpoint matching,
invalid geometry and session validation, plus the imported parser geometry routed
through Map confirmation to the existing FTD calculation golden. Focused browser
coverage includes replacement/cancellation, denied Clipboard, reload and region order
at 1100/1440 px. The CI Local suite selects the import and re-edit scenarios;
application tests validate parser adaptation and session recovery.

## KML/KMZ の経路候補と連結規則

KML/KMZ の取込では、`Point`、`LineString`、`Polygon` の各 Placemark を読み取ります。KMZ 内に複数の KML がある場合は、使用する KML の選択が必要です。候補の選択後も MAP 上で経路と順序を確認し、経路を確定してください。

### 複数 LineString の連結

- **同じ最も内側の `Folder` または `Document`** に属する複数の `LineString` を対象にします。異なるコンテナの線は連結しません。
- KML に記載された順序と線の向きをそのまま使います。順番の並べ替えや線の自動反転はしません。
- 隣接する各線の終点と次の線の始点が **0.02 NM 以内**なら連結できます。一組でも超えた場合は、そのコンテナの線を連結せず、個別の候補として残して警告します。
- 連結点では、同じコンテナ内にある `Point` Placemark のうち、前の線の終点と次の線の始点の**双方から 0.02 NM 以内**のものを名称・座標に採用します。複数ある場合は距離と記載順をもとに選びます。
- 該当する `Point` がなければ、前の線の終点を使います。両端点のずれが **10 m を超える**場合は警告します。
- 線の途中に置かれた C'K などの `Point` は、連結点の条件を満たさない限り自動では経路点にしません。連結後の先頭・末尾には、同じコンテナ内で端点から 0.02 NM 以内にある `Point` の名称を使う場合があります。

### 候補の選択と上限

- 複数の経路候補があれば、利用者が候補を選びます。先頭候補を無条件には採用しません。
- `LineString` 候補がある場合は、全 `Point` を並べた Point-only 候補を追加しません。`LineString` がない Point-only KML は Point 候補として扱います。
- 連結候補には、採用元のコンテナと構成 `LineString` の情報を記録します。
- 選択した経路は、簡略化前の座標で **500 点まで**です。プレビュー用の線の簡略化でこの制限を回避しません。
- KML の読み取り・候補選択が成功しても、出発地・目的地が判定できない経路は MAP で確認・修正するまで確定できません。

正本となるパーサー実装は `src/autonavlog/importers/kml.py`、取込候補から MAP draft への変換は `web/src/importRouteDraft.ts` です。この節は既存挙動の説明であり、取込アルゴリズムや制限値を変更しません。
