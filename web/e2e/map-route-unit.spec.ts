import { expect, test } from "@playwright/test";
import { canConfirmMapDraft, nextWaypointName, validMapDraft, routeDraftFromProject } from "../src/mapRouteDraft";
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


test("confirmed draft preserves occurrence identities and leaves Project untouched", () => {
  const nodes = [
    { id: "first", sequence: 0, name: "FROM", latitude_deg: 31, longitude_deg: 131 },
    { id: "via", sequence: 1, name: "WP1", latitude_deg: 32, longitude_deg: 130 },
    { id: "last", sequence: 2, name: "TO", latitude_deg: 31, longitude_deg: 131 },
  ];
  const project = { id: "project", route_nodes: nodes, departure_airport_id: "RJFM", destination_airport_id: "RJFM" } as import("../src/types").Project;
  const before = structuredClone(project);
  const draft = routeDraftFromProject(project, []);
  expect(draft.map(point => point.original_node_id)).toEqual(["first", "via", "last"]);
  expect(draft.map(point => point.airport_id)).toEqual(["RJFM", null, "RJFM"]);
  expect(validMapDraft(draft)).toBe(true);
  draft.splice(1, 1);
  expect(project).toEqual(before);
  expect(validMapDraft([{ ...airport, original_node_id: 42 }])).toBe(false);
});
