import { expect, test } from "@playwright/test";
import { importCandidateToMapDraft } from "../src/importRouteDraft";
import { canConfirmMapDraft, validMapDraft } from "../src/mapRouteDraft";
import type { AirportOption, RouteCandidate } from "../src/types";

const airports: AirportOption[] = [
  { id: "A", icao: "RJFM", latitudeDeg: 31.877, longitudeDeg: 131.449 },
  { id: "B", icao: "RJFK", latitudeDeg: 31.803, longitudeDeg: 130.719 },
].map(airport => ({ ...airport, name: airport.icao, elevationFtMsl: 0,
  patternAltitudeFtMsl: 1000, patternAltitudeValidationStatus: "VERIFIED",
  patternAltitudeSource: "test", patternAltitudeSourceRevision: "test" }));
const candidate: RouteCandidate = {
  kind: "line", index: 0, name: "KML route", vertexCount: 4, distanceNm: 40,
  coordinates: [[31.88, 131.45], [32, 131], [31.9, 130.9], [31.8, 130.72]],
  departureAirportId: "A", departureDistanceNm: 0.2,
  destinationAirportId: "B", destinationDistanceNm: 0.2,
};

test("all parser candidate kinds keep vertex order, snap only endpoints and do not mutate input", () => {
  for (const kind of ["line", "polygon", "points", "connected_lines"] as const) {
    const input = structuredClone({ ...candidate, kind });
    const before = structuredClone(input);
    const result = importCandidateToMapDraft(input, airports);
    expect(result.status).toBe("ready");
    if (result.status === "invalid") throw new Error("unexpected invalid input");
    expect(result.points.map(point => [point.latitude_deg, point.longitude_deg])).toEqual([
      [31.877, 131.449], [32, 131], [31.9, 130.9], [31.803, 130.719],
    ]);
    expect(result.points.map(point => point.name)).toEqual(["RJFM", "WP1", "WP2", "RJFK"]);
    expect(result.points.map(point => point.airport_id)).toEqual(["A", null, null, "B"]);
    expect(validMapDraft(result.points)).toBe(true);
    expect(canConfirmMapDraft(result.points)).toBe(true);
    expect(input).toEqual(before);
    expect(result.points.every(point => !("original_node_id" in point))).toBe(true);
  }
});

test("closed polygon keeps first coordinate, winding and repeated occurrences", () => {
  const result = importCandidateToMapDraft({ ...candidate, kind: "polygon",
    destinationAirportId: "A", coordinates: [[31.88, 131.45], [32, 131], [32, 131], [31.88, 131.45]],
  }, airports);
  expect(result.status).toBe("ready");
  if (result.status === "invalid") return;
  expect(result.points.map(point => [point.latitude_deg, point.longitude_deg])).toEqual([
    [31.877, 131.449], [32, 131], [32, 131], [31.877, 131.449],
  ]);
  expect(new Set(result.points.map(point => point.id)).size).toBe(4);
});

test("missing or stale airport matches stay unconfirmable without losing imported geometry", () => {
  for (const departureAirportId of [null, "unknown"]) {
    const result = importCandidateToMapDraft({ ...candidate, departureAirportId }, airports);
    expect(result.status).toBe("unmatched-endpoints");
    if (result.status === "invalid") return;
    expect(result.missingEndpoints).toEqual(["departure"]);
    expect(result.points[0]).toMatchObject({ latitude_deg: 31.88, longitude_deg: 131.45, airport_id: null });
    expect(canConfirmMapDraft(result.points)).toBe(false);
  }
  const result = importCandidateToMapDraft({ ...candidate, destinationAirportId: null }, airports);
  expect(result.status).toBe("unmatched-endpoints");
  if (result.status === "invalid") return;
  expect(result.missingEndpoints).toEqual(["destination"]);
  expect(canConfirmMapDraft(result.points)).toBe(false);
  const neither = importCandidateToMapDraft(candidate, []);
  expect(neither.status !== "invalid" && neither.missingEndpoints).toEqual(["departure", "destination"]);
});

test("direct route has no synthetic VREP and parser matches are authoritative despite rounded distances", () => {
  const result = importCandidateToMapDraft({ ...candidate,
    coordinates: [candidate.coordinates[0]!, candidate.coordinates[3]!],
    departureDistanceNm: 5, destinationDistanceNm: 5,
  }, airports);
  expect(result.status).toBe("ready");
  if (result.status !== "invalid") expect(result.points).toHaveLength(2);
});

test("rejects unusable coordinate counts and non-finite/out-of-range geometry", () => {
  for (const coordinates of [[], [[31, 131]], Array.from({ length: 501 }, () => [31, 131])]) {
    expect(importCandidateToMapDraft({ ...candidate, coordinates: coordinates as [number, number][] }, airports))
      .toEqual({ status: "invalid", reason: "point-count" });
  }
  for (const coordinate of [[NaN, 131], [31, Infinity], [91, 131], [31, -181]]) {
    expect(importCandidateToMapDraft({ ...candidate, coordinates: [coordinate as [number, number], [32, 130]] }, airports))
      .toEqual({ status: "invalid", reason: "coordinates" });
  }
  expect(importCandidateToMapDraft(candidate, [{ ...airports[0]!, latitudeDeg: NaN }, airports[1]!]))
    .toEqual({ status: "invalid", reason: "coordinates" });
});
