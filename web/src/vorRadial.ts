import vorStationData from "./data/vorStations.json";

const METERS_PER_NM = 1852;
const WGS84_A_METERS = 6378137;
const WGS84_FLATTENING = 1 / 298.257223563;
const WGS84_B_METERS = (1 - WGS84_FLATTENING) * WGS84_A_METERS;
const MEAN_EARTH_RADIUS_NM = 6371008.8 / METERS_PER_NM;
const AUTO_SELECTION_VALUE = "__AUTO__";
const ROUTE_NEARBY_STATION_LIMIT = 5;
const SEGMENT_ENDPOINT_MARGIN_NM = 1 / METERS_PER_NM;

export type VorStationType = "VOR" | "VOR/DME" | "VORTAC";

export interface VorStation {
  identifier: string;
  name: string;
  type: VorStationType;
  frequency_mhz: number;
  latitude_deg: number;
  longitude_deg: number;
  variation_west_deg: number;
  variation_source: string;
  source_file: string;
  source_page: number;
}

export interface VorRoutePoint {
  latitude_deg: number;
  longitude_deg: number;
}

interface VorStationDataset {
  dataset_id: string;
  effective_cycle: string;
  origin: {
    identifier: string;
    latitude_deg: number;
    longitude_deg: number;
  };
  radius_km: number;
  stations: VorStation[];
}

const dataset = vorStationData as VorStationDataset;

export const VOR_AUTO_SELECTION = AUTO_SELECTION_VALUE;
export const VOR_DATASET_ID = dataset.dataset_id;
export const VOR_DATASET_EFFECTIVE_CYCLE = dataset.effective_cycle;
export const VOR_STATIONS = dataset.stations;

function defaultVorStation(): VorStation {
  const station = VOR_STATIONS.find((candidate) => candidate.identifier === "MZE");
  if (station === undefined) throw new Error("MZE is missing from the VOR dataset");
  return station;
}

export const DEFAULT_VOR_STATION = defaultVorStation();

interface GeodesicInverse {
  distanceNm: number;
  initialTrueBearingDeg: number;
}

function normalizeDegrees(value: number): number {
  return ((value % 360) + 360) % 360;
}

function sphericalInverse(
  fromLatitudeDeg: number,
  fromLongitudeDeg: number,
  toLatitudeDeg: number,
  toLongitudeDeg: number,
): GeodesicInverse {
  const latitude1 = fromLatitudeDeg * Math.PI / 180;
  const latitude2 = toLatitudeDeg * Math.PI / 180;
  const deltaLatitude = (toLatitudeDeg - fromLatitudeDeg) * Math.PI / 180;
  const deltaLongitude = (toLongitudeDeg - fromLongitudeDeg) * Math.PI / 180;
  const haversine = Math.sin(deltaLatitude / 2) ** 2
    + Math.cos(latitude1) * Math.cos(latitude2) * Math.sin(deltaLongitude / 2) ** 2;
  const centralAngle = 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
  const y = Math.sin(deltaLongitude) * Math.cos(latitude2);
  const x = Math.cos(latitude1) * Math.sin(latitude2)
    - Math.sin(latitude1) * Math.cos(latitude2) * Math.cos(deltaLongitude);
  return {
    distanceNm: 6371008.8 * centralAngle / METERS_PER_NM,
    initialTrueBearingDeg: normalizeDegrees(Math.atan2(y, x) * 180 / Math.PI),
  };
}

/** WGS84 inverse solution using Vincenty's iterative ellipsoidal formula. */
export function wgs84Inverse(
  fromLatitudeDeg: number,
  fromLongitudeDeg: number,
  toLatitudeDeg: number,
  toLongitudeDeg: number,
): GeodesicInverse {
  const phi1 = fromLatitudeDeg * Math.PI / 180;
  const phi2 = toLatitudeDeg * Math.PI / 180;
  const longitudeDelta = (toLongitudeDeg - fromLongitudeDeg) * Math.PI / 180;
  const reduced1 = Math.atan((1 - WGS84_FLATTENING) * Math.tan(phi1));
  const reduced2 = Math.atan((1 - WGS84_FLATTENING) * Math.tan(phi2));
  const sinReduced1 = Math.sin(reduced1);
  const cosReduced1 = Math.cos(reduced1);
  const sinReduced2 = Math.sin(reduced2);
  const cosReduced2 = Math.cos(reduced2);
  let lambda = longitudeDelta;
  let sinSigma = 0;
  let cosSigma = 0;
  let sigma = 0;
  let sinAlpha = 0;
  let cosSquaredAlpha = 0;
  let cosDoubleSigmaMidpoint = 0;
  let converged = false;

  for (let iteration = 0; iteration < 100; iteration += 1) {
    const sinLambda = Math.sin(lambda);
    const cosLambda = Math.cos(lambda);
    const x = cosReduced2 * sinLambda;
    const y = cosReduced1 * sinReduced2 - sinReduced1 * cosReduced2 * cosLambda;
    sinSigma = Math.hypot(x, y);
    if (sinSigma === 0) return { distanceNm: 0, initialTrueBearingDeg: 0 };
    cosSigma = sinReduced1 * sinReduced2 + cosReduced1 * cosReduced2 * cosLambda;
    sigma = Math.atan2(sinSigma, cosSigma);
    sinAlpha = cosReduced1 * cosReduced2 * sinLambda / sinSigma;
    cosSquaredAlpha = 1 - sinAlpha ** 2;
    cosDoubleSigmaMidpoint = cosSquaredAlpha === 0
      ? 0
      : cosSigma - 2 * sinReduced1 * sinReduced2 / cosSquaredAlpha;
    const coefficient = WGS84_FLATTENING / 16 * cosSquaredAlpha
      * (4 + WGS84_FLATTENING * (4 - 3 * cosSquaredAlpha));
    const nextLambda = longitudeDelta + (1 - coefficient) * WGS84_FLATTENING * sinAlpha
      * (sigma + coefficient * sinSigma
        * (cosDoubleSigmaMidpoint + coefficient * cosSigma
          * (-1 + 2 * cosDoubleSigmaMidpoint ** 2)));
    if (Math.abs(nextLambda - lambda) <= 1e-12) {
      lambda = nextLambda;
      converged = true;
      break;
    }
    lambda = nextLambda;
  }
  if (!converged) {
    return sphericalInverse(
      fromLatitudeDeg,
      fromLongitudeDeg,
      toLatitudeDeg,
      toLongitudeDeg,
    );
  }

  const squaredU = cosSquaredAlpha
    * (WGS84_A_METERS ** 2 - WGS84_B_METERS ** 2) / WGS84_B_METERS ** 2;
  const coefficientA = 1 + squaredU / 16384
    * (4096 + squaredU * (-768 + squaredU * (320 - 175 * squaredU)));
  const coefficientB = squaredU / 1024
    * (256 + squaredU * (-128 + squaredU * (74 - 47 * squaredU)));
  const sigmaCorrection = coefficientB * sinSigma
    * (cosDoubleSigmaMidpoint + coefficientB / 4
      * (cosSigma * (-1 + 2 * cosDoubleSigmaMidpoint ** 2)
        - coefficientB / 6 * cosDoubleSigmaMidpoint
        * (-3 + 4 * sinSigma ** 2) * (-3 + 4 * cosDoubleSigmaMidpoint ** 2)));
  const distanceMeters = WGS84_B_METERS * coefficientA * (sigma - sigmaCorrection);
  const initialBearing = Math.atan2(
    cosReduced2 * Math.sin(lambda),
    cosReduced1 * sinReduced2 - sinReduced1 * cosReduced2 * Math.cos(lambda),
  );
  return {
    distanceNm: distanceMeters / METERS_PER_NM,
    initialTrueBearingDeg: normalizeDegrees(initialBearing * 180 / Math.PI),
  };
}

export function nearestVorStation(latitudeDeg: number, longitudeDeg: number): VorStation {
  return VOR_STATIONS.reduce((nearest, station) => (
    wgs84Inverse(station.latitude_deg, station.longitude_deg, latitudeDeg, longitudeDeg).distanceNm
      < wgs84Inverse(nearest.latitude_deg, nearest.longitude_deg, latitudeDeg, longitudeDeg).distanceNm
      ? station
      : nearest
  ));
}

function stationDistanceToPoint(station: VorStation, point: VorRoutePoint): number {
  return wgs84Inverse(
    station.latitude_deg,
    station.longitude_deg,
    point.latitude_deg,
    point.longitude_deg,
  ).distanceNm;
}

export function pointDistanceToSegmentNm(
  point: VorRoutePoint,
  start: VorRoutePoint,
  end: VorRoutePoint,
): number {
  const startToEnd = wgs84Inverse(
    start.latitude_deg,
    start.longitude_deg,
    end.latitude_deg,
    end.longitude_deg,
  );
  const startToPoint = wgs84Inverse(
    start.latitude_deg,
    start.longitude_deg,
    point.latitude_deg,
    point.longitude_deg,
  );
  if (startToEnd.distanceNm < 1e-9) return startToPoint.distanceNm;

  const angularDistance = startToPoint.distanceNm / MEAN_EARTH_RADIUS_NM;
  const bearingDelta = (
    startToPoint.initialTrueBearingDeg - startToEnd.initialTrueBearingDeg
  ) * Math.PI / 180;
  const crossTrackArgument = Math.sin(angularDistance) * Math.sin(bearingDelta);
  const crossTrackAngle = Math.asin(Math.max(-1, Math.min(1, crossTrackArgument)));
  const alongTrackAngle = Math.atan2(
    Math.sin(angularDistance) * Math.cos(bearingDelta),
    Math.cos(angularDistance),
  );
  const segmentAngularLength = startToEnd.distanceNm / MEAN_EARTH_RADIUS_NM;
  if (alongTrackAngle >= 0 && alongTrackAngle <= segmentAngularLength) {
    return Math.abs(crossTrackAngle) * MEAN_EARTH_RADIUS_NM;
  }
  return Math.min(
    startToPoint.distanceNm,
    wgs84Inverse(
      point.latitude_deg,
      point.longitude_deg,
      end.latitude_deg,
      end.longitude_deg,
    ).distanceNm,
  );
}

export function pointAbeamDistanceToSegmentNm(
  point: VorRoutePoint,
  start: VorRoutePoint,
  end: VorRoutePoint,
): number | null {
  const startToEnd = wgs84Inverse(
    start.latitude_deg,
    start.longitude_deg,
    end.latitude_deg,
    end.longitude_deg,
  );
  if (startToEnd.distanceNm <= SEGMENT_ENDPOINT_MARGIN_NM * 2) return null;

  const startToPoint = wgs84Inverse(
    start.latitude_deg,
    start.longitude_deg,
    point.latitude_deg,
    point.longitude_deg,
  );
  const angularDistance = startToPoint.distanceNm / MEAN_EARTH_RADIUS_NM;
  const bearingDelta = (
    startToPoint.initialTrueBearingDeg - startToEnd.initialTrueBearingDeg
  ) * Math.PI / 180;
  const alongTrackAngle = Math.atan2(
    Math.sin(angularDistance) * Math.cos(bearingDelta),
    Math.cos(angularDistance),
  );
  const alongTrackDistanceNm = alongTrackAngle * MEAN_EARTH_RADIUS_NM;
  if (
    alongTrackDistanceNm <= SEGMENT_ENDPOINT_MARGIN_NM ||
    startToEnd.distanceNm - alongTrackDistanceNm <= SEGMENT_ENDPOINT_MARGIN_NM
  ) {
    return null;
  }

  const crossTrackArgument = Math.sin(angularDistance) * Math.sin(bearingDelta);
  const crossTrackAngle = Math.asin(Math.max(-1, Math.min(1, crossTrackArgument)));
  return Math.abs(crossTrackAngle) * MEAN_EARTH_RADIUS_NM;
}

function stationDistanceToSegment(
  station: VorStation,
  start: VorRoutePoint,
  end: VorRoutePoint,
): number {
  return pointDistanceToSegmentNm(station, start, end);
}

function stationDistanceToRoute(station: VorStation, route: VorRoutePoint[]): number {
  if (route.length === 0) return Number.POSITIVE_INFINITY;
  if (route.length === 1) return stationDistanceToPoint(station, route[0]!);
  let nearestDistance = Number.POSITIVE_INFINITY;
  for (let index = 0; index < route.length - 1; index += 1) {
    nearestDistance = Math.min(
      nearestDistance,
      stationDistanceToSegment(station, route[index]!, route[index + 1]!),
    );
  }
  return nearestDistance;
}

function compareDistanceThenIdentifier(
  left: { distance: number; station: VorStation },
  right: { distance: number; station: VorStation },
): number {
  return left.distance - right.distance
    || left.station.identifier.localeCompare(right.station.identifier);
}

/**
 * Order station choices for a route: departure, arrival, five route-nearby
 * stations ordered from the departure, then all remaining identifiers.
 */
export function orderVorStationsForRoute(route: VorRoutePoint[]): VorStation[] {
  if (route.length === 0) {
    return [...VOR_STATIONS].sort((left, right) => (
      left.identifier.localeCompare(right.identifier)
    ));
  }

  const departure = route[0]!;
  const arrival = route.at(-1)!;
  const departureDistances = VOR_STATIONS.map((station) => ({
    station,
    distance: stationDistanceToPoint(station, departure),
  })).sort(compareDistanceThenIdentifier);
  const arrivalDistances = VOR_STATIONS.map((station) => ({
    station,
    distance: stationDistanceToPoint(station, arrival),
  })).sort(compareDistanceThenIdentifier);
  const prioritized = [departureDistances[0]!.station];
  if (arrivalDistances[0]!.station.identifier !== prioritized[0]!.identifier) {
    prioritized.push(arrivalDistances[0]!.station);
  }

  const prioritizedIdentifiers = new Set(prioritized.map((station) => station.identifier));
  const routeNearby = VOR_STATIONS
    .filter((station) => !prioritizedIdentifiers.has(station.identifier))
    .map((station) => ({ station, distance: stationDistanceToRoute(station, route) }))
    .sort(compareDistanceThenIdentifier)
    .slice(0, ROUTE_NEARBY_STATION_LIMIT)
    .map(({ station }) => ({
      station,
      distance: stationDistanceToPoint(station, departure),
    }))
    .sort(compareDistanceThenIdentifier)
    .map(({ station }) => station);
  const selectedIdentifiers = new Set([
    ...prioritizedIdentifiers,
    ...routeNearby.map((station) => station.identifier),
  ]);
  const remaining = VOR_STATIONS
    .filter((station) => !selectedIdentifiers.has(station.identifier))
    .sort((left, right) => left.identifier.localeCompare(right.identifier));
  return [...prioritized, ...routeNearby, ...remaining];
}

export function formatVorRadialDistance(
  station: VorStation,
  latitudeDeg: number | null | undefined,
  longitudeDeg: number | null | undefined,
): string {
  if (latitudeDeg == null || longitudeDeg == null) return "— / —";
  const inverse = wgs84Inverse(
    station.latitude_deg,
    station.longitude_deg,
    latitudeDeg,
    longitudeDeg,
  );
  if (inverse.distanceNm < 1e-9) return "— / 0.0";
  const magneticRadial = normalizeDegrees(
    inverse.initialTrueBearingDeg + station.variation_west_deg,
  );
  const roundedRadial = Math.round(magneticRadial) % 360 || 360;
  const distance = (Math.round((inverse.distanceNm + Number.EPSILON) * 10) / 10).toFixed(1);
  return `${roundedRadial.toString().padStart(3, "0")} / ${distance}`;
}
