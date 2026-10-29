import { expect, test } from "@playwright/test";
import { canConfirmMapDraft, nextWaypointName, validMapDraft } from "../src/mapRouteDraft";
const airport = { id: "a", name: "RJFM", latitude_deg: 31.8, longitude_deg: 131.4, airport_id: "RJFM" };
test("route occurrences, endpoint constraints and waypoint numbering", () => {
  const wp = { ...airport, id: "b", name: "WP7", airport_id: null };
  expect(canConfirmMapDraft([])).toBe(false);
  expect(canConfirmMapDraft([airport])).toBe(false);
  expect(canConfirmMapDraft([airport, wp])).toBe(false);
  expect(canConfirmMapDraft([airport, { ...airport, id: "c" }])).toBe(true);
  expect(validMapDraft([airport, wp, { ...airport, id: "c" }])).toBe(true);
  expect(nextWaypointName([airport, wp])).toBe("WP8");
  expect(validMapDraft([airport, airport])).toBe(false);
  expect(validMapDraft([wp, airport])).toBe(false);
  expect(validMapDraft([airport, { ...wp, latitude_deg: NaN }])).toBe(false);
  expect(validMapDraft([airport, { ...wp, longitude_deg: 200 }])).toBe(false);
});

import { clusterCandidates, referencePoints, validReferenceSnapshot } from "../src/referencePoints";

test("airports and references cluster together by screen distance", () => {
  const mixed = [
    { id: "airport:RJFM", x: 100, y: 100 },
    { id: "RJFM-VREP-HITOTSUBA", x: 110, y: 110 },
    { id: "airport:RJFK", x: 300, y: 300 },
  ];
  expect(clusterCandidates(mixed).map(group => group.map(point => point.id))).toEqual([
    ["airport:RJFM", "RJFM-VREP-HITOTSUBA"], ["airport:RJFK"],
  ]);
  expect(clusterCandidates(mixed, 5)).toHaveLength(3);
});

test("reference snapshot survives draft recovery without consulting a changed master", () => {
  const snapshot = structuredClone(referencePoints[0]!);
  snapshot.source.table_effective_date = "2000-01-01";
  const selected = { id: "visit1", name: snapshot.name, airport_id: null,
    latitude_deg: snapshot.latitude_deg, longitude_deg: snapshot.longitude_deg, reference_snapshot: snapshot };
  expect(validMapDraft([airport, selected, { ...selected, id: "visit2" }])).toBe(true);
  expect(validMapDraft([selected])).toBe(false);
  expect(validMapDraft([airport, { ...selected, reference_snapshot: {} }])).toBe(false);
  expect(validMapDraft([airport, { ...selected, latitude_deg: 0 }])).toBe(false);
  expect(validReferenceSnapshot({ ...snapshot, source: null })).toBe(false);
  expect(validReferenceSnapshot({ ...snapshot, source: { ...snapshot.source, sha256: "bad" } })).toBe(false);
  expect(validReferenceSnapshot({ ...snapshot, bearing_true_deg: 360 })).toBe(true);
  expect(referencePoints).toHaveLength(115);
  expect(referencePoints.every(validReferenceSnapshot)).toBe(true);
});
