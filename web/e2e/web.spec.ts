import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { parseGsiCivilTrainingAirspaceTile } from "../src/rjfmAirspace";

import type {
  NavLogDisplayRow,
  RjfmCivilTrainingTestAirspaceName,
  RjfmDepartureGuidance,
  RjfmMapReference,
  WebState,
} from "../src/types";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);

const kml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document><Placemark><name>RJFM-RJFO</name><LineString><coordinates>
    131.4486111111,31.8772222222,0
    131.5000000000,32.4000000000,0
    131.6500000000,33.1000000000,0
    131.7000000000,33.4000000000,0
    131.7372222222,33.4794444444,0
  </coordinates></LineString></Placemark></Document>
</kml>`;

const kmlFromRjfk = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document><Placemark><name>RJFK-RJFO</name><LineString><coordinates>
    130.7194444444,31.8033333333,0
    131.0000000000,32.4000000000,0
    131.4000000000,33.1000000000,0
    131.7372222222,33.4794444444,0
  </coordinates></LineString></Placemark></Document>
</kml>`;

const rjfmPhysicalUmkOmaruKml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>小丸～日振島～祝島～ゴルフコース</name>
  <LineString><coordinates>
    131.4488055215004,31.87716585260077,0
    131.4317398539069,31.98214589070221,0
    131.4734489929498,32.16275095638636,0
    132.2948734240414,33.1802236311398,0
    131.9894319344609,33.78695544494976,0
    131.67890296839,33.62999835453385,0
    131.7371811724867,33.47957070171427,0
  </coordinates></LineString></Placemark>
</Document></kml>`;

const rjfmDepartureGuidanceFixture: RjfmDepartureGuidance = {
  rule_version: "RJFM_NORTHBOUND_R6_5_1_V2",
  reference_revision: "2026-08-17-rjfm-umk-guidance-v2",
  reference_content_fingerprint: "b".repeat(64),
  source_effective_dates: {
    training_procedure: "2024-05-01",
    aip_rjfm: "2026-03-01",
    pca_notice: "2020-11-05",
  },
  generated_against_fingerprint: "a".repeat(64),
  center_route: [
    {
      latitude_deg: 31.9851378,
      longitude_deg: 131.4242985,
      source: "KML:UMK",
      estimated_error_nm: 0,
    },
    {
      latitude_deg: 32.0852164,
      longitude_deg: 131.4500908,
      source: "RJFM_REFERENCE:OVER_FIELD",
      estimated_error_nm: 0.35,
    },
    {
      latitude_deg: 32.1625507,
      longitude_deg: 131.4703692,
      source: "KML:OMARU",
      estimated_error_nm: 0,
    },
  ],
  candidates: [
    {
      runway: "09",
      status: "VALID",
      turn_method: "FIXED_BANK_20",
      turn_direction: "LEFT",
      path: [
        {
          latitude_deg: 31.87618,
          longitude_deg: 131.43528,
          altitude_ft_msl: 15,
          elapsed_seconds: 0,
          segment: "INITIAL",
        },
        {
          latitude_deg: 31.91,
          longitude_deg: 131.39,
          altitude_ft_msl: 2400,
          elapsed_seconds: 150,
          segment: "EXTENSION_TURN",
        },
        {
          latitude_deg: 31.9851378,
          longitude_deg: 131.4242985,
          altitude_ft_msl: 5500,
          elapsed_seconds: 360,
          segment: "CENTER_INTERCEPT",
        },
      ],
      constraints: [
        {
          code: "UMK_TARGET",
          passed: true,
          hard: true,
          message: "UMK位置・高度の許容差内です。",
          metadata: {},
        },
      ],
      full_turns: 1,
      partial_turn_deg: 92.4,
      turn_entry_radial_deg: 326.2,
      turn_entry_dme_nm: 3.8,
      turn_entry_altitude_ft_msl: 2380,
      exit_drift_nm: 0.31,
      expected_time_delta_seconds: 47,
      position_residual_nm: 0.004,
      altitude_residual_ft: 4,
      tangent_residual_deg: 0.1,
      notes: ["風を含む地上軌跡です。"],
    },
    {
      runway: "27",
      status: "HARD_INVALID",
      turn_method: "ADJUSTED_MAX_RADIUS",
      turn_direction: "RIGHT",
      path: [
        {
          latitude_deg: 31.87807,
          longitude_deg: 131.46161,
          altitude_ft_msl: 21,
          elapsed_seconds: 0,
          segment: "INITIAL",
        },
        {
          latitude_deg: 31.93,
          longitude_deg: 131.5,
          altitude_ft_msl: 2700,
          elapsed_seconds: 185,
          segment: "EXTENSION_TURN",
        },
        {
          latitude_deg: 31.9851378,
          longitude_deg: 131.4242985,
          altitude_ft_msl: 5500,
          elapsed_seconds: 340,
          segment: "CENTER_INTERCEPT",
        },
      ],
      constraints: [
        {
          code: "PCA_PENETRATION",
          passed: false,
          hard: true,
          message: "PCA運用高度帯への進入を検出しました。",
          metadata: {},
        },
      ],
      full_turns: 0,
      partial_turn_deg: 188.1,
      turn_entry_radial_deg: 41.6,
      turn_entry_dme_nm: 5.2,
      turn_entry_altitude_ft_msl: 2850,
      exit_drift_nm: 0.52,
      expected_time_delta_seconds: -20,
      position_residual_nm: 0.008,
      altitude_residual_ft: 7,
      tangent_residual_deg: 0.1,
      notes: ["不成立経路は編集画面の診断専用です。"],
    },
  ],
  limitations: [
    "経路候補は計画支援用であり、ATC指示と実機の飛行を優先してください。",
  ],
};

const rjfmValidUnavailableGuidanceFixture: RjfmDepartureGuidance = {
  ...rjfmDepartureGuidanceFixture,
  candidates: [
    {
      ...rjfmDepartureGuidanceFixture.candidates[0]!,
      status: "VALID",
      constraints: [
        {
          code: "UMK_TARGET",
          passed: true,
          hard: true,
          message: "UMK位置・高度の許容差内です。",
          metadata: {},
        },
      ],
      turn_entry_dme_nm: 4.8,
      notes: [],
    },
    {
      runway: "27",
      status: "UNAVAILABLE",
      turn_method: "NONE",
      turn_direction: "RIGHT",
      path: [],
      constraints: [],
      full_turns: 0,
      partial_turn_deg: null,
      turn_entry_radial_deg: null,
      turn_entry_dme_nm: null,
      turn_entry_altitude_ft_msl: null,
      exit_drift_nm: null,
      expected_time_delta_seconds: null,
      position_residual_nm: null,
      altitude_residual_ft: null,
      tangent_residual_deg: null,
      notes: ["採用済みの風データがないため算出できません。"],
    },
  ],
};

const rjfmMapReferenceFixture: RjfmMapReference = {
  revision: "2026-08-17-rjfm-umk-guidance-v3",
  contentFingerprint: "c".repeat(64),
  pca: {
    name: "MIYAZAKI_SPECIAL_CONTROL_AREA",
    polygonVertices: [
      { latitudeDeg: 31.934444444444445, longitudeDeg: 131.52 },
      { latitudeDeg: 31.834444444444443, longitudeDeg: 131.5327777777778 },
      { latitudeDeg: 31.851666666666667, longitudeDeg: 131.74166666666665 },
      { latitudeDeg: 31.951666666666664, longitudeDeg: 131.73111111111112 },
    ],
    exclusionCenter: {
      latitudeDeg: 31.883333333333333,
      longitudeDeg: 131.45,
    },
    exclusionRadiusKm: 9,
    sourceAltitudeLowerM: 200,
    sourceAltitudeUpperM: 800,
    operationalAltitudeLowerFtMsl: 656,
    operationalAltitudeUpperFtMsl: 2700,
    altitudeBoundsInclusive: true,
    operationalAltitudePolicyStatus: "USER_APPROVED_NOT_EXACT_METRIC_CONVERSION",
    sourceIds: [
      "mlit-special-control-area-consolidated-2024-02-08",
      "user-approved-rjfm-guidance-policy-2026-08-16",
    ],
  },
  civilTrainingTestAirspace: {
    availability: "REMOTE_GSI_GEOJSON",
    dataUse: "DISPLAY_ONLY_LIVE_REFERENCE",
    contentFingerprintScope: "CONFIGURATION_ONLY_LIVE_GEOJSON_EXCLUDED",
    sourcePageUrl: "https://www.mlit.go.jp/koku/koku_tk10_000004.html",
    layerMetadataUrl: (
      "https://maps.gsi.go.jp/development/ichiran.html"
      + "#kokuarea_minkankunren"
    ),
    tileUrlTemplate: (
      "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/"
      + "{z}/{x}/{y}.geojson"
    ),
    tileUrls: [
      "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/103.geojson",
      "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/104.geojson",
    ],
    tiles: [
      {
        url: "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/103.geojson",
        expectedPolygonNames: ["KS4-1/4", "KS4-1", "KS4-3", "KS4-5"],
      },
      {
        url: "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/104.geojson",
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
    ],
    featureNamePrefix: "KS4-",
    checkedAtUtc: "2026-08-17T04:16:51Z",
    caution: "地図には誤差が含まれる場合があります。この表示は参照専用で、NAV LOG計算やPCA判定には使用しません。",
    sourceIds: [
      "mlit-civil-training-test-airspace-map-2026-08-17",
      "mlit-gsi-boundary-caution-2026-08-17",
      "gsi-civil-training-test-airspace-geojson-2026-08-17",
    ],
  },
};

interface GsiAirspaceFeatureFixture {
  type: "Feature";
  properties: Record<string, string>;
  geometry: {
    type: string;
    coordinates: unknown;
  };
}

interface GsiAirspaceCollectionFixture {
  type: "FeatureCollection";
  features: GsiAirspaceFeatureFixture[];
}

const gsiTile103PolygonNames: RjfmCivilTrainingTestAirspaceName[] = [
  "KS4-1/4",
  "KS4-1",
  "KS4-3",
  "KS4-5",
];
const gsiTile104PolygonNames: RjfmCivilTrainingTestAirspaceName[] = [
  "KS4-2",
  "KS4-7",
  "KS4-6",
  "KS4-1/4",
  "KS4-1",
  "KS4-3",
  "KS4-5",
  "KS4-8",
];
const gsiTile103Ring = [
  [131.25, 32.12],
  [131.48, 32.12],
  [131.48, 32.28],
  [131.25, 32.28],
  [131.25, 32.12],
];
const gsiTile104Ring = [
  [131.32, 31.8],
  [131.55, 31.8],
  [131.55, 31.92],
  [131.32, 31.92],
  [131.32, 31.8],
];

function gsiPolygonFeature(
  name: RjfmCivilTrainingTestAirspaceName,
  ring: number[][],
): GsiAirspaceFeatureFixture {
  return {
    type: "Feature",
    properties: {
      "空域名称": name,
      "下限": name === "KS4-2" ? "SFC" : "5500FT",
      "上限": name === "KS4-2" ? "8000FT" : "7000FT",
      "管轄機関": "航空交通管理センター",
    },
    geometry: {
      type: "Polygon",
      coordinates: [ring],
    },
  };
}

const gsiAirspaceTile103Fixture: GsiAirspaceCollectionFixture = {
  type: "FeatureCollection",
  features: [
    ...gsiTile103PolygonNames.map((name) => gsiPolygonFeature(name, gsiTile103Ring)),
    {
      type: "Feature",
      properties: {
        "空域名称": "KS3-4",
        "下限": "SFC",
        "上限": "5000FT",
        "管轄機関": "航空交通管理センター",
      },
      geometry: {
        type: "Polygon",
        coordinates: [[
          [131.1, 32], [131.2, 32], [131.2, 32.1], [131.1, 32],
        ]],
      },
    },
    {
      type: "Feature",
      properties: { "空域名称": "KS4-1" },
      geometry: {
        type: "LineString",
        coordinates: [[131.25, 32.12], [131.48, 32.12]],
      },
    },
  ],
};

const gsiAirspaceTile104Fixture: GsiAirspaceCollectionFixture = {
  type: "FeatureCollection",
  features: gsiTile104PolygonNames.map((name) => (
    gsiPolygonFeature(name, gsiTile104Ring)
  )),
};

type GsiFixtureMode =
  | "valid"
  | "malformed"
  | "oversized"
  | "missing-polygon"
  | "duplicate-polygon"
  | "unexpected-polygon"
  | "unexpected-geometry"
  | "http-error"
  | "http-error-with-peer-pending";

interface GsiFixtureState {
  mode: GsiFixtureMode;
  pendingPeerAborted: boolean;
}

const gsiFixtureStateByPage = new WeakMap<Page, GsiFixtureState>();

function setGsiFixtureMode(page: Page, mode: GsiFixtureMode): void {
  const state = gsiFixtureStateByPage.get(page);
  if (state === undefined) {
    gsiFixtureStateByPage.set(page, { mode, pendingPeerAborted: false });
    return;
  }
  state.mode = mode;
  state.pendingPeerAborted = false;
}

function didGsiPendingPeerAbort(page: Page): boolean {
  return gsiFixtureStateByPage.get(page)?.pendingPeerAborted ?? false;
}

function gsiPayloadForMode(
  basePayload: GsiAirspaceCollectionFixture,
  mode: GsiFixtureMode,
  isTile103: boolean,
): GsiAirspaceCollectionFixture {
  if (mode === "malformed" && isTile103) {
    return {
      ...basePayload,
      features: [{
        ...basePayload.features[0],
        geometry: {
          type: "Polygon",
          coordinates: [[
            [0, 0],
            [0.1, 0],
            [0.1, 0.1],
            [0, 0],
          ]],
        },
      }],
    };
  }
  if (mode === "missing-polygon" && isTile103) {
    return {
      ...basePayload,
      features: basePayload.features.filter((feature) => !(
        feature.geometry.type === "Polygon"
        && feature.properties["空域名称"] === "KS4-5"
      )),
    };
  }
  if (mode === "duplicate-polygon" && isTile103) {
    return {
      ...basePayload,
      features: [...basePayload.features, { ...basePayload.features[0] }],
    };
  }
  if (mode === "unexpected-polygon" && isTile103) {
    return {
      ...basePayload,
      features: [
        ...basePayload.features,
        gsiPolygonFeature("KS4-8", gsiTile103Ring),
      ],
    };
  }
  if (mode === "unexpected-geometry" && isTile103) {
    return {
      ...basePayload,
      features: basePayload.features.map((feature) => (
        feature.geometry.type === "Polygon"
        && feature.properties["空域名称"] === "KS4-3"
          ? {
              ...feature,
              geometry: {
                type: "Point",
                coordinates: [131.32, 32.18],
              },
            }
          : feature
      )),
    };
  }
  return basePayload;
}

async function installGsiAirspaceRoute(page: Page): Promise<void> {
  setGsiFixtureMode(page, "valid");
  page.on("requestfailed", (request) => {
    const state = gsiFixtureStateByPage.get(page);
    if (
      state?.mode === "http-error-with-peer-pending"
      && request.url().endsWith("/104.geojson")
    ) {
      state.pendingPeerAborted = true;
    }
  });
  await page.route(
    "https://maps.gsi.go.jp/xyz/kokuarea_minkankunren/8/221/*.geojson",
    async (route) => {
      const mode = gsiFixtureStateByPage.get(page)?.mode ?? "valid";
      const isTile103 = route.request().url().endsWith("/103.geojson");
      if (mode === "http-error-with-peer-pending") {
        if (isTile103) {
          await route.fulfill({ status: 503, body: "unavailable" });
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1_000));
        if (route.request().failure() !== null) return;
      }
      if (mode === "http-error") {
        await route.fulfill({ status: 503, body: "unavailable" });
        return;
      }
      if (mode === "oversized") {
        await route.fulfill({
          status: 200,
          contentType: "application/geo+json",
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Content-Length": "not-a-number",
          },
          body: " ".repeat(256_001),
        });
        return;
      }
      const basePayload = isTile103
        ? gsiAirspaceTile103Fixture
        : gsiAirspaceTile104Fixture;
      const payload = gsiPayloadForMode(basePayload, mode, isTile103);
      try {
        await route.fulfill({
          status: 200,
          contentType: "application/geo+json",
          headers: { "Access-Control-Allow-Origin": "*" },
          body: JSON.stringify(payload),
        });
      } catch (error) {
        if (mode === "http-error-with-peer-pending") return;
        throw error;
      }
    },
  );
}

async function reloadWithGsiFixtureMode(
  page: Page,
  mode: GsiFixtureMode,
  state: WebState,
): Promise<void> {
  setGsiFixtureMode(page, mode);
  await page.route("**/api/state", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(state),
    });
  }, { times: 1 });
  await page.reload();
}

const twoConnectedRouteCandidatesKml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Grouped routes</name>
    <Folder>
      <name>RJFM→RJFO①</name>
      <Placemark><name>Outbound 1</name><LineString><coordinates>
        131.4486111111,31.8772222222,0
        131.5100000000,32.2000000000,0
        131.5800000000,32.6500000000,0
      </coordinates></LineString></Placemark>
      <Placemark><name>OUT TURN</name><Point><coordinates>
        131.5800000000,32.6500000000,0
      </coordinates></Point></Placemark>
      <Placemark><name>Outbound 2</name><LineString><coordinates>
        131.5800000000,32.6500000000,0
        131.7372222222,33.4794444444,0
      </coordinates></LineString></Placemark>
    </Folder>
    <Folder>
      <name>RJFO→RJFM①</name>
      <Placemark><name>Inbound 1</name><LineString><coordinates>
        131.7372222222,33.4794444444,0
        131.5200000000,32.7000000000,0
      </coordinates></LineString></Placemark>
      <Placemark><name>HOME TURN</name><Point><coordinates>
        131.5200000000,32.7000000000,0
      </coordinates></Point></Placemark>
      <Placemark><name>Inbound 2</name><LineString><coordinates>
        131.5200000000,32.7000000000,0
        131.4486111111,31.8772222222,0
      </coordinates></LineString></Placemark>
    </Folder>
  </Document>
</kml>`;

const sameNamedConnectedRouteCandidatesKml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Duplicate routes</name>
    <Folder><name>Outbound</name><Folder><name>Route</name>
      <Placemark><name>Outbound 1</name><LineString><coordinates>
        131.4486111111,31.8772222222,0 131.5800000000,32.6500000000,0
      </coordinates></LineString></Placemark>
      <Placemark><name>Outbound 2</name><LineString><coordinates>
        131.5800000000,32.6500000000,0 131.7372222222,33.4794444444,0
      </coordinates></LineString></Placemark>
    </Folder></Folder>
    <Folder><name>Inbound</name><Folder><name>Route</name>
      <Placemark><name>Inbound 1</name><LineString><coordinates>
        131.7372222222,33.4794444444,0 131.5200000000,32.7000000000,0
      </coordinates></LineString></Placemark>
      <Placemark><name>Inbound 2</name><LineString><coordinates>
        131.5200000000,32.7000000000,0 131.4486111111,31.8772222222,0
      </coordinates></LineString></Placemark>
    </Folder></Folder>
  </Document>
</kml>`;

const twoConnectedRouteCandidatesKmz = Buffer.from(
  "UEsDBBQAAAAIAKqxEF1jGYFVZQEAAEMFAAAHAAAAZG9jLmttbKWUXXKCMBSF311FhmdL+FNoJ+JLa3+mFsfqAihkLKMkDoTaFfS9XUHX5kp6NaC0DgPY+5K58E1yzuUQMnyPV+iNJmnE2UDRVU1BlAU8jNhioMxnowtHGbodsgQKSJYOlFch1lcYbzYbla8pW0SpyqjAQGBDNRS3gxC55kEWUyZ2DbTMj6l7m/BsTUMEi6ApwfuH8v2Ir0KayKbApw+j8fbjCxZv+/ldxoGYrPyAxn6ydCXsZeKFZyxEeg6Sx4jRZ5GADZcEnCdgyIdjix0Q0k1dtSynr++rC51j24asrvYL6+laXl0TLGqH7g/mlLF+7wQjuKyE4LJGfLRUaXI+Q7P59KmwOOERTLjaXWs9+YZNpBTzNlrNu1aRxGzz8CFMU7XsS0vWWYMkuJyuqqh5Mmrj+qjds3OSVmspH5BRHpB9mrR/RujOG9+0y1BbQc0zVAyyZYTqBDX8s8+NEMHHu43sLj33B1BLAQIUAxQAAAAIAKqxEF1jGYFVZQEAAEMFAAAHAAAAAAAAAAAAAACAAQAAAABkb2Mua21sUEsFBgAAAAABAAEANQAAAIoBAAAAAA==",
  "base64",
);

const multiDocumentKmz = Buffer.from(
  "UEsDBBQAAAAIAG0kCl36V3yWdAAAAJwAAAAJAAAAZmlyc3Qua21sTY1BCgMhDEWvMsx6MKi7kuYEXRR6ApmmU1HjoAF7/NKu3H14vPcxlbx8SpZ+Xd+q5wVgjGHqyXLEboQVUsngjFsJ7znsXEJLhBIK0yu2rgj/jbco/NAW5SDca23PKEG5k/V283ax3m3eIcwIYZZgyv9O6QtQSwMEFAAAAAgAbSQKXeGCuSN0AAAAnQAAAAoAAABzZWNvbmQua21sTY1BCsMgEEWvErIODuouTOcEXRR6AjFDIuoYVLDHL+3K5efx3seY0/LJSdpjvXq/d4Axhio3yxmaEu4QcwKjzEr4Ss5zdjUSistMjX2RA+E/8BmE370GOQl9KfUI4jo30lZvVi/ams0ahBkhzBJM/d8rfQFQSwECFAMUAAAACABtJApd+ld8lnQAAACcAAAACQAAAAAAAAAAAAAAgAEAAAAAZmlyc3Qua21sUEsBAhQDFAAAAAgAbSQKXeGCuSN0AAAAnQAAAAoAAAAAAAAAAAAAAIABmwAAAHNlY29uZC5rbWxQSwUGAAAAAAIAAgBvAAAANwEAAAAA",
  "base64",
);

const displayCellFields = [
  "pa", "toat", "cas", "tas", "tc", "variation", "mc", "wind", "wca",
  "mh", "distance", "gs", "ete", "eto", "ato", "ate", "fuel",
] as const satisfies ReadonlyArray<keyof NavLogDisplayRow>;

async function expectDisplayProjectionToMatchWebTable(page: Page): Promise<void> {
  const state = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    return await response.json() as WebState;
  });
  const expectedRows = state.outcome?.display_rows;
  if (expectedRows === undefined) throw new Error("calculated display rows are missing");
  const actualRows = await page.locator(".nav-log-table tbody tr[data-row-type]").evaluateAll(
    (rows) => rows.map((row) => ({
      rowType: row.getAttribute("data-row-type"),
      sequence: Number(row.getAttribute("data-row-sequence")),
      values: Array.from(row.querySelectorAll<HTMLElement>("td[data-display-text]:not(.vor-reference-cell)"))
        .map((cell) => cell.dataset.displayText ?? ""),
    })),
  );

  expect(actualRows).toHaveLength(expectedRows.length);
  expectedRows.forEach((row, index) => {
    expect(actualRows[index]?.rowType).toBe(row.row_type);
    expect(actualRows[index]?.sequence).toBe(row.sequence);
    if (row.row_type === "LEG_SEPARATOR") {
      expect(actualRows[index]?.values).toEqual([]);
      return;
    }
    expect(actualRows[index]?.values).toEqual([
      row.from_name,
      row.to_name,
      ...displayCellFields.map((field) => {
        const cell = row[field];
        if (typeof cell !== "object" || cell === null || !("text" in cell)) {
          throw new Error(`display field ${field} is not a display cell`);
        }
        return cell.text ?? "";
      }),
    ]);
  });
}

async function calculateNavLog(
  page: Page,
  verifyDestinationWind = true,
): Promise<void> {
  const openPaste = page.getByRole("button", { name: "KMLを貼り付け" });
  await openPaste.click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  const textbox = dialog.getByRole("textbox");
  await expect(textbox).toBeFocused();
  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("Tab");
    await expect(dialog.locator(":focus")).toHaveCount(1);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(openPaste).toBeFocused();

  await openPaste.click();
  await textbox.fill(kml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();

  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await expect(page.getByLabel("TO")).toHaveValue(/RJFO/);
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  await expect(page.locator(".route-table tbody tr.vrep-row")).toHaveCount(1);
  const altitudeInputs = page.locator(".table-number-input");
  await expect(altitudeInputs.first()).toHaveValue("");
  await expect(altitudeInputs.last()).toHaveValue("1500");
  for (let index = 0; index < await altitudeInputs.count(); index += 1) {
    await altitudeInputs.nth(index).fill("4500");
  }
  const altitudeCandidates = page.locator(".altitude-candidate-select");
  const firstAltitudeCandidate = await altitudeCandidates
    .first()
    .locator("option")
    .first()
    .getAttribute("value");
  if (firstAltitudeCandidate === null) throw new Error("Altitude candidate is missing");
  for (let index = 0; index < await altitudeCandidates.count(); index += 1) {
    await altitudeCandidates.nth(index).selectOption({ index: 0 });
  }
  const cruiseAltitude = page.getByLabel(/出発Legの巡航高度候補/).first();
  const variationGuidance = page.locator(".altitude-course");
  await expect(variationGuidance.first()).toContainText("VAR +7°");
  await expect(variationGuidance.nth(1)).toContainText("VAR +8°");
  const cruiseCandidate = await cruiseAltitude.locator("option").first().getAttribute("value");
  if (cruiseCandidate === null) {
    throw new Error("Cruise altitude candidate is missing");
  }
  await cruiseAltitude.selectOption(cruiseCandidate);
  const patternAltitude = page.getByLabel("今回採用する場周経路高度");
  const arrivalRow = page.locator(".destination-row");
  const confirmDestination = page.getByRole("button", {
    name: "目的空港・場周高度を確定",
  });
  await expect(page.locator(".input-rail").getByLabel("今回採用する場周経路高度")).toHaveCount(0);
  await expect(arrivalRow.getByLabel("今回採用する場周経路高度")).toBeVisible();
  await expect(arrivalRow.getByText("飛行場標高", { exact: true })).toBeVisible();
  await expect(arrivalRow.getByText("17 ft MSL", { exact: true })).toBeVisible();
  await expect(arrivalRow).toContainText("master 1,000 ft MSL（標高差 983 ft）");
  await expect(patternAltitude).toHaveValue("1000");
  await patternAltitude.fill("");
  await expect(confirmDestination).toBeDisabled();
  await patternAltitude.fill("1000");
  await expect(confirmDestination).toBeEnabled();
  await patternAltitude.fill("1300");
  await expect(patternAltitude).toHaveValue("1300");
  await confirmDestination.click();
  await expect(patternAltitude).toHaveValue("1300");
  await expect(altitudeInputs.first()).toHaveValue(firstAltitudeCandidate);
  await expect(altitudeInputs.last()).toHaveValue("1800");
  await expect(cruiseAltitude).toHaveValue(cruiseCandidate);
  await expect(page.locator(".altitude-warning-row")).toHaveCount(0);
  let releaseCalculationRequest = () => {};
  const calculationRequestReleased = new Promise<void>((resolve) => {
    releaseCalculationRequest = resolve;
  });
  await page.route("**/api/calculation-jobs", async (route) => {
    await calculationRequestReleased;
    await route.continue();
  }, { times: 1 });
  const calculateButton = page.locator(".status-actions .primary-button");
  await expect(calculateButton).toHaveText("NAV LOGを作る");
  await calculateButton.click();
  const calculationProgress = page.getByRole("status", { name: "NAV LOGを計算中" });
  await expect(calculationProgress).toBeVisible();
  await expect(calculateButton).toHaveText("NAV LOGを計算中…");
  releaseCalculationRequest();
  await expect(page.getByLabel("計算済みNAV LOG")).toBeFocused({ timeout: 30_000 });
  await expect(calculationProgress).toBeHidden();
  const firstRow = page.locator(".nav-log-table .nav-leg-detail-row").first();
  await expect(
    page.locator(".nav-log-table").getByLabel(/計画高度$/).first(),
  ).toHaveValue(firstAltitudeCandidate);
  await expect(page.locator(".nav-log-table .nav-leg-heading-row").first().locator("td").nth(8)).toHaveText("+7");
  const windInputs = firstRow.locator(".nav-log-wind-inputs");
  const windDirectionInput = firstRow.getByLabel(/手動風向$/);
  const windSpeedInput = firstRow.getByLabel(/手動風速$/);
  await expect(windInputs).toBeVisible();
  await expect(windDirectionInput).toBeVisible();
  await expect(windSpeedInput).toBeVisible();
  await expect(windDirectionInput).toHaveAttribute(
    "placeholder",
    /^(DIR|\d{3})$/,
  );
  await expect(windSpeedInput).toHaveAttribute("placeholder", /^\d{1,2}$/);
  await expect.poll(() => windInputs.evaluate((element) => getComputedStyle(element).opacity)).toBe("1");
  await expect(page.getByLabel("NAV LOG高度ポリシー")).toHaveCount(0);
  await expect(page.getByText("PA = MSL", { exact: true })).toHaveCount(0);
  const finalRow = page.locator(".nav-log-table .nav-destination-info-row");
  if (verifyDestinationWind) {
    const destinationWindSummary = page.getByLabel("目的地空港の風予報");
    await expect(destinationWindSummary).toContainText(/目的地風: \d{3}\/\d{1,2} kt/);
    const destinationWind = (await destinationWindSummary.textContent())
      ?.match(/目的地風: (\d{3}\/\d{1,2}) kt/)?.[1];
    expect(destinationWind).toBeDefined();
    await expect(
      finalRow.getByRole("cell", { name: destinationWind, exact: true }),
    ).toBeVisible();
  }
  await expect(finalRow.getByLabel(/手動風向$/)).toHaveCount(0);
  await expect(finalRow.getByLabel(/手動風速$/)).toHaveCount(0);
  await expect(
    page.locator(".nav-log-table").getByText("自動", { exact: true }),
  ).toHaveCount(0);
  await expectDisplayProjectionToMatchWebTable(page);
}

async function reloadWithCurrentRjfmGuidance(
  page: Page,
  guidance: RjfmDepartureGuidance = rjfmDepartureGuidanceFixture,
): Promise<WebState> {
  const guidanceState = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    return await response.json() as WebState;
  });
  if (guidanceState.outcome === null) throw new Error("calculation outcome is missing");
  guidanceState.outcome.rjfm_departure_guidance = guidance;
  guidanceState.rjfmMapReference = rjfmMapReferenceFixture;
  guidanceState.readiness.calculationIsCurrent = true;
  await page.route("**/api/state", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(guidanceState),
    });
  }, { times: 1 });
  await page.reload();
  return guidanceState;
}

async function importKmlCandidate(page: Page): Promise<void> {
  await page.getByRole("button", { name: "KMLを貼り付け" }).click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(kml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
}

test.beforeEach(async ({ page }) => {
  await installGsiAirspaceRoute(page);
});

test("GSI live tile Polygon contract fails closed on payload drift", () => {
  const tile = rjfmMapReferenceFixture.civilTrainingTestAirspace.tiles[0];
  const polygons = parseGsiCivilTrainingAirspaceTile(
    gsiAirspaceTile103Fixture,
    tile,
  );
  expect(polygons.map((polygon) => polygon.name)).toEqual(gsiTile103PolygonNames);

  for (const rejectedMode of [
    "missing-polygon",
    "duplicate-polygon",
    "unexpected-polygon",
    "unexpected-geometry",
  ] as const) {
    expect(() => parseGsiCivilTrainingAirspaceTile(
      gsiPayloadForMode(gsiAirspaceTile103Fixture, rejectedMode, true),
      tile,
    )).toThrow();
  }
});

test("desktop workflow renders without the removed A4 output", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await expect(page.locator("body")).not.toBeEmpty();
  const savedProjectButton = page.getByRole("button", { name: "保存済みProjectを開く" });
  const savedProjectButtonLayout = await savedProjectButton.evaluate((button) => {
    const icon = button.querySelector("svg");
    const buttonRect = button.getBoundingClientRect();
    const iconRect = icon?.getBoundingClientRect();
    return {
      buttonWidth: buttonRect.width,
      iconWidth: iconRect?.width ?? 0,
      paddingLeft: getComputedStyle(button).paddingLeft,
      paddingRight: getComputedStyle(button).paddingRight,
    };
  });
  expect(savedProjectButtonLayout).toEqual({
    buttonWidth: 40,
    iconWidth: 18,
    paddingLeft: "0px",
    paddingRight: "0px",
  });
  await expect(page.getByLabel("TO")).toHaveValue("");
  await expect(page.getByLabel("RUN UP あり")).toBeChecked();
  await expect(page.getByLabel("ノーズフェアリングあり (OFF: -10 kt)")).not.toBeChecked();
  await expect(page.getByLabel("A/C ON (巡航速度 -2 kt)")).toBeChecked();
  await expect(page.getByLabel("QNH値")).toHaveCount(0);
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0);
  await page.screenshot({
    path: path.join(repositoryRoot, "docs/web-design/implementation-desktop.png"),
    fullPage: false,
  });

  await calculateNavLog(page);

  await expect(
    page.getByText("PATTERN_ALTITUDE_REQUIRED", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "A4転記補助HTMLを出力" })).toHaveCount(0);
  await page.screenshot({
    path: path.join(repositoryRoot, "docs/web-design/implementation-calculated.png"),
    fullPage: true,
  });

  // Render a deliberately failed display cell through the same Web component.
  // This keeps the visual contract explicit: only UNAVAILABLE says 未取得 and
  // receives the bold red treatment; a neighboring BLANK remains truly empty.
  const unavailableState = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    const next = await response.json() as WebState;
    const destination = next.outcome?.display_rows.find(
      (row) => row.row_type === "DESTINATION_INFO",
    );
    if (destination === undefined) throw new Error("destination display row is missing");
    destination.cas = {
      state: "UNAVAILABLE",
      text: "未取得",
      effective_value: null,
      reason_code: "E2E_TRUE_FAILURE",
      manual: false,
    };
    return next;
  });
  await page.route("**/api/state", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(unavailableState),
    });
  }, { times: 1 });
  await page.reload();
  const unavailableCell = page.locator(
    '.nav-destination-info-row [data-cell-state="UNAVAILABLE"]',
  );
  await expect(unavailableCell).toHaveText("未取得");
  const unavailableStyle = await unavailableCell.evaluate((element) => {
    const style = getComputedStyle(element);
    return { fontWeight: style.fontWeight, backgroundColor: style.backgroundColor };
  });
  expect(Number(unavailableStyle.fontWeight)).toBeGreaterThanOrEqual(700);
  expect(unavailableStyle.backgroundColor).not.toBe("rgba(0, 0, 0, 0)");
  const blankCell = page.locator(
    '.nav-destination-info-row [data-cell-state="BLANK"]',
  ).first();
  await expect(blankCell).toHaveText("");
  await expect(blankCell).not.toHaveClass(/unavailable-value/);

  expect(pageErrors).toEqual([]);
});

test("edited VREP altitude reaches the calculation request and NAV LOG", async ({ page }) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await importKmlCandidate(page);
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();
  await page.getByRole("button", { name: "目的空港・場周高度を確定" }).click();

  const routeAltitudes = page.locator(".route-table tbody .table-number-input");
  for (let index = 0; index < await routeAltitudes.count() - 1; index += 1) {
    await routeAltitudes.nth(index).fill("3500");
  }
  const vrepAltitude = page.locator(".route-table tbody tr.vrep-row .table-number-input");
  await expect(vrepAltitude).toHaveValue("1500");
  await vrepAltitude.fill("2100");
  await expect(vrepAltitude).toHaveValue("2100");

  const updateRequest = page.waitForRequest(
    (request) => request.url().endsWith("/api/project") && request.method() === "PUT",
  );
  await page.getByRole("button", { name: "NAV LOGを作る" }).click();
  const payload = (await updateRequest).postDataJSON() as Record<string, unknown>;
  expect(payload.arrival_altitude_mode).toBe("MANUAL_NON_STANDARD_ENTRY");
  expect(payload.manual_vrep_altitude_ft_msl).toBe(2100);
  expect(payload.manual_vrep_reason).toBe("経路画面で指定したVREP計画高度");

  await expect(page.getByLabel("計算済みNAV LOG")).toBeFocused({ timeout: 30_000 });
  const state = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    return await response.json() as WebState;
  });
  const visualResult = state.outcome?.sections.find(
    (section) => section.phase === "VISUAL_ARRIVAL",
  );
  expect(visualResult?.planned_altitude_ft_msl.automatic_value).toBe(2100);
  await expect(
    page.locator(".nav-log-table").getByRole("cell", { name: "2100", exact: true }),
  ).toHaveCount(1);
  await page.screenshot({ path: "/tmp/autonavlog-vrep-v1.5.0.png", fullPage: false });
  expect(pageErrors).toEqual([]);
  expect(
    consoleErrors.filter((message) => !message.includes("401 (Unauthorized)")),
  ).toEqual([]);
});

test("mobile numeric inputs allow clear then re-entry and TGL is sent as a number", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  const fuel = page.getByLabel("FUEL gal");
  await expect(fuel).toHaveValue("90");
  await fuel.fill("");
  await expect(fuel).toHaveValue("");
  await fuel.fill("77.5");
  await expect(fuel).toHaveValue("77.5");

  const tgl = page.getByLabel("TGL");
  await expect(tgl).toHaveValue("0");
  await tgl.fill("");
  await expect(tgl).toHaveValue("");

  await importKmlCandidate(page);
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();
  await expect(page.getByText("TGLは0～20の整数で入力してください。")).toBeVisible();

  await tgl.fill("3");
  await expect(tgl).toHaveValue("3");
  const confirmRequest = page.waitForRequest(
    (request) => request.url().endsWith("/api/route/confirm")
      && request.method() === "POST",
  );
  await page.getByRole("button", { name: "経路を確定" }).click();
  const payload = (await confirmRequest).postDataJSON() as Record<string, unknown>;
  expect(payload.tgl_count).toBe(3);
});

test("mobile route confirmation follows the map without scrolling back", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await importKmlCandidate(page);

  const map = page.locator("#route-map-frame");
  const confirmation = page.getByRole("region", { name: "経路確認" });
  const checkbox = page.getByLabel("地図とKML記載順を確認しました");
  const confirm = page.getByRole("button", { name: "経路を確定" });
  const mapBox = await map.boundingBox();
  const confirmationBox = await confirmation.boundingBox();
  const checkboxBox = await checkbox.boundingBox();
  const confirmBox = await confirm.boundingBox();
  if (!mapBox || !confirmationBox || !checkboxBox || !confirmBox) {
    throw new Error("Map confirmation layout is missing");
  }
  expect(confirmationBox.y).toBeGreaterThanOrEqual(mapBox.y + mapBox.height - 1);
  expect(checkboxBox.y).toBeLessThan(confirmBox.y);
  await expect(page.getByRole("separator", { name: "地図の高さを調整" })).toBeHidden();

  await checkbox.check();
  await confirm.click();
  await expect(confirmation).toBeHidden();
  await expect(page.getByRole("heading", { name: "チェックポイント" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("responsive workflow keeps a one-way order at intermediate width", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.goto("/");
  await expect(page).toHaveTitle(/AutoNavLog/);
  await expect(page.locator("body")).not.toBeEmpty();
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0);
  await calculateNavLog(page);
  await reloadWithCurrentRjfmGuidance(page);

  const input = page.locator(".input-rail");
  const route = page.locator(".route-workspace");
  const status = page.locator(".status-rail");
  const navLog = page.locator(".nav-log-scroll");
  const guidance = page.locator(".rjfm-guidance");
  const intermediate = await Promise.all([
    input.boundingBox(),
    route.boundingBox(),
    status.boundingBox(),
    navLog.boundingBox(),
    guidance.boundingBox(),
  ]);
  if (intermediate.some((box) => box === null)) {
    throw new Error("Intermediate workflow regions are missing");
  }
  expect(intermediate[1]!.y).toBeGreaterThanOrEqual(
    intermediate[0]!.y + intermediate[0]!.height - 1,
  );
  expect(intermediate[2]!.y).toBeGreaterThanOrEqual(
    intermediate[1]!.y + intermediate[1]!.height - 1,
  );
  expect(intermediate[3]!.y).toBeGreaterThanOrEqual(
    intermediate[2]!.y + intermediate[2]!.height - 1,
  );
  expect(intermediate[4]!.y).toBeGreaterThanOrEqual(
    intermediate[3]!.y + intermediate[3]!.height - 1,
  );
  const from = page.getByLabel("FROM");
  const to = page.getByLabel("TO");
  const date = page.locator('.flight-plan-section input[type="date"]');
  const time = page.locator('.flight-plan-section input[type="time"]');
  const intermediateSchedule = await Promise.all([date.boundingBox(), time.boundingBox()]);
  if (intermediateSchedule.some((box) => box === null)) {
    throw new Error("Intermediate DATE/ETD fields are missing");
  }
  expect(intermediateSchedule[1]!.x).toBeGreaterThanOrEqual(
    intermediateSchedule[0]!.x + intermediateSchedule[0]!.width - 1,
  );
  expect(Math.abs(intermediateSchedule[0]!.y - intermediateSchedule[1]!.y)).toBeLessThanOrEqual(1);
  const intermediateEndpoints = await Promise.all([from.boundingBox(), to.boundingBox()]);
  if (intermediateEndpoints.some((box) => box === null)) {
    throw new Error("Intermediate FROM/TO fields are missing");
  }
  expect(intermediateEndpoints[1]!.x).toBeGreaterThanOrEqual(
    intermediateEndpoints[0]!.x + intermediateEndpoints[0]!.width - 1,
  );
  expect(Math.abs(intermediateEndpoints[0]!.y - intermediateEndpoints[1]!.y)).toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 1440, height: 900 });
  const wide = await Promise.all([
    input.boundingBox(),
    route.boundingBox(),
    status.boundingBox(),
  ]);
  if (wide.some((box) => box === null)) {
    throw new Error("Wide workflow regions are missing");
  }
  expect(wide[1]!.x).toBeGreaterThanOrEqual(wide[0]!.x + wide[0]!.width - 1);
  expect(wide[2]!.x).toBeGreaterThanOrEqual(wide[1]!.x + wide[1]!.width - 1);
  expect(Math.abs(wide[0]!.y - wide[1]!.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(wide[1]!.y - wide[2]!.y)).toBeLessThanOrEqual(1);
  const wideSchedule = await Promise.all([date.boundingBox(), time.boundingBox()]);
  if (wideSchedule.some((box) => box === null)) {
    throw new Error("Wide DATE/ETD fields are missing");
  }
  expect(wideSchedule[1]!.y).toBeGreaterThanOrEqual(
    wideSchedule[0]!.y + wideSchedule[0]!.height - 1,
  );
  expect(wideSchedule[0]!.x + wideSchedule[0]!.width).toBeLessThanOrEqual(
    wide[0]!.x + wide[0]!.width,
  );
  expect(wideSchedule[1]!.x + wideSchedule[1]!.width).toBeLessThanOrEqual(
    wide[0]!.x + wide[0]!.width,
  );
  const wideEndpoints = await Promise.all([from.boundingBox(), to.boundingBox()]);
  if (wideEndpoints.some((box) => box === null)) {
    throw new Error("Wide FROM/TO fields are missing");
  }
  expect(wideEndpoints[1]!.y).toBeGreaterThanOrEqual(
    wideEndpoints[0]!.y + wideEndpoints[0]!.height - 1,
  );
  expect(wideEndpoints[0]!.x + wideEndpoints[0]!.width).toBeLessThanOrEqual(
    wide[0]!.x + wide[0]!.width,
  );
  expect(wideEndpoints[1]!.x + wideEndpoints[1]!.width).toBeLessThanOrEqual(
    wide[0]!.x + wide[0]!.width,
  );
  expect(pageErrors).toEqual([]);
});

test("RJFM to UMK and UMK to OMARU inputs are fixed while OMARU outgoing stays editable", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "KMLを貼り付け" }).click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(rjfmPhysicalUmkOmaruKml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  const rows = page.locator(".route-table tbody tr");
  await expect(page.locator(".route-table th", { hasText: "ROLE" })).toHaveCount(0);
  const departure = rows.nth(0);
  const umk = rows.nth(1);
  const omaru = rows.nth(2);
  await expect(departure.locator(".fixed-altitude-control")).toContainText(
    "UMK 5,500 ft HIT",
  );
  await expect(departure.locator(".fixed-phase")).toHaveText("上昇（固定）");
  await expect(umk.locator(".fixed-altitude-control")).toContainText(
    "UMK→OMARUの巡航高度",
  );
  await expect(umk.locator(".fixed-phase")).toHaveText("巡航（固定）");
  await expect(departure.locator(".table-number-input, .table-select")).toHaveCount(0);
  await expect(umk.locator(".table-number-input, .table-select")).toHaveCount(0);
  await expect(omaru.locator(".table-number-input")).toBeVisible();
  await expect(omaru.getByLabel(/出発LegのPhase/)).toBeVisible();
});

test("desktop map height is keyboard adjustable and fixed below breakpoint", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");

  const map = page.locator("#route-map-frame");
  const separator = page.getByRole("separator", { name: "地図の高さを調整" });
  await expect(separator).toBeVisible();
  await expect(separator).toHaveAttribute("aria-valuenow", "425");
  expect((await map.boundingBox())?.height).toBeCloseTo(425, 0);

  await separator.focus();
  await separator.press("ArrowDown");
  await expect(separator).toHaveAttribute("aria-valuenow", "450");
  expect((await map.boundingBox())?.height).toBeCloseTo(450, 0);

  await page.setViewportSize({ width: 1240, height: 1000 });
  await expect(separator).toBeHidden();
  expect((await map.boundingBox())?.height).toBeCloseTo(500, 0);
});

test("RJFM departure guidance renders route overlays and runway diagnostics", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await calculateNavLog(page);
  const guidanceState = await reloadWithCurrentRjfmGuidance(page);

  const guidance = page.getByRole("region", { name: "RJFM北方面出発ガイダンス" });
  await expect(guidance).toBeVisible();
  await expect(guidance.getByRole("heading", {
    name: "Newta CENTER Route 出発ガイダンス",
  })).toBeVisible();
  const runway09 = guidance.getByRole("article", { name: "RWY 09 候補 成立" });
  const runway27 = guidance.getByRole("article", { name: "RWY 27 候補 不成立" });
  const orderedCards = guidance.locator(".rjfm-candidate");
  await expect(orderedCards.nth(0)).toHaveAttribute("aria-label", /RWY 27/);
  await expect(orderedCards.nth(1)).toHaveAttribute("aria-label", /RWY 09/);
  const runway27WideBox = await runway27.boundingBox();
  const runway09WideBox = await runway09.boundingBox();
  if (!runway27WideBox || !runway09WideBox) {
    throw new Error("Wide runway guidance cards are missing");
  }
  expect(runway27WideBox.x + runway27WideBox.width).toBeLessThanOrEqual(
    runway09WideBox.x + 1,
  );
  await expect(runway09.locator(".rjfm-candidate-metrics dt")).toHaveText([
    "旋回開始高度",
    "旋回開始 MZE DME",
    "NAV LOG直線Legとの差",
  ]);
  await expect(runway09).not.toContainText("92.4°");
  await expect(runway27).not.toContainText("188.1°");
  await expect(runway09).toContainText("LOSS +1.0 min");
  await expect(runway09).toContainText("3.8 DME");
  await expect(runway09).not.toContainText("4 DME未満");
  await expect(runway09).not.toContainText("非ブロッキング注意");
  await expect(runway27).toContainText("GAIN −0.5 min");
  await expect(guidance).toContainText(
    "固定20°バンクを基本とし、必要時は最大半径調整モデルを想定します。",
  );
  await expect(guidance).toContainText(
    "候補経路のUMK到達時間から、NAV LOG主経路のRJFM→UMK/RCA直線距離を",
  );
  await expect(guidance).toContainText(
    "CLIMB GSで飛行した基準時間を差し引いた値です。LOSSは基準より長く、",
  );
  for (const removedLabel of [
    "旋回モデル",
    "MZE位置",
    "到達条件",
    "全周旋回後ドリフト",
    "制約判定",
    "解の残差",
  ]) {
    await expect(
      guidance.locator(".rjfm-candidate").getByText(removedLabel, { exact: true }),
    ).toHaveCount(0);
  }
  await expect(guidance.locator(".rjfm-constraint-list, .rjfm-residuals")).toHaveCount(0);
  await expect(guidance).toContainText("訓練飛行実施要領");
  await expect(guidance).toContainText("2024-05-01");
  await expect(guidance).toContainText("ATC指示と実機の飛行を優先");
  const navLogScrollBox = await page.locator(".nav-log-scroll").boundingBox();
  const guidanceBox = await guidance.boundingBox();
  if (!navLogScrollBox || !guidanceBox) {
    throw new Error("NAV LOG guidance placement cannot be measured");
  }
  expect(guidanceBox.y).toBeGreaterThanOrEqual(
    navLogScrollBox.y + navLogScrollBox.height - 1,
  );

  const legend = page.getByRole("group", { name: "RJFMガイダンス凡例" });
  await expect(legend).toHaveCSS("pointer-events", "none");
  await expect(legend).toContainText("PCA 200–800 m");
  await expect(legend).toContainText("中心除外 9 km");
  await expect(legend).toContainText("民間訓練試験空域 KS4（GSI）");
  await expect(legend).toContainText("GSI: 12区画を表示");
  const airspaceNote = page.getByLabel("RJFM空域データ注記");
  await expect(airspaceNote).toContainText("NAV LOG計算やPCA判定には使用しません");
  await expect(airspaceNote).not.toContainText("境界付近は空域を管轄する機関へ確認してください");
  await expect(airspaceNote.getByRole("link", { name: "国土交通省" })).toHaveAttribute(
    "href",
    "https://www.mlit.go.jp/koku/koku_tk10_000004.html",
  );
  await expect(airspaceNote.getByRole("link", { name: "国土地理院レイヤー" })).toHaveAttribute(
    "href",
    /kokuarea_minkankunren$/,
  );
  await expect(legend).toContainText("Newta CENTER");
  await expect(legend).toContainText("RWY 09 成立");
  await expect(legend).toContainText("RWY 27 不成立");
  await expect(page.locator(".rjfm-center-route")).toHaveAttribute("stroke-dasharray", "8 6");
  await expect(page.locator(".rjfm-guidance-path.is-rwy-09")).toHaveAttribute(
    "stroke",
    "#2368a2",
  );
  await expect(page.locator(".rjfm-guidance-path.is-warning")).toHaveCount(0);
  await expect(page.locator(".rjfm-guidance-path.is-invalid")).toHaveAttribute(
    "stroke",
    "#b42318",
  );
  await expect(page.locator(".rjfm-center-marker")).toHaveCount(3);
  await expect(page.locator(".rjfm-pca-boundary")).toHaveCount(1);
  await expect(page.locator(".rjfm-pca-boundary")).toHaveAttribute("stroke", "#a45b13");
  await expect(page.locator(".rjfm-pca-exclusion")).toHaveCount(1);
  await expect(page.locator(".rjfm-pca-exclusion")).toHaveAttribute(
    "stroke-dasharray",
    "7 6",
  );
  await expect(page.locator(".rjfm-training-airspace")).toHaveCount(12);

  await page.setViewportSize({ width: 390, height: 844 });
  const runway09Box = await runway09.boundingBox();
  const runway27Box = await runway27.boundingBox();
  if (!runway09Box || !runway27Box) throw new Error("runway guidance cards are missing");
  expect(runway09Box.y).toBeGreaterThan(runway27Box.y + runway27Box.height - 1);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  expect(pageErrors).toEqual([]);

  await page.getByLabel("FUEL gal").fill("77");
  await expect(guidance).toHaveCount(0);
  await expect(legend).toBeVisible();
  await expect(legend).toContainText("PCA 200–800 m");
  await expect(legend).toContainText("民間訓練試験空域 KS4（GSI）");
  await expect(legend).not.toContainText("Newta CENTER");
  await expect(legend).not.toContainText("RWY 09");
  await expect(page.locator(".rjfm-guidance-path")).toHaveCount(0);
  await expect(page.locator(".rjfm-center-route")).toHaveCount(0);
  await expect(page.locator(".rjfm-center-marker")).toHaveCount(0);
  await expect(page.locator(".rjfm-pca-boundary")).toHaveCount(1);
  await expect(page.locator(".rjfm-pca-exclusion")).toHaveCount(1);
  await expect(page.locator(".rjfm-training-airspace")).toHaveCount(12);

  guidanceState.readiness.calculationIsCurrent = false;
  await page.route("**/api/state", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(guidanceState),
    });
  }, { times: 1 });
  await page.reload();
  await expect(page.getByRole("region", {
    name: "RJFM北方面出発ガイダンス",
  })).toHaveCount(0);
  await expect(legend).toBeVisible();
  await expect(legend).toContainText("PCA 200–800 m");
  await expect(legend).toContainText("民間訓練試験空域 KS4（GSI）");
  await expect(legend).not.toContainText("Newta CENTER");
  await expect(page.locator(".rjfm-guidance-path")).toHaveCount(0);
  await expect(page.locator(".rjfm-center-route")).toHaveCount(0);
  await expect(page.locator(".rjfm-center-marker")).toHaveCount(0);
  await expect(page.locator(".rjfm-pca-boundary")).toHaveCount(1);
  await expect(page.locator(".rjfm-pca-exclusion")).toHaveCount(1);
  await expect(page.locator(".rjfm-training-airspace")).toHaveCount(12);

  guidanceState.readiness.calculationIsCurrent = true;
  guidanceState.outcome.rjfm_departure_guidance = rjfmValidUnavailableGuidanceFixture;
  await page.route("**/api/state", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(guidanceState),
    });
  }, { times: 1 });
  await page.reload();

  const statusCoverageGuidance = page.getByRole("region", {
    name: "RJFM北方面出発ガイダンス",
  });
  await expect(statusCoverageGuidance.getByRole("article", {
    name: "RWY 09 候補 成立",
  })).toBeVisible();
  await expect(statusCoverageGuidance.getByRole("article", {
    name: "RWY 27 候補 算出不可",
  })).toContainText("採用済みの風データがないため算出できません。");
  const statusCoverageLegend = page.getByRole("group", { name: "RJFMガイダンス凡例" });
  await expect(statusCoverageLegend).toContainText("RWY 09 成立");
  await expect(statusCoverageLegend).not.toContainText("RWY 27");
  await expect(statusCoverageLegend.locator("span").first()).toHaveCSS("font-size", "12px");
  await expect(statusCoverageGuidance.locator(".rjfm-candidate-metrics dt").first()).toHaveCSS(
    "font-size",
    "12px",
  );
  await expect(statusCoverageGuidance.locator(".rjfm-candidate.is-unavailable .rjfm-status"))
    .toHaveCSS("color", "rgb(51, 74, 96)");
  await expect(page.locator(".rjfm-guidance-path.is-rwy-09")).toHaveCount(1);
  await expect(page.locator(".rjfm-guidance-path.is-unavailable")).toHaveCount(0);

  const rejectedAirspaceLegend = page.getByRole("group", {
    name: "RJFMガイダンス凡例",
  });
  for (const rejectedMode of [
    "malformed",
    "oversized",
    "missing-polygon",
    "duplicate-polygon",
    "unexpected-polygon",
    "unexpected-geometry",
    "http-error",
  ] as const) {
    await reloadWithGsiFixtureMode(page, rejectedMode, guidanceState);
    await expect(rejectedAirspaceLegend).toContainText(
      "GSI空域は取得できず非表示",
    );
    await expect(page.locator(".rjfm-training-airspace")).toHaveCount(0);
    await expect(page.locator(".rjfm-pca-boundary")).toHaveCount(1);
    await expect(page.locator(".rjfm-pca-exclusion")).toHaveCount(1);
  }

  await reloadWithGsiFixtureMode(
    page,
    "http-error-with-peer-pending",
    guidanceState,
  );
  await expect(rejectedAirspaceLegend).toContainText(
    "GSI空域は取得できず非表示",
  );
  await expect.poll(() => didGsiPendingPeerAbort(page), {
    message: "the pending peer GSI request should be aborted after the first tile fails",
  }).toBe(true);
  await expect(page.locator(".rjfm-training-airspace")).toHaveCount(0);
  await expect(page.locator(".rjfm-pca-boundary")).toHaveCount(1);
  expect(pageErrors).toEqual([]);
});

test("FTD route settings and checkpoint CRUD are available from the web UI", async ({ page }) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.goto("/");
  await expect(page).toHaveTitle(/AutoNavLog/);
  await expect(page.locator("body")).not.toBeEmpty();
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0);
  await importKmlCandidate(page);

  await page.getByLabel("気象モード").selectOption("FTD");
  await expect(page.getByLabel("地上風向 ° FROM")).toHaveValue("360");
  await expect(page.getByLabel("地上風速 kt")).toHaveValue("15");
  await expect(page.getByLabel("5,000 ft風向 ° FROM")).toHaveValue("270");
  await expect(page.getByLabel("5,000 ft風速 kt")).toHaveValue("30");
  const confirmRoute = page.getByRole("button", { name: "経路を確定" });
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByLabel("地上風向 ° FROM").fill("0");
  await expect(confirmRoute).toBeDisabled();
  await page.getByLabel("地上風向 ° FROM").fill("1.5");
  await expect(confirmRoute).toBeDisabled();
  await page.getByLabel("地上風向 ° FROM").fill("350");
  await page.getByLabel("地上風速 kt").fill("10");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("10");
  await page.getByLabel("5,000 ft風速 kt").fill("20");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("360");
  await expect(confirmRoute).toBeEnabled();
  await confirmRoute.click();

  const stateAfterConfirm = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    return await response.json() as WebState;
  });
  expect(stateAfterConfirm.project?.weather_mode).toBe("FTD");
  expect(stateAfterConfirm.project?.ftd_weather?.wind_at_5000_ft.speed_kt).toBe(20);
  expect(stateAfterConfirm.project?.ftd_weather?.wind_at_5000_ft.direction_deg_from).toBe(360);

  const routePanel = page.getByRole("region", { name: "経路地図とLeg設定" });
  const editor = routePanel.getByRole("region", { name: "チェックポイント設定" });
  await editor.getByRole("button", { name: "チェックポイントを追加" }).click();
  await expect(editor.getByLabel("関連Leg")).toHaveCount(0);
  const mapFrame = routePanel.locator("#route-map-frame");
  const activeMapPicker = editor.getByRole("button", { name: "地図上の地点をクリック" });
  await expect(activeMapPicker).toHaveAttribute("aria-pressed", "true");
  await expect(mapFrame).toHaveClass(/is-picking-checkpoint/);
  expect(await mapFrame.evaluate((element) => getComputedStyle(element).boxShadow)).toContain(
    "rgba(11, 31, 51, 0.58)",
  );
  await routePanel.locator(".route-map").click({ position: { x: 300, y: 200 } });
  await expect(mapFrame).not.toHaveClass(/is-picking-checkpoint/);
  const draftMarker = routePanel.locator(".checkpoint-draft-marker");
  await expect(draftMarker).toHaveCount(1);
  await expect(routePanel.getByText("仮CP（未保存）", { exact: true })).toBeVisible();
  await expect(editor.getByLabel("緯度")).not.toHaveValue("");
  await expect(editor.getByLabel("経度")).not.toHaveValue("");
  const nameWarning = editor.getByText(
    "チェックポイントの名称・未入力（入力必須）",
    { exact: true },
  );
  await expect(nameWarning).toBeVisible();
  await expect(nameWarning).toHaveCSS("color", "rgb(180, 35, 24)");
  await expect(editor.getByLabel("名称")).toHaveAttribute("aria-invalid", "true");
  await expect(editor.getByLabel("名称")).toHaveCSS("border-top-color", "rgb(180, 35, 24)");
  await expect(editor.getByText("Blocking", { exact: true })).toHaveCount(0);
  const draftMarkerStroke = await draftMarker.getAttribute("stroke");
  expect(draftMarkerStroke).toBe("#6e4aa0");

  await editor.getByLabel("緯度").fill("35.000000");
  await expect(draftMarker).toHaveCount(0);
  await editor.getByLabel("経度").fill("140.000000");
  await expect(editor.getByText(
    "Leg線分内にabeam点があり、横ずれ10 NM以内となる関連Legがありません。",
  )).toBeVisible();
  await expect(editor.getByRole("button", { name: "追加", exact: true })).toBeDisabled();

  await editor.getByLabel("緯度").fill("32.389063");
  await editor.getByLabel("経度").fill("131.597572");
  const linkedSection = editor.getByLabel("関連Leg");
  await expect(linkedSection).toBeVisible();
  await expect(linkedSection.locator("option")).toHaveCount(3);
  await editor.locator("select option").first().evaluate((option) => {
    option.textContent =
      "Leg 2: 変針点 UMK(MZE 004/6.2,NHT6.0) → 変針点 OMARU(NHT 171/11.4)（CRUISE）";
  });
  for (const width of [1100, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    const bounds = await routePanel.evaluate((element) => {
      const editor = element.querySelector(".checkpoint-editor")?.getBoundingClientRect();
      const map = element.querySelector("#route-map-frame")?.getBoundingClientRect();
      const routeTable = element.querySelector(".route-table-scroll")?.getBoundingClientRect();
      const form = element.querySelector(".checkpoint-form")?.getBoundingClientRect();
      const select = element.querySelector(".checkpoint-form select")?.getBoundingClientRect();
      const actions = element.querySelector(".checkpoint-form-actions")?.getBoundingClientRect();
      return editor && map && routeTable && form && select && actions
        ? {
            editorTop: editor.top,
            editorBottom: editor.bottom,
            mapBottom: map.bottom,
            routeTableTop: routeTable.top,
            formLeft: form.left,
            formRight: form.right,
            selectLeft: select.left,
            selectRight: select.right,
            actionsLeft: actions.left,
            actionsRight: actions.right,
          }
        : null;
    });
    expect(bounds, `${width}pxでチェックポイントフォームが表示されること`).not.toBeNull();
    expect(bounds!.editorTop).toBeGreaterThanOrEqual(bounds!.mapBottom);
    expect(bounds!.editorBottom).toBeLessThanOrEqual(bounds!.routeTableTop);
    expect(bounds!.selectLeft).toBeGreaterThanOrEqual(bounds!.formLeft);
    expect(bounds!.selectRight).toBeLessThanOrEqual(bounds!.formRight);
    expect(bounds!.actionsLeft).toBeGreaterThanOrEqual(bounds!.formLeft);
    expect(bounds!.actionsRight).toBeLessThanOrEqual(bounds!.formRight);
  }
  await editor.getByLabel("緯度").fill("32.482176");
  await editor.getByLabel("経度").fill("131.517485");
  await expect(editor.getByLabel("関連Leg")).toHaveCount(0);
  await expect(editor.getByText(/関連Legがありません/)).toHaveCount(0);
  await editor.getByLabel("名称").fill("訓練CP");
  await expect(nameWarning).toHaveCount(0);
  await expect(editor.getByLabel("名称")).not.toHaveAttribute("aria-invalid");
  const addCheckPoint = editor.getByRole("button", { name: "追加", exact: true });
  await expect(addCheckPoint).toBeEnabled();
  await addCheckPoint.click();
  await expect(routePanel.getByText("訓練CP", { exact: true })).toBeVisible();
  const confirmedMarker = routePanel.locator(".checkpoint-confirmed-marker");
  await expect(confirmedMarker).toHaveCount(1);
  expect(await confirmedMarker.getAttribute("stroke")).toBe("#9b5b13");
  expect(await confirmedMarker.getAttribute("stroke")).not.toBe(draftMarkerStroke);
  await expect(editor.getByText(/Leg内 .* NM \/ 累積 .* NM \/ 横ずれ .* NM/)).toBeVisible();
  const checkpointState = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    return await response.json() as WebState;
  });
  const expectedAutomaticSection = checkpointState.project?.sections.find(
    (section) => section.sequence === 1,
  );
  expect(expectedAutomaticSection).toBeDefined();
  expect(checkpointState.project?.visual_references[0]?.linked_section_id).toBe(
    expectedAutomaticSection!.id,
  );

  await editor.getByRole("button", { name: "訓練CPを編集" }).click();
  await editor.getByLabel("名称").fill("訓練CP改");
  await editor.getByRole("button", { name: "保存", exact: true }).click();
  await expect(editor.getByText("訓練CP改", { exact: true })).toBeVisible();

  page.once("dialog", (dialog) => void dialog.accept());
  await editor.getByRole("button", { name: "訓練CP改を削除" }).click();
  await expect(editor.getByText("まだチェックポイントはありません。")).toBeVisible();
  expect(runtimeErrors).toEqual([]);
});

test("changed ALT appears in PA with lesson display precision", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("/");
  await calculateNavLog(page);

  await expect(
    page.getByLabel("ALT・Phase・FUEL・VAR・TGLを原資料と照合しました"),
  ).toHaveCount(0);
  const calculate = page.getByRole("button", { name: "NAV LOGを再計算" });
  await page.locator(".table-number-input").first().fill("5500");

  await expect(calculate).toBeEnabled();
  await calculate.click();

  const altitudeInput = page.locator(".nav-log-table").getByLabel(/計画高度$/).first();
  await expect(altitudeInput).toHaveValue("5500");
  const firstParent = page.locator(".nav-log-table .nav-leg-heading-row").first();
  const vorSelect = page.getByLabel("VOR基準局");
  await expect(vorSelect.locator("option")).toHaveCount(32);
  const orderedVorIdentifiers = await vorSelect.locator("option").evaluateAll((options) => (
    options.slice(1).map((option) => (option as HTMLOptionElement).value)
  ));
  expect(orderedVorIdentifiers.slice(0, 7)).toEqual([
    "MZE", "TFE", "KGE", "HKC", "KUE", "SWE", "UBE",
  ]);
  expect(orderedVorIdentifiers.slice(7)).toEqual(
    [...orderedVorIdentifiers.slice(7)].sort((left, right) => left.localeCompare(right)),
  );
  await expect(vorSelect).toHaveValue("__AUTO__");
  await expect(vorSelect.locator("option:checked")).toHaveText("自動 MZE");
  await expect(vorSelect).toHaveAttribute("title", /局からTOへのradial・距離/);
  const vorState = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    return await response.json() as WebState;
  });
  const firstParentProjection = vorState.outcome?.display_rows.find(
    (row) => row.row_type === "PHYSICAL_LEG_SUMMARY",
  );
  if (firstParentProjection === undefined) throw new Error("parent NAV LOG row is missing");
  expect(firstParentProjection.to_latitude_deg).toBeCloseTo(32.4, 6);
  expect(firstParentProjection.to_longitude_deg).toBeCloseTo(131.5, 6);
  const automaticVorText = await firstParent.locator("td").nth(0).textContent();
  expect(automaticVorText).toBe("012 / 31.4");
  expect(automaticVorText).not.toBe("105 / 0.6");
  expect(automaticVorText).not.toMatch(/°|NM/);
  await vorSelect.selectOption("HKC");
  await expect(vorSelect).toHaveValue("HKC");
  const manualVorText = await firstParent.locator("td").nth(0).textContent();
  expect(manualVorText).toBe("055 / 62.9");
  expect(manualVorText).not.toBe(automaticVorText);
  await expect(firstParent.locator(".vor-reference-cell")).toHaveAttribute(
    "title",
    "HKCからTOへのradial / 距離（表示専用セル）",
  );
  const destinationProjection = vorState.outcome?.display_rows.find(
    (row) => row.row_type === "DESTINATION_INFO",
  );
  if (destinationProjection === undefined) throw new Error("destination NAV LOG row is missing");
  expect(destinationProjection.to_latitude_deg).toBeCloseTo(33.4794444444, 6);
  expect(destinationProjection.to_longitude_deg).toBeCloseTo(131.7372222222, 6);
  await expect(page.locator(".nav-destination-info-row .vor-reference-cell")).toHaveText(
    "035 / 121.7",
  );
  await expect(firstParent.locator("td").nth(7)).toHaveText(/^\d{3}$/);
  await expect(firstParent.locator("td").nth(8)).toHaveText("+7");
  await expect(firstParent.locator("td").nth(9)).toHaveText(/^\d{3}$/);
  const course = await firstParent.locator("td").nth(7).textContent();
  const variation = await firstParent.locator("td").nth(8).textContent();
  const magneticCourse = await firstParent.locator("td").nth(9).textContent();
  expect((Number(course) + Number(variation) + 360) % 360).toBe(Number(magneticCourse));
  await expect(firstParent.locator("td").nth(13)).toHaveText(
    /^\d+\.[05] \/ \d+\.[05]$/,
  );
  await expect(firstParent.locator("td").nth(15)).toHaveText(
    /^\d+\.[05] \/ \d+\.[05]$/,
  );
  await expect(firstParent.locator("td").nth(19)).toHaveText(
    /^\d+\.\d \/ \d+\.\d$/,
  );
  const firstDetail = page.locator(".nav-log-table .nav-leg-detail-row").first();
  await expect(firstDetail.locator("td").nth(13)).toHaveText(/^\d+\.[05]$/);
  await expect(firstDetail.locator("td").nth(15)).toHaveText(/^\d+\.[05]$/);
  const displayedWca = await firstDetail.locator("td").nth(11).textContent();
  const displayedHeading = await firstDetail.locator("td").nth(12).textContent();
  expect((Number(magneticCourse) + Number(displayedWca) + 360) % 360).toBe(
    Number(displayedHeading),
  );
  await expect(page.getByText("DESCENT_END", { exact: true })).toHaveCount(0);
  await expect(page.locator(".nav-leg-heading-row")).not.toHaveCount(0);

  const fuelTable = page.locator(".fuel-plan-table");
  await expect(fuelTable.locator("col")).toHaveCount(5);
  await expect(fuelTable.getByText("TAXI・RUN UP", { exact: true })).toBeVisible();
  await expect(fuelTable.getByText("MIN REQUIRED", { exact: true })).toBeVisible();
  await expect(fuelTable.locator("tbody tr")).toHaveCount(10);
  const tableLayout = await page.locator(".nav-log-tables").evaluate((container) => {
    const navTable = container.querySelector<HTMLElement>(".nav-log-table");
    const planTable = container.querySelector<HTMLElement>(".fuel-plan-table");
    if (navTable === null || planTable === null) throw new Error("NAV LOG tables are missing");
    return {
      gap: planTable.offsetLeft - (navTable.offsetLeft + navTable.offsetWidth),
      fuelWidth: planTable.offsetWidth,
      navWidth: navTable.offsetWidth,
      fuelRowHeight: planTable.querySelector<HTMLElement>("tbody tr")?.offsetHeight ?? 0,
      fuelAmountAlignment: getComputedStyle(
        planTable.querySelector<HTMLElement>(".fuel-amount")!,
      ).justifyContent,
    };
  });
  expect(tableLayout.gap).toBeGreaterThanOrEqual(11);
  expect(tableLayout.gap).toBeLessThanOrEqual(13);
  expect(tableLayout.navWidth).toBeLessThanOrEqual(1800);
  expect(tableLayout.fuelWidth).toBeLessThanOrEqual(430);
  expect(tableLayout.fuelWidth).toBeLessThan(tableLayout.navWidth / 2);
  expect(tableLayout.fuelRowHeight).toBeLessThanOrEqual(25);
  expect(tableLayout.fuelAmountAlignment).toBe("center");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.locator(".destination-row").getByLabel("今回採用する場周経路高度"),
  ).toBeVisible();
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(mobileOverflow).toBeLessThanOrEqual(1);

  const unexpectedConsoleErrors = consoleErrors.filter(
    (message) => !message.includes("401 (Unauthorized)"),
  );
  expect(unexpectedConsoleErrors).toEqual([]);
});

test("VOR/DME reference columns can be added, configured independently, and removed", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.goto("/");
  await calculateNavLog(page);

  const table = page.locator(".nav-log-table");
  const scroll = page.locator(".nav-log-scroll");
  const firstRow = table.locator(".nav-leg-heading-row").first();
  const initialWidth = (await table.boundingBox())?.width ?? 0;
  const vorState = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    return await response.json() as WebState;
  });
  const firstRowProjection = vorState.outcome?.display_rows.find(
    (row) => row.row_type === "PHYSICAL_LEG_SUMMARY",
  );
  if (firstRowProjection === undefined) throw new Error("parent NAV LOG row is missing");
  expect(firstRowProjection.to_latitude_deg).toBeCloseTo(32.4, 6);
  expect(firstRowProjection.to_longitude_deg).toBeCloseTo(131.5, 6);

  await expect(page.getByLabel("VOR基準局")).toHaveCount(1);
  await expect(firstRow.locator(".vor-reference-cell")).toHaveCount(1);
  await page.getByRole("button", { name: "VOR/DME列を追加" }).click();

  await expect(page.getByLabel(/^VOR基準局/)).toHaveCount(2);
  await expect(firstRow.locator(".vor-reference-cell")).toHaveCount(2);
  const addedVor = page.getByLabel("VOR基準局", { exact: true });
  const originalVor = page.getByLabel("VOR基準局 2");
  await expect(addedVor).toHaveValue("");
  await expect(originalVor).toHaveValue("__AUTO__");
  await expect(firstRow.locator(".vor-reference-cell").nth(0)).toHaveText("—");
  await expect(firstRow.locator(".vor-reference-cell").nth(1)).toHaveText(
    "012 / 31.4",
  );

  await addedVor.selectOption("HKC");
  await expect(firstRow.locator(".vor-reference-cell").nth(0)).toHaveText(
    "055 / 62.9",
  );
  expect(await firstRow.locator(".vor-reference-cell").nth(0).textContent()).not.toBe(
    await firstRow.locator(".vor-reference-cell").nth(1).textContent(),
  );
  await expect(firstRow.locator(".route-from-cell")).toHaveText("RJFM");
  expect((await table.boundingBox())?.width ?? 0).toBeGreaterThanOrEqual(initialWidth + 95);
  expect(await scroll.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(firstRow.locator(".vor-reference-cell")).toHaveCount(2);
  await expect(firstRow.locator(".route-from-cell")).toHaveText("RJFM");
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1);

  await page.getByRole("button", { name: "VOR/DME 1列目を削除" }).click();
  await expect(page.getByLabel(/^VOR基準局/)).toHaveCount(1);
  await expect(page.getByLabel("VOR基準局", { exact: true })).toHaveValue("__AUTO__");
  await expect(firstRow.locator(".vor-reference-cell")).toHaveCount(1);
  await expect(page.getByRole("button", { name: /VOR\/DME .*列目を削除/ })).toHaveCount(0);
});

test("climb and descent legs show magnetic-course altitude candidates", async ({ page }) => {
  await page.goto("/");

  const openPaste = page.getByRole("button", { name: "KMLを貼り付け" });
  await openPaste.click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(kml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  const phaseSelects = page.getByLabel(/出発LegのPhase/);
  await page.getByRole("button", { name: "変更", exact: true }).click();
  await expect(phaseSelects.nth(0)).toBeEnabled();
  await phaseSelects.nth(0).selectOption("CLIMB");
  await phaseSelects.nth(2).selectOption("DESCENT");

  const climbCandidate = page.getByLabel(/出発Legの上昇先の巡航高度候補/);
  const descentCandidate = page.getByLabel(/出発Legの降下開始時の巡航高度候補/);
  await expect(climbCandidate).toBeVisible();
  await expect(descentCandidate).toBeVisible();
  await expect(climbCandidate.locator("option")).not.toHaveCount(0);
  await expect(descentCandidate.locator("option")).not.toHaveCount(0);
  await expect(
    page.getByText(/上昇のALTは上昇先、降下のALTは降下開始時の巡航高度/),
  ).toBeVisible();

  const climbAltitude = await climbCandidate.locator("option").first().getAttribute("value");
  const descentAltitude = await descentCandidate.locator("option").first().getAttribute("value");
  if (climbAltitude === null || descentAltitude === null) {
    throw new Error("Climb or descent altitude candidate is missing");
  }
  await climbCandidate.selectOption(climbAltitude);
  await descentCandidate.selectOption(descentAltitude);
  const descentRow = descentCandidate.locator("xpath=ancestor::tr");
  const descentInput = descentRow.getByLabel(/出発Legの計画高度/);

  await descentInput.fill("3000");
  await expect(descentCandidate).toHaveValue("custom");
  await expect(descentRow).not.toHaveClass(/altitude-warning-row/);
  await expect(descentRow).not.toContainText("候補外（警告）");

  await descentInput.fill("3100");
  await expect(descentCandidate).toHaveValue("custom");
  await expect(descentRow).toHaveClass(/altitude-warning-row/);
  await expect(descentRow).toContainText("候補外（警告）");
  await expect(descentInput).toHaveCSS("border-top-color", "rgb(168, 102, 13)");

  await descentCandidate.selectOption(descentAltitude);
  await expect(descentRow).not.toHaveClass(/altitude-warning-row/);
});

test("NAV LOG safe inputs validate and recalculate automatically", async ({ page }) => {
  await page.goto("/");
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await calculateNavLog(page);

  const recalculationRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/project/recalculate")) {
      recalculationRequests.push(request.url());
    }
  });

  const altitude = page.locator(".nav-log-table").getByLabel(/計画高度$/).first();
  const altitudeResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/project/recalculate") && response.ok(),
  );
  await altitude.fill("5500");
  await altitudeResponse;
  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  const manualRecalculation = page.waitForResponse(
    (response) => response.url().endsWith("/api/calculation-jobs") && response.ok(),
  );
  await page.getByRole("button", { name: "NAV LOGを再計算" }).click();
  await manualRecalculation;
  await expect(page.getByText("NAV LOGを計算しました。準備状況と各値を確認してください。", { exact: true })).toBeVisible();
  await expect(altitude).toHaveValue("5500");

  const windDirection = page.locator(".nav-log-table").getByLabel(/手動風向$/).first();
  const windSpeed = page.locator(".nav-log-table").getByLabel(/手動風速$/).first();
  const requestCount = recalculationRequests.length;
  await windDirection.fill("270");
  await expect(page.getByText(/入力を確認してください。直前の正常な計算結果/)).toBeVisible();
  await expect(windDirection).toHaveAttribute("aria-invalid", "true");
  await page.waitForTimeout(850);
  expect(recalculationRequests).toHaveLength(requestCount);
  const saveResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/projects/save") && response.ok(),
  );
  await page.getByLabel("プロジェクト").fill("訓練航法 8月");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const savedResponse = await saveResponse;
  expect((await savedResponse.request().postDataJSON()).name).toBe("訓練航法 8月");
  await expect(page.getByLabel("プロジェクト")).toHaveValue("訓練航法 8月");
  await expect(page.locator("#saved-project")).toContainText("訓練航法 8月");
  await expect(windDirection).toHaveValue("270");
  await expect(windDirection).toHaveAttribute("aria-invalid", "true");

  const windResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/project/recalculate") && response.ok(),
  );
  await windSpeed.fill("15");
  await windResponse;
  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  await expect(windDirection).toHaveAttribute("aria-invalid", "false");
  const splitRows = page.locator(".nav-log-table .nav-leg-detail-row");
  await expect(splitRows.first().getByLabel(/手動風向$/)).toHaveAttribute("aria-label", /→RCA 手動風向$/);
  await expect(splitRows.nth(1).getByLabel(/手動風向$/)).toHaveAttribute("aria-label", /^RCA→/);
  const cruiseWindDirection = splitRows.nth(1).getByLabel(/手動風向$/);
  const cruiseWindSpeed = splitRows.nth(1).getByLabel(/手動風速$/);
  await cruiseWindDirection.fill("180");
  await expect(cruiseWindDirection).toHaveAttribute("aria-invalid", "true");
  const cruiseWindResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/project/recalculate") && response.ok(),
  );
  await cruiseWindSpeed.fill("20");
  const cruiseResponse = await cruiseWindResponse;
  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  await expect(windDirection).toHaveValue("270");
  await expect(windSpeed).toHaveValue("15");
  const payload = cruiseResponse.request().postDataJSON() as {
    sections: Array<Record<string, unknown>>;
  };
  expect(payload.sections[0]).toMatchObject({
    manual_wind_direction_deg: 270,
    manual_wind_speed_kt: 15,
    manual_wind_by_phase: { CRUISE: { direction_deg_from: 180, speed_kt: 20 } },
  });
  await expect(page.getByRole("heading", { name: "NAV LOG" })).toBeVisible();
  await expect(page.locator("body")).not.toBeEmpty();
  await expect(
    page.locator("[data-nextjs-dialog], .vite-error-overlay, #webpack-dev-server-client-overlay"),
  ).toHaveCount(0);
  const unexpectedConsoleErrors = consoleErrors.filter(
    (message) => !message.includes("401 (Unauthorized)"),
  );
  expect(unexpectedConsoleErrors).toEqual([]);
  await page.screenshot({ path: "/tmp/autonavlog-phase-wind.png", fullPage: false });
  await expect(page.locator(".nav-log-table th").nth(7)).toHaveText("TC");
  await expect(page.locator(".derived-readonly-cell").first()).toHaveAttribute("title", /表示専用セル/);
});

test("stale automatic recalculation cannot overwrite newer planning inputs", async ({ page }) => {
  await page.goto("/");
  await calculateNavLog(page);

  let releaseFirstResponse = () => {};
  let markFirstResponseReady = () => {};
  const firstResponseReady = new Promise<void>((resolve) => {
    markFirstResponseReady = resolve;
  });
  const firstResponseReleased = new Promise<void>((resolve) => {
    releaseFirstResponse = resolve;
  });
  const recalculationBodies: Array<Record<string, unknown>> = [];
  await page.route("**/api/project/recalculate", async (route) => {
    recalculationBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    if (recalculationBodies.length === 1) {
      const response = await route.fetch();
      markFirstResponseReady();
      await firstResponseReleased;
      await route.fulfill({ response });
      return;
    }
    await route.continue();
  });

  const altitude = page.locator(".nav-log-table").getByLabel(/計画高度$/).first();
  await altitude.fill("5500");
  await firstResponseReady;
  const fuel = page.getByLabel("FUEL gal");
  await fuel.fill("77");
  const latestResponse = page.waitForResponse((response) => {
    if (
      !response.url().endsWith("/api/project/recalculate") ||
      !response.ok()
    ) {
      return false;
    }
    const body = response.request().postDataJSON() as Record<string, unknown>;
    return body.total_usable_fuel_gal === 77;
  });
  releaseFirstResponse();
  await latestResponse;

  await expect(page.getByText("自動再計算しました。", { exact: true })).toBeVisible();
  await expect(fuel).toHaveValue("77");
  await expect(altitude).toHaveValue("5500");
  expect(recalculationBodies).toHaveLength(2);
  expect(recalculationBodies[1]?.total_usable_fuel_gal).toBe(77);
});

test("calculated mobile layout has no body overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();

  await calculateNavLog(page);

  await expect(page.getByRole("heading", { name: "準備状況" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "NAV LOG" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({
    path: path.join(repositoryRoot, "docs/web-design/implementation-mobile.png"),
    fullPage: true,
  });
});

test("KMZ document selection modal moves and traps focus", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();
  await page.locator("#route-file").setInputFiles({
    name: "multiple.kmz",
    mimeType: "application/vnd.google-earth.kmz",
    buffer: multiDocumentKmz,
  });

  const dialog = page.getByRole("dialog", { name: "KMZ内のKMLを選択" });
  const documentSelect = dialog.getByLabel("KML文書");
  await expect(dialog).toBeVisible();
  await expect(documentSelect).toBeFocused();
  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("Tab");
    await expect(dialog.locator(":focus")).toHaveCount(1);
  }

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("grouped LineStrings require an explicit route candidate selection", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();

  await importKmlCandidate(page);
  const candidateSelect = page.getByLabel("飛行経路候補");
  const departureSelect = page.getByLabel("FROM", { exact: true });
  await expect(departureSelect).toHaveAttribute("readonly", "");
  await expect(page.getByLabel("TO", { exact: true })).toHaveAttribute("readonly", "");
  await expect(candidateSelect).toHaveValue("line:0");
  await expect(page.locator(".candidate-control")).not.toHaveClass(/is-required/);
  await page.getByLabel("地図とKML記載順を確認しました").check();

  await page.getByRole("button", { name: "KMLを貼り付け" }).click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(twoConnectedRouteCandidatesKml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();

  await expect(candidateSelect).toHaveValue("");
  const candidateControl = page.locator(".candidate-control");
  await expect(candidateControl).toHaveClass(/is-required/);
  await expect(candidateControl.getByText("経路選択", { exact: true })).toBeVisible();
  await expect(candidateControl.getByText("選択必須", { exact: true })).toBeVisible();
  await expect(candidateControl).toContainText("使用する飛行経路を選択してください。");
  await expect(candidateSelect).toHaveAttribute("required", "");
  await expect(candidateSelect).toHaveAttribute("aria-invalid", "true");
  const intermediateCandidateBoxes = await Promise.all([
    candidateControl.boundingBox(),
    candidateSelect.boundingBox(),
  ]);
  if (!intermediateCandidateBoxes[0] || !intermediateCandidateBoxes[1]) {
    throw new Error("Required route selection is missing at intermediate width");
  }
  expect(
    intermediateCandidateBoxes[1].x + intermediateCandidateBoxes[1].width,
  ).toBeLessThanOrEqual(
    intermediateCandidateBoxes[0].x + intermediateCandidateBoxes[0].width + 1,
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(candidateControl).toBeVisible();
  const wideCandidateBoxes = await Promise.all([
    candidateControl.boundingBox(),
    candidateSelect.boundingBox(),
  ]);
  if (!wideCandidateBoxes[0] || !wideCandidateBoxes[1]) {
    throw new Error("Required route selection is missing at wide width");
  }
  expect(wideCandidateBoxes[1].x + wideCandidateBoxes[1].width).toBeLessThanOrEqual(
    wideCandidateBoxes[0].x + wideCandidateBoxes[0].width + 1,
  );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
  ).toBeLessThanOrEqual(1);
  await expect(candidateSelect.locator("option")).toHaveCount(3);
  await expect(candidateSelect.locator("option").nth(0)).toHaveText("経路を選択");
  await expect(candidateSelect.locator("option").nth(1)).toHaveText(
    /^RJFM→RJFO① · 3 Leg · \d+(?:\.\d+)? NM$/,
  );
  await expect(candidateSelect.locator("option").nth(2)).toHaveText(
    /^RJFO→RJFM① · 2 Leg · \d+(?:\.\d+)? NM$/,
  );
  await expect(departureSelect).toHaveValue("");
  await expect(page.getByLabel("TO")).toHaveValue("");
  await expect(page.getByText("飛行経路候補を選択すると地図へ表示します")).toBeVisible();
  await expect(page.locator(".leaflet-overlay-pane path.leaflet-interactive")).toHaveCount(0);
  await expect(page.getByLabel("経路確認")).toHaveCount(0);

  await candidateSelect.selectOption("connected_lines:0");
  await expect(candidateControl).not.toHaveClass(/is-required/);
  await expect(candidateControl.getByText("選択必須", { exact: true })).toHaveCount(0);
  await expect(candidateSelect).toHaveAttribute("aria-invalid", "false");
  await expect(departureSelect).toHaveValue(/^RJFM\b/);
  await expect(page.getByLabel("TO")).toHaveValue(/RJFO/);
  const routeWorkspace = page.getByLabel("経路地図とLeg設定");
  await expect(routeWorkspace.getByText("RJFM→RJFO①", { exact: true })).toBeVisible();
  await expect(routeWorkspace.getByText("4点の形状を確認中", { exact: true })).toBeVisible();
  await expect(page.locator(".leaflet-overlay-pane path.leaflet-interactive")).toHaveCount(1);

  const routeConfirmation = page.getByLabel("経路確認");
  const routeUseConfirmed = routeConfirmation.getByLabel("地図とKML記載順を確認しました");
  await routeUseConfirmed.check();
  await expect(routeUseConfirmed).toBeChecked();

  await candidateSelect.selectOption("");
  await expect(candidateControl).toHaveClass(/is-required/);
  await expect(candidateControl.getByText("選択必須", { exact: true })).toBeVisible();
  await expect(departureSelect).toHaveValue("");
  await expect(page.getByLabel("TO")).toHaveValue("");
  await expect(page.getByText("飛行経路候補を選択すると地図へ表示します")).toBeVisible();
  await expect(page.getByLabel("経路確認")).toHaveCount(0);
  await candidateSelect.selectOption("connected_lines:1");
  await expect(routeUseConfirmed).not.toBeChecked();
  await expect(departureSelect).toHaveValue(/^RJFO\b/);
  await expect(page.getByLabel("TO")).toHaveValue(/RJFM/);
  await expect(routeWorkspace.getByText("RJFO→RJFM①", { exact: true })).toBeVisible();

  await routeUseConfirmed.check();
  const confirmRequest = page.waitForRequest(
    (request) => request.url().endsWith("/api/route/confirm") && request.method() === "POST",
  );
  await routeConfirmation.getByRole("button", { name: "経路を確定" }).click();
  const confirmPayload = (await confirmRequest).postDataJSON() as Record<string, unknown>;
  expect(confirmPayload).toMatchObject({
    candidate_kind: "connected_lines",
    candidate_index: 1,
    route_use_confirmed: true,
    polygon_route_confirmed: false,
  });
  await expect(routeWorkspace.getByText("RJFO → RJFM", { exact: true })).toBeVisible();
});

test("same-named grouped routes remain distinguishable in the selector", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();

  await page.getByRole("button", { name: "KMLを貼り付け" }).click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(sameNamedConnectedRouteCandidatesKml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();

  const candidateSelect = page.getByLabel("飛行経路候補");
  await expect(candidateSelect).toHaveValue("");
  const options = candidateSelect.locator("option");
  await expect(options).toHaveCount(3);
  await expect(options.nth(1)).toContainText(
    "Duplicate routes / Outbound / Route（候補1） · 2 Leg",
  );
  await expect(options.nth(2)).toContainText(
    "Duplicate routes / Inbound / Route（候補2） · 2 Leg",
  );
  expect(await options.nth(1).textContent()).not.toBe(await options.nth(2).textContent());
});

test("file picker, drop, and KMZ use the same grouped-route candidate flow", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();

  const candidateSelect = page.getByLabel("飛行経路候補");
  const groupedRouteFile = {
    name: "grouped-routes.kml",
    mimeType: "application/vnd.google-earth.kml+xml",
    buffer: Buffer.from(twoConnectedRouteCandidatesKml),
  };
  await page.locator("#route-file").setInputFiles(groupedRouteFile);
  await expect(candidateSelect).toHaveValue("");
  await expect(candidateSelect.locator("option")).toHaveCount(3);

  await candidateSelect.selectOption("connected_lines:0");
  await expect(page.getByLabel("FROM")).toHaveValue(/^RJFM\b/);
  await expect(page.getByLabel("TO")).toHaveValue(/RJFO/);

  const dataTransfer = await page.evaluateHandle((kmlText) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(
      [kmlText],
      "dropped-grouped-routes.kml",
      { type: "application/vnd.google-earth.kml+xml" },
    ));
    return transfer;
  }, twoConnectedRouteCandidatesKml);
  await page.locator(".drop-zone").dispatchEvent("drop", { dataTransfer });
  await dataTransfer.dispose();

  await expect(candidateSelect).toHaveValue("");
  await expect(candidateSelect.locator("option")).toHaveCount(3);
  await expect(page.getByLabel("FROM")).toHaveValue("");
  await expect(page.getByLabel("TO")).toHaveValue("");

  await page.locator("#route-file").setInputFiles({
    name: "grouped-routes.kmz",
    mimeType: "application/vnd.google-earth.kmz",
    buffer: twoConnectedRouteCandidatesKmz,
  });
  await expect(candidateSelect).toHaveValue("");
  await expect(candidateSelect.locator("option")).toHaveCount(3);
  await expect(candidateSelect.locator("option").nth(1)).toContainText(
    "RJFM→RJFO① · 3 Leg",
  );
});

test("KML endpoints automatically determine read-only FROM and TO", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();

  await page.getByRole("button", { name: "KMLを貼り付け" }).click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(kmlFromRjfk);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();

  const departure = page.getByLabel("FROM");
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await expect(departure).toHaveValue(/^RJFK\b/);
  await expect(page.getByLabel("TO")).toHaveValue(/RJFO/);
  await expect(departure).toHaveAttribute("readonly", "");
  await expect(page.getByLabel("TO")).toHaveAttribute("readonly", "");
});

test("RUN UP, nose fairing, and A/C choices persist after save and reload", async ({ page }) => {
  await page.goto("/");
  await importKmlCandidate(page);

  const runUp = page.getByLabel("RUN UP あり");
  const noseFairing = page.getByLabel("ノーズフェアリングあり (OFF: -10 kt)");
  const airConditioning = page.getByLabel("A/C ON (巡航速度 -2 kt)");
  await expect(runUp).toBeChecked();
  await expect(noseFairing).not.toBeChecked();
  await expect(airConditioning).toBeChecked();

  for (const width of [1100, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const [runUpBox, noseFairingBox, airConditioningBox] = await Promise.all([
      runUp.boundingBox(),
      noseFairing.boundingBox(),
      airConditioning.boundingBox(),
    ]);
    if (!runUpBox || !noseFairingBox || !airConditioningBox) {
      throw new Error(`Fuel option layout is missing at ${width}px`);
    }
    expect(noseFairingBox.y).toBeGreaterThanOrEqual(runUpBox.y + runUpBox.height - 1);
    expect(airConditioningBox.x).toBeGreaterThanOrEqual(runUpBox.x + runUpBox.width - 1);
    expect(Math.abs(airConditioningBox.y - runUpBox.y)).toBeLessThanOrEqual(1);
  }

  await runUp.uncheck();
  await noseFairing.check();
  await airConditioning.uncheck();
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  const state = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    return await response.json() as WebState;
  });
  expect(state.project?.run_up_included).toBe(false);
  expect(state.project?.nose_fairing_enabled).toBe(true);
  expect(state.project?.air_conditioning_enabled).toBe(false);

  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("Projectをローカルへ保存しました。", { exact: true })).toBeVisible();
  await page.reload();
  await expect(runUp).not.toBeChecked();
  await expect(noseFairing).toBeChecked();
  await expect(airConditioning).not.toBeChecked();
});

test("500 and 1000 fpm descent rates recalculate, render, and persist", async ({ page }) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.goto("/");

  const rateGroup = page.getByRole("group", { name: "計画降下率" });
  const standardRate = page.getByRole("radio", { name: /500 fpm/ });
  const fastRate = page.getByRole("radio", { name: /1000 fpm/ });
  await expect(rateGroup).toBeVisible();
  await expect(standardRate).toBeChecked();
  await expect(fastRate).not.toBeChecked();
  await expect(page.getByText("標準計画値は500 fpmです。", { exact: true })).toHaveCount(0);

  const assertWorkflowLayout = async (width: 1100 | 1440) => {
    await page.setViewportSize({ width, height: 900 });
    const [inputBox, routeBox, statusBox, groupBox] = await Promise.all([
      page.locator(".input-rail").boundingBox(),
      page.locator(".route-workspace").boundingBox(),
      page.locator(".status-rail").boundingBox(),
      rateGroup.boundingBox(),
    ]);
    if (!inputBox || !routeBox || !statusBox || !groupBox) {
      throw new Error(`Descent-rate workflow layout is missing at ${width}px`);
    }
    expect(groupBox.x).toBeGreaterThanOrEqual(inputBox.x);
    expect(groupBox.x + groupBox.width).toBeLessThanOrEqual(inputBox.x + inputBox.width);
    if (width === 1100) {
      expect(routeBox.y).toBeGreaterThanOrEqual(inputBox.y + inputBox.height - 1);
      expect(statusBox.y).toBeGreaterThanOrEqual(routeBox.y + routeBox.height - 1);
    } else {
      expect(routeBox.x).toBeGreaterThanOrEqual(inputBox.x + inputBox.width - 1);
      expect(statusBox.x).toBeGreaterThanOrEqual(routeBox.x + routeBox.width - 1);
      expect(Math.abs(routeBox.y - inputBox.y)).toBeLessThanOrEqual(1);
      expect(Math.abs(statusBox.y - inputBox.y)).toBeLessThanOrEqual(1);
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
    ).toBeLessThanOrEqual(1);
  };

  await assertWorkflowLayout(1100);
  await assertWorkflowLayout(1440);
  await page.setViewportSize({ width: 1100, height: 900 });
  await fastRate.check();
  await expect(fastRate).toBeChecked();
  await expect(standardRate).not.toBeChecked();
  await expect(page.getByText("標準計画値は500 fpmです。", { exact: true })).toBeVisible();
  await page.getByLabel("気象モード").selectOption("FTD");

  await calculateNavLog(page, false);
  await expect(page.getByText("計算結果 1000 fpm", { exact: true })).toBeVisible();
  await standardRate.check();
  await expect(page.getByText("計算結果 1000 fpm", { exact: true })).toBeVisible();
  await expect(page.getByText("計算結果 500 fpm", { exact: true })).toHaveCount(0);
  await fastRate.check();
  const calculatedState = await page.evaluate(async () => {
    const response = await fetch("/api/state");
    return await response.json() as WebState;
  });
  expect(calculatedState.project?.descent_rate_fpm).toBe(1000);
  const calculatedDescent = calculatedState.outcome?.sections.find(
    (section) => section.phase === "DESCENT",
  );
  expect(calculatedDescent?.performance_metadata.descent_rate_fpm).toBe(1000);

  const [statusBox, navLogBox] = await Promise.all([
    page.locator(".status-rail").boundingBox(),
    page.getByLabel("計算済みNAV LOG").boundingBox(),
  ]);
  if (!statusBox || !navLogBox) {
    throw new Error("Calculated workflow regions are missing at 1100px");
  }
  expect(navLogBox.y).toBeGreaterThanOrEqual(statusBox.y + statusBox.height - 1);

  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("Projectをローカルへ保存しました。", { exact: true })).toBeVisible();
  await page.reload();
  await expect(fastRate).toBeChecked();
  await expect(page.getByText("計算結果 1000 fpm", { exact: true })).toBeVisible();
  expect(pageErrors).toEqual([]);
  expect(
    consoleErrors.filter((message) => !message.includes("401 (Unauthorized)")),
  ).toEqual([]);
});
