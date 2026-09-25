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
