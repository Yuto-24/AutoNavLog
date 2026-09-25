import { expect, test } from "@playwright/test";
import {
  formatVorRadialDistance, nearestVorStation, orderVorStationsForRoute, VOR_STATIONS,
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
