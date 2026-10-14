import { expect, test } from "@playwright/test";
import {
  formatVorRadialDistance, nearestVorStation, orderVorStationsForRoute, VOR_STATIONS,
  VOR_DATASET_EFFECTIVE_CYCLE, wgs84Inverse,
} from "../src/vorRadial";

// JCAB AIP 2026-08-06 cycle, RJDA__20251201.pdf p.6, AD 2.19.
const amakusa = { latitude_deg: 32 + 28 / 60 + 48.85 / 3600, longitude_deg: 130 + 9 / 60 + 39.48 / 3600 };
const miyazaki = { latitude_deg: 31.8772222222, longitude_deg: 131.4486111111 };

test("AKE is present once with AIP coordinates and station declination provenance", () => {
  const matches = VOR_STATIONS.filter(station => station.identifier === "AKE");
  expect(matches).toHaveLength(1);
  expect(matches[0]).toMatchObject({
    name: "AMAKUSA", type: "VOR/DME", frequency_mhz: 113.45,
    variation_west_deg: 7, variation_source: "AD 2.19 VOR declination",
    source_file: "RJDA__20251201.pdf", source_page: 6,
  });
  expect(matches[0]!.latitude_deg).toBeCloseTo(amakusa.latitude_deg, 10);
  expect(matches[0]!.longitude_deg).toBeCloseTo(amakusa.longitude_deg, 10);
});

test("AKE participates in the existing nearest and route-priority selection", () => {
  // A point near the airport, distinct from the station's own coordinates.
  const nearAmakusa = { latitude_deg: 32.49, longitude_deg: 130.17 };
  expect(nearestVorStation(nearAmakusa.latitude_deg, nearAmakusa.longitude_deg).identifier).toBe("AKE");
  expect(nearestVorStation(miyazaki.latitude_deg, miyazaki.longitude_deg).identifier).toBe("MZE");
  expect(orderVorStationsForRoute([nearAmakusa, miyazaki]).slice(0, 2).map(station => station.identifier))
    .toEqual(["AKE", "MZE"]);
  expect(orderVorStationsForRoute([miyazaki, nearAmakusa]).slice(0, 2).map(station => station.identifier))
    .toEqual(["MZE", "AKE"]);
});

test("AKE radial and distance use station declination and TO coordinates", () => {
  const station = VOR_STATIONS.find(candidate => candidate.identifier === "AKE");
  expect(station).toBeDefined();
  // Independent GeographicLib WGS84 inverse: true bearing 93.6825310448 deg,
  // distance 68.1611544251 NM. Add the AIP station declination of 7 deg west.
  expect(formatVorRadialDistance(station!, 32.4, 131.5)).toBe("101 / 68.2");
  expect(formatVorRadialDistance(station!, amakusa.latitude_deg, amakusa.longitude_deg)).toBe("— / 0.0");
  expect(formatVorRadialDistance(station!, null, null)).toBe("— / —");
});

// JCAB AIP ENR_20261001.pdf p.541 / ENR 4.1-21, EFF 1 OCT 2026.
// VOR and TACAN are separate rows with distinct antenna coordinates.
const shimizuVor = {
  latitude_deg: 32 + 45 / 60 + 21.49 / 3600,
  longitude_deg: 132 + 59 / 60 + 47.93 / 3600,
};

test("SUC uses the VOR row and station declination from ENR 4.1", () => {
  const matches = VOR_STATIONS.filter(station => station.identifier === "SUC");
  expect(matches).toHaveLength(1);
  expect(matches[0]).toMatchObject({
    name: "SHIMIZU", type: "VORTAC", frequency_mhz: 115.2,
    variation_west_deg: 7, variation_source: "ENR 4.1 VOR declination (7°W/2020)",
    source_file: "ENR_20261001.pdf", source_page: 541,
    effective_cycle: "2026-10-01", source_effective_date: "2026-10-01",
    source_section: "ENR 4.1-21",
    source_sha256: "5f30504f557ab31bbac5a1d0369da6e71b58cd6abc1af938f70a0100df87331a",
  });
  expect(matches[0]!.latitude_deg).toBeCloseTo(shimizuVor.latitude_deg, 10);
  expect(matches[0]!.longitude_deg).toBeCloseTo(shimizuVor.longitude_deg, 10);
  expect(matches[0]!.latitude_deg).not.toBeCloseTo(32 + 45 / 60 + 21.47 / 3600, 6);
  expect(matches[0]!.longitude_deg).not.toBeCloseTo(132 + 59 / 60 + 45.05 / 3600, 6);
  expect(VOR_DATASET_EFFECTIVE_CYCLE).toBe("2026-08-06");
  expect(VOR_STATIONS.filter(station => station.identifier !== "SUC")
    .every(station => station.effective_cycle === undefined)).toBe(true);
});

test("SUC participates in nearest and route-priority selection within the catalog radius", () => {
  const nearShimizu = { latitude_deg: 32.76, longitude_deg: 133.0 };
  expect(nearestVorStation(nearShimizu.latitude_deg, nearShimizu.longitude_deg).identifier).toBe("SUC");
  expect(orderVorStationsForRoute([nearShimizu, miyazaki]).slice(0, 2).map(station => station.identifier))
    .toEqual(["SUC", "MZE"]);
  expect(orderVorStationsForRoute([miyazaki, nearShimizu]).slice(0, 2).map(station => station.identifier))
    .toEqual(["MZE", "SUC"]);
  const distance = wgs84Inverse(
    shimizuVor.latitude_deg, shimizuVor.longitude_deg, miyazaki.latitude_deg, miyazaki.longitude_deg,
  ).distanceNm;
  expect(distance * 1.852).toBeLessThan(540);
});

test("SUC radial and distance match independent WGS84 inverses to TO", () => {
  const station = VOR_STATIONS.find(candidate => candidate.identifier === "SUC")!;
  // GeographicLib: 254.7132192393 deg true / 78.8154874550 NM; add 7 deg west.
  expect(formatVorRadialDistance(station, 32.4, 131.5)).toBe("262 / 78.8");
  // Destination: 304.6618516297 deg true / 76.8436658445 NM.
  expect(formatVorRadialDistance(station, 33.4794444444, 131.7372222222)).toBe("312 / 76.8");
  expect(formatVorRadialDistance(station, shimizuVor.latitude_deg, shimizuVor.longitude_deg)).toBe("— / 0.0");
  expect(formatVorRadialDistance(station, null, null)).toBe("— / —");
});
