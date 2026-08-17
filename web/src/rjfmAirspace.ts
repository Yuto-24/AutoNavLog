import type {
  RjfmCivilTrainingTestAirspaceReference,
  RjfmCivilTrainingTestAirspaceTileReference,
} from "./types";

const APPROVED_SOURCE_PAGE_URL =
  "https://www.mlit.go.jp/koku/koku_tk10_000004.html";
const APPROVED_LAYER_METADATA_URL =
  "https://maps.gsi.go.jp/development/ichiran.html#kokuarea_minkankunren";
const APPROVED_TILE_URL_TEMPLATE =
  "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/{z}/{x}/{y}.geojson";
const APPROVED_TILES = [
  {
    id: "z8-x221-y103",
    url: "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/103.geojson",
    minimumLatitude: 31.952162238024968,
    maximumLatitude: 33.137551192346145,
    minimumLongitude: 130.78125,
    maximumLongitude: 132.1875,
    expectedPolygonNames: ["KS4-1/4", "KS4-1", "KS4-3", "KS4-5"],
  },
  {
    id: "z8-x221-y104",
    url: "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/104.geojson",
    minimumLatitude: 30.751277776257812,
    maximumLatitude: 31.952162238024968,
    minimumLongitude: 130.78125,
    maximumLongitude: 132.1875,
    expectedPolygonNames: [
      "KS4-2",
      "KS4-7",
      "KS4-6",
      "KS4-1/4",
      "KS4-1",
      "KS4-3",
      "KS4-5",
      "KS4-8",
    ],
  },
] as const;
const TILE_BOUNDARY_EPSILON_DEG = 0.000001;

const MAX_TILE_BYTES = 256_000;
const MAX_FEATURES_PER_TILE = 64;
const MAX_POLYGONS_PER_TILE = 24;
const MAX_RINGS_PER_POLYGON = 8;
const MAX_COORDINATES_PER_RING = 512;
const MAX_COORDINATES_PER_TILE = 4_096;
const MAX_DISPLAY_TEXT_LENGTH = 80;

const allowedAirspaceName = /^KS4-(?:[1-8]|1\/4)$/;

export interface RjfmTrainingAirspacePolygon {
  id: string;
  name: string;
  lowerLimit: string;
  upperLimit: string;
  authority: string;
  positions: [number, number][][];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasSameOrderedStrings(
  actual: readonly string[],
  expected: readonly string[],
): boolean {
  return actual.length === expected.length
    && expected.every((value, index) => actual[index] === value);
}

function requireDisplayText(
  value: unknown,
  fieldName: string,
  maximumLength = MAX_DISPLAY_TEXT_LENGTH,
): string {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > maximumLength
  ) {
    throw new Error(`GSI ${fieldName} is not a bounded display string`);
  }
  return value;
}

type ApprovedTile = (typeof APPROVED_TILES)[number];

function parsePosition(value: unknown, tile: ApprovedTile): [number, number] {
  if (!Array.isArray(value) || value.length !== 2) {
    throw new Error("GSI Polygon coordinate must contain longitude and latitude");
  }
  const [longitude, latitude] = value;
  if (
    typeof longitude !== "number"
    || !Number.isFinite(longitude)
    || longitude < -180
    || longitude > 180
    || typeof latitude !== "number"
    || !Number.isFinite(latitude)
    || latitude < -90
    || latitude > 90
  ) {
    throw new Error("GSI Polygon coordinate is outside finite WGS84 bounds");
  }
  if (
    latitude < tile.minimumLatitude - TILE_BOUNDARY_EPSILON_DEG
    || latitude > tile.maximumLatitude + TILE_BOUNDARY_EPSILON_DEG
    || longitude < tile.minimumLongitude - TILE_BOUNDARY_EPSILON_DEG
    || longitude > tile.maximumLongitude + TILE_BOUNDARY_EPSILON_DEG
  ) {
    throw new Error("GSI Polygon coordinate is outside its approved z8 tile");
  }
  return [latitude, longitude];
}

function parsePolygonCoordinates(
  value: unknown,
  coordinateCounter: { value: number },
  tile: ApprovedTile,
): [number, number][][] {
  if (
    !Array.isArray(value)
    || value.length === 0
    || value.length > MAX_RINGS_PER_POLYGON
  ) {
    throw new Error("GSI Polygon ring count is outside the accepted limit");
  }
  return value.map((rawRing) => {
    if (
      !Array.isArray(rawRing)
      || rawRing.length < 4
      || rawRing.length > MAX_COORDINATES_PER_RING
    ) {
      throw new Error("GSI Polygon coordinate count is outside the accepted limit");
    }
    coordinateCounter.value += rawRing.length;
    if (coordinateCounter.value > MAX_COORDINATES_PER_TILE) {
      throw new Error("GSI tile coordinate count exceeds the accepted limit");
    }
    const ring = rawRing.map((position) => parsePosition(position, tile));
    const first = ring[0];
    const last = ring.at(-1);
    if (
      first === undefined
      || last === undefined
      || first[0] !== last[0]
      || first[1] !== last[1]
    ) {
      throw new Error("GSI Polygon ring is not closed");
    }
    return ring;
  });
}

export function parseGsiCivilTrainingAirspaceTile(
  payload: unknown,
  tileReference: RjfmCivilTrainingTestAirspaceTileReference,
): RjfmTrainingAirspacePolygon[] {
  const tile = APPROVED_TILES.find(
    (candidate) => candidate.url === tileReference.url,
  );
  if (
    tile === undefined
    || !hasSameOrderedStrings(
      tileReference.expectedPolygonNames,
      tile.expectedPolygonNames,
    )
  ) {
    throw new Error("GSI response is not from an approved RJFM tile contract");
  }
  if (!isRecord(payload) || payload.type !== "FeatureCollection") {
    throw new Error("GSI response is not a FeatureCollection");
  }
  if (
    !Array.isArray(payload.features)
    || payload.features.length > MAX_FEATURES_PER_TILE
  ) {
    throw new Error("GSI FeatureCollection exceeds the accepted feature limit");
  }

  const coordinateCounter = { value: 0 };
  const polygons: RjfmTrainingAirspacePolygon[] = [];
  const expectedPolygonNames = new Set<string>(tileReference.expectedPolygonNames);
  const seenPolygonNames = new Set<string>();
  payload.features.forEach((rawFeature, featureIndex) => {
    if (!isRecord(rawFeature) || rawFeature.type !== "Feature") {
      throw new Error("GSI collection contains a malformed Feature");
    }
    if (!isRecord(rawFeature.geometry)) {
      throw new Error("GSI Feature geometry is malformed");
    }
    if (!isRecord(rawFeature.properties)) {
      throw new Error("GSI Feature properties are malformed");
    }
    const rawName = rawFeature.properties["空域名称"];
    const isKs4Feature = typeof rawName === "string" && rawName.startsWith("KS4-");
    if (rawFeature.geometry.type === "LineString") {
      return;
    }
    if (rawFeature.geometry.type !== "Polygon") {
      if (isKs4Feature) {
        throw new Error("GSI KS4 Feature has an unexpected geometry type");
      }
      return;
    }
    if (!isKs4Feature) {
      return;
    }
    const name = requireDisplayText(rawName, "airspace name", 16);
    if (!allowedAirspaceName.test(name)) {
      throw new Error("GSI Polygon has an unexpected KS4 airspace name");
    }
    if (!expectedPolygonNames.has(name)) {
      throw new Error("GSI tile contains an unexpected KS4 Polygon name");
    }
    if (seenPolygonNames.has(name)) {
      throw new Error("GSI tile contains a duplicate KS4 Polygon name");
    }
    seenPolygonNames.add(name);
    if (polygons.length >= MAX_POLYGONS_PER_TILE) {
      throw new Error("GSI tile exceeds the accepted KS4 Polygon limit");
    }
    polygons.push({
      id: `${tile.id}-${featureIndex}-${name}`,
      name,
      lowerLimit: requireDisplayText(rawFeature.properties["下限"], "lower limit"),
      upperLimit: requireDisplayText(rawFeature.properties["上限"], "upper limit"),
      authority: requireDisplayText(rawFeature.properties["管轄機関"], "authority"),
      positions: parsePolygonCoordinates(
        rawFeature.geometry.coordinates,
        coordinateCounter,
        tile,
      ),
    });
  });
  if (
    seenPolygonNames.size !== expectedPolygonNames.size
    || [...expectedPolygonNames].some((name) => !seenPolygonNames.has(name))
  ) {
    throw new Error("GSI tile is missing an expected KS4 Polygon name");
  }
  return polygons;
}

export function isApprovedRjfmAirspaceReference(
  reference: RjfmCivilTrainingTestAirspaceReference,
): boolean {
  return reference.availability === "REMOTE_GSI_GEOJSON"
    && reference.dataUse === "DISPLAY_ONLY_LIVE_REFERENCE"
    && reference.contentFingerprintScope
      === "CONFIGURATION_ONLY_LIVE_GEOJSON_EXCLUDED"
    && reference.sourcePageUrl === APPROVED_SOURCE_PAGE_URL
    && reference.layerMetadataUrl === APPROVED_LAYER_METADATA_URL
    && reference.tileUrlTemplate === APPROVED_TILE_URL_TEMPLATE
    && reference.featureNamePrefix === "KS4-"
    && reference.tileUrls.length === APPROVED_TILES.length
    && APPROVED_TILES.every((tile) => reference.tileUrls.includes(tile.url))
    && reference.tiles.length === APPROVED_TILES.length
    && APPROVED_TILES.every((tile) => {
      const referenceTile = reference.tiles.find((item) => item.url === tile.url);
      return referenceTile !== undefined
        && hasSameOrderedStrings(
          referenceTile.expectedPolygonNames,
          tile.expectedPolygonNames,
        );
    });
}

async function readBoundedJson(response: Response): Promise<unknown> {
  const declaredLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_TILE_BYTES) {
    throw new Error("GSI response exceeds the accepted byte limit");
  }
  const reader = response.body?.getReader();
  if (reader === undefined) {
    throw new Error("GSI response has no readable body");
  }
  const decoder = new TextDecoder();
  let byteCount = 0;
  let text = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value === undefined) continue;
      byteCount += value.byteLength;
      if (byteCount > MAX_TILE_BYTES) {
        await reader.cancel();
        throw new Error("GSI response exceeds the accepted byte limit");
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
  } finally {
    reader.releaseLock();
  }
  return JSON.parse(text) as unknown;
}

export async function fetchRjfmTrainingAirspace(
  reference: RjfmCivilTrainingTestAirspaceReference,
  signal: AbortSignal,
): Promise<RjfmTrainingAirspacePolygon[]> {
  if (!isApprovedRjfmAirspaceReference(reference)) {
    throw new Error("RJFM airspace reference does not match the approved GSI source");
  }
  const tilePolygons = await Promise.all(reference.tiles.map(async (tile) => {
    const response = await fetch(tile.url, {
      headers: { Accept: "application/geo+json, application/json" },
      mode: "cors",
      signal,
    });
    if (!response.ok) {
      throw new Error(`GSI tile request failed with HTTP ${response.status}`);
    }
    return parseGsiCivilTrainingAirspaceTile(
      await readBoundedJson(response),
      tile,
    );
  }));
  const polygons = tilePolygons.flat();
  if (polygons.length === 0 || polygons.length > MAX_POLYGONS_PER_TILE * 2) {
    throw new Error("GSI response has no accepted KS4 Polygon geometry");
  }
  return polygons;
}
