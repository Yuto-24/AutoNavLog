import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import {
  Circle,
  CircleMarker,
  MapContainer,
  Polygon,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import { patternAltitudeFtMsl } from "../forms";
import { formatOperationalMagneticCourse } from "../displayRounding";
import {
  fetchRjfmTrainingAirspace,
  isApprovedRjfmAirspaceReference,
  type RjfmTrainingAirspaceBoundary,
  type RjfmTrainingAirspacePolygon,
} from "../rjfmAirspace";
import type {
  AltitudeGuidance,
  AltitudeInputMode,
  AirportOption,
  CalculationOutcome,
  CheckPointInput,
  CheckPointPlanning,
  FlightPhase,
  NavSection,
  Project,
  RjfmMapReference,
  RouteCandidate,
} from "../types";
import { CheckPointEditor } from "./CheckPointEditor";
import { MapResizeHandle } from "./MapResizeHandle";
import { rjfmCandidateClass, rjfmStatusLabels } from "./RjfmGuidancePanel";
import { RouteConfirmation } from "./RouteConfirmation";
import "./RouteWorkspacePhase.css";

interface RouteWorkspaceProps {
  candidate: RouteCandidate | null;
  project: Project | null;
  outcome: CalculationOutcome | null;
  calculationIsCurrent: boolean;
  altitudeGuidance: AltitudeGuidance;
  rjfmMapReference: RjfmMapReference | null;
  altitudeInputs: Record<string, string>;
  destinationAirport: AirportOption | null;
  destinationPatternAltitudeFtMsl: string;
  checkPointPlanning: CheckPointPlanning;
  busy: boolean;
  routeUseConfirmed: boolean;
  polygonRouteConfirmed: boolean;
  canConfirmRoute: boolean;
  onAltitudeInputChange: (sectionId: string, value: string) => void;
  onDestinationPatternAltitudeChange: (value: string) => void;
  onSectionChange: (
    sectionId: string,
    changes: Partial<NavSection>,
    invalidate?: boolean,
  ) => void;
  onRenameRouteNode: (nodeId: string, name: string) => Promise<void>;
  onRouteUseConfirmedChange: (checked: boolean) => void;
  onPolygonRouteConfirmedChange: (checked: boolean) => void;
  onConfirmRoute: () => void;
  onReplaceCheckPoints: (checkPoints: CheckPointInput[]) => Promise<boolean>;
}

const phaseLabels: Record<FlightPhase, string> = {
  CLIMB: "上昇",
  CRUISE: "巡航",
  DESCENT: "降下",
  VISUAL_ARRIVAL: "場周進入",
};

const altitudeBasisLabels: Record<FlightPhase, string> = {
  CLIMB: "上昇先の巡航高度",
  CRUISE: "このLegの巡航高度",
  DESCENT: "降下開始時の巡航高度",
  VISUAL_ARRIVAL: "場周進入の計画高度",
};

function isFixedAltitudeMode(inputMode: AltitudeInputMode | undefined): boolean {
  return inputMode !== undefined && inputMode !== "EDITABLE";
}

function fixedAltitudeLabels(
  inputMode: AltitudeInputMode,
  altitudeFtMsl: number,
): { primary: string; detail: string; phase: string } {
  const altitude = altitudeFtMsl.toLocaleString("ja-JP");
  if (inputMode === "RJFM_DEPARTURE_TO_UMK_FIXED") {
    return {
      primary: `UMK ${altitude} ft HIT`,
      detail: "RJFM→UMKの到達条件（固定）",
      phase: "上昇（固定）",
    };
  }
  if (inputMode === "RJFM_UMK_TO_OMARU_FIXED") {
    return {
      primary: `${altitude} ft 固定`,
      detail: "UMK→OMARUの巡航高度",
      phase: "巡航（固定）",
    };
  }
  if (inputMode === "RJFM_INBOUND_OMARU_TO_UMK_FIXED") {
    return {
      primary: `${altitude} ft 固定`,
      detail: "RJFM帰路 OMARU→UMKの到達高度",
      phase: "巡航（固定）",
    };
  }
  return {
    primary: `UMK ${altitude} ft HIT`,
    detail: `RJFM→OMARU親Leg内・OMARUまで${altitude} ft固定`,
    phase: "上昇→UMK/RCA→巡航（固定）",
  };
}

const centerRoutePointLabels = ["UMK", "OVER FIELD", "OMARU"] as const;

type TrainingAirspaceState =
  | { status: "idle" | "loading" | "unavailable"; polygons: []; boundaries: [] }
  | {
      status: "ready";
      polygons: RjfmTrainingAirspacePolygon[];
      boundaries: RjfmTrainingAirspaceBoundary[];
    };

function FitBounds({
  coordinates,
  signature,
  viewportRevision,
}: {
  coordinates: [number, number][];
  signature: string;
  viewportRevision: number;
}) {
  const map = useMap();
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      map.invalidateSize({ animate: false });
      if (coordinates.length >= 2) {
        map.fitBounds(coordinates as LatLngBoundsExpression, { padding: [28, 28] });
      } else if (coordinates.length === 1 && coordinates[0]) {
        map.setView(coordinates[0], 10);
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [map, signature, viewportRevision]);
  return null;
}

function CheckPointMapPicker({
  active,
  onPick,
}: {
  active: boolean;
  onPick: (latitude: number, longitude: number) => void;
}) {
  useMapEvents({
    click(event) {
      if (active) onPick(event.latlng.lat, event.latlng.lng);
    },
  });
  return null;
}

export function RouteWorkspace({
  candidate,
  project,
  outcome,
  calculationIsCurrent,
  altitudeGuidance,
  rjfmMapReference,
  altitudeInputs,
  destinationAirport,
  destinationPatternAltitudeFtMsl,
  checkPointPlanning,
  busy,
  routeUseConfirmed,
  polygonRouteConfirmed,
  canConfirmRoute,
  onAltitudeInputChange,
  onDestinationPatternAltitudeChange,
  onSectionChange,
  onRenameRouteNode,
  onRouteUseConfirmedChange,
  onPolygonRouteConfirmedChange,
  onConfirmRoute,
  onReplaceCheckPoints,
}: RouteWorkspaceProps) {
  const [mapHeight, setMapHeight] = useState(425);
  const [pickingCheckPoint, setPickingCheckPoint] = useState(false);
  const [phaseEditing, setPhaseEditing] = useState(false);
  const [editingNodeId, setEditingNodeId] = useState<string | null>(null);
  const [nodeNameDraft, setNodeNameDraft] = useState("");
  const [nodeNameError, setNodeNameError] = useState<string | null>(null);
  const [nodeNameSaving, setNodeNameSaving] = useState(false);
  const [pickedCoordinate, setPickedCoordinate] = useState<{
    latitude: number;
    longitude: number;
    revision: number;
  } | null>(null);
  const [trainingAirspace, setTrainingAirspace] = useState<TrainingAirspaceState>({
    status: "idle",
    polygons: [],
    boundaries: [],
  });
  const parsedPatternAltitude = patternAltitudeFtMsl(
    destinationPatternAltitudeFtMsl,
  );
  const patternAltitudeBelowAirport = (
    parsedPatternAltitude !== null &&
    destinationAirport !== null &&
    parsedPatternAltitude <= destinationAirport.elevationFtMsl
  );
  const validPatternAltitude = patternAltitudeBelowAirport
    ? null
    : parsedPatternAltitude;
  const nodes = useMemo(
    () => [...(project?.route_nodes ?? [])].sort((a, b) => a.sequence - b.sequence),
    [project],
  );
  const sections = useMemo(
    () => [...(project?.sections ?? [])].sort((a, b) => a.sequence - b.sequence),
    [project],
  );
  const nodeById = useMemo(
    () => new Map(nodes.map((node) => [node.id, node])),
    [nodes],
  );
  const sectionByFromNode = useMemo(
    () => new Map(sections.map((section) => [section.from_node_id, section])),
    [sections],
  );
  const guidanceBySection = useMemo(
    () =>
      new Map(
        altitudeGuidance.sections.map((guidance) => [guidance.sectionId, guidance]),
      ),
    [altitudeGuidance.sections],
  );
  useEffect(() => {
    setPhaseEditing(false);
    setEditingNodeId(null);
    setNodeNameDraft("");
    setNodeNameError(null);
  }, [project?.id]);
  const cancelNodeNameEdit = () => {
    if (nodeNameSaving) return;
    setEditingNodeId(null);
    setNodeNameDraft("");
    setNodeNameError(null);
  };
  const saveNodeNameEdit = async () => {
    if (!editingNodeId || nodeNameSaving) return;
    const name = nodeNameDraft.trim();
    if (!name) {
      setNodeNameError("名称を入力してください。");
      return;
    }
    setNodeNameSaving(true);
    setNodeNameError(null);
    try {
      await onRenameRouteNode(editingNodeId, name);
      setEditingNodeId(null);
      setNodeNameDraft("");
    } catch (reason) {
      setNodeNameError(
        reason instanceof Error ? reason.message : "名称を保存できませんでした。",
      );
    } finally {
      setNodeNameSaving(false);
    }
  };
  const isPhaseLocked = useCallback(
    (section: NavSection): boolean => {
      const guidance = guidanceBySection.get(section.id);
      const startNode = nodeById.get(section.from_node_id);
      return (
        isFixedAltitudeMode(guidance?.inputMode) ||
        section.phase === "VISUAL_ARRIVAL" ||
        startNode?.role === "VISUAL_REPORTING_POINT"
      );
    },
    [guidanceBySection, nodeById],
  );
  const currentClimbSection = sections.find((section) => section.phase === "CLIMB") ?? null;
  const currentDescentSection = sections.find((section) => section.phase === "DESCENT") ?? null;
  const calculatedDescentRate = outcome?.sections
    .find((section) => section.phase === "DESCENT")
    ?.performance_metadata.descent_rate_fpm;
  const displayedDescentRate =
    typeof calculatedDescentRate === "number" &&
    (calculatedDescentRate === 500 || calculatedDescentRate === 1000)
      ? calculatedDescentRate
      : null;
  const fixedClimbExists = Boolean(currentClimbSection && isPhaseLocked(currentClimbSection));
  const fixedDescentExists = Boolean(currentDescentSection && isPhaseLocked(currentDescentSection));
  const hasEditablePhaseSections = sections.some((section) => !isPhaseLocked(section));
  const phaseOptionsForSection = (section: NavSection): FlightPhase[] => {
    const options: FlightPhase[] = [];
    const canBeClimb =
      (!fixedClimbExists || section.phase === "CLIMB") &&
      (currentDescentSection === null ||
        section.sequence < currentDescentSection.sequence ||
        section.phase === "CLIMB");
    const canBeDescent =
      (!fixedDescentExists || section.phase === "DESCENT") &&
      (currentClimbSection === null ||
        section.sequence > currentClimbSection.sequence ||
        section.phase === "DESCENT");
    if (canBeClimb) options.push("CLIMB");
    options.push("CRUISE");
    if (canBeDescent) options.push("DESCENT");
    return options;
  };
  const changePhaseBasis = (section: NavSection, nextPhase: FlightPhase) => {
    if (!phaseEditing || isPhaseLocked(section) || nextPhase === "VISUAL_ARRIVAL") return;
    if (nextPhase === "CLIMB" || nextPhase === "DESCENT") {
      const duplicates = sections.filter(
        (candidate) =>
          candidate.id !== section.id &&
          candidate.phase === nextPhase &&
          !isPhaseLocked(candidate),
      );
      duplicates.forEach((candidate) =>
        onSectionChange(candidate.id, { phase: "CRUISE" }, false),
      );
      if (section.phase !== nextPhase || duplicates.length > 0) {
        onSectionChange(section.id, { phase: nextPhase });
      }
      return;
    }
    if (section.phase !== "CRUISE") {
      onSectionChange(section.id, { phase: "CRUISE" });
    }
  };
  const checkPoints = useMemo(
    () => (project?.visual_references ?? []).filter((item) => item.role === "CHECK_POINT"),
    [project],
  );
  const rjfmGuidance = calculationIsCurrent
    ? outcome?.rjfm_departure_guidance ?? null
    : null;
  const visibleRjfmCandidates = useMemo(
    () => (rjfmGuidance?.candidates ?? []).filter(
      (item) => item.status !== "UNAVAILABLE" && item.path.length >= 2,
    ),
    [rjfmGuidance],
  );
  const civilAirspaceReference = rjfmMapReference?.civilTrainingTestAirspace ?? null;
  const civilAirspaceReferenceIsApproved = civilAirspaceReference !== null
    && isApprovedRjfmAirspaceReference(civilAirspaceReference);
  const civilAirspaceRequestKey = civilAirspaceReference === null
    ? ""
    : JSON.stringify({
        availability: civilAirspaceReference.availability,
        dataUse: civilAirspaceReference.dataUse,
        contentFingerprintScope: civilAirspaceReference.contentFingerprintScope,
        sourcePageUrl: civilAirspaceReference.sourcePageUrl,
        layerMetadataUrl: civilAirspaceReference.layerMetadataUrl,
        tileUrlTemplate: civilAirspaceReference.tileUrlTemplate,
        tiles: civilAirspaceReference.tiles,
        tileUrls: civilAirspaceReference.tileUrls,
        featureNamePrefix: civilAirspaceReference.featureNamePrefix,
      });
  useEffect(() => {
    if (civilAirspaceReference === null) {
      setTrainingAirspace({ status: "idle", polygons: [], boundaries: [] });
      return undefined;
    }
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8_000);
    setTrainingAirspace({ status: "loading", polygons: [], boundaries: [] });
    void fetchRjfmTrainingAirspace(civilAirspaceReference, controller.signal)
      .then(({ polygons, boundaries }) => {
        if (active) setTrainingAirspace({ status: "ready", polygons, boundaries });
      })
      .catch(() => {
        controller.abort();
        if (active) {
          setTrainingAirspace({ status: "unavailable", polygons: [], boundaries: [] });
        }
      })
      .finally(() => window.clearTimeout(timeout));
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [civilAirspaceRequestKey]);
  const pcaCoordinates = useMemo<[number, number][]>(
    () => (rjfmMapReference?.pca.polygonVertices ?? []).map((point) => [
      point.latitudeDeg,
      point.longitudeDeg,
    ]),
    [rjfmMapReference],
  );
  const pcaFitCoordinates = useMemo<[number, number][]>(
    () => rjfmMapReference === null
      ? []
      : [
          ...pcaCoordinates,
          [
            rjfmMapReference.pca.exclusionCenter.latitudeDeg,
            rjfmMapReference.pca.exclusionCenter.longitudeDeg,
          ],
        ],
    [pcaCoordinates, rjfmMapReference],
  );

  const coordinates = useMemo<[number, number][]>(
    () =>
      nodes.length
        ? nodes.map((node) => [node.latitude_deg, node.longitude_deg])
        : candidate?.coordinates ?? [],
    [candidate, nodes],
  );
  const coordinateSignature = useMemo(
    () =>
      [
        ...coordinates,
        ...checkPoints.map<[number, number]>((item) => [item.latitude_deg, item.longitude_deg]),
        ...checkPointPlanning.projections.map<[number, number]>((item) => [
          item.abeam_latitude_deg,
          item.abeam_longitude_deg,
        ]),
        ...(rjfmGuidance?.center_route ?? []).map<[number, number]>((item) => [
          item.latitude_deg,
          item.longitude_deg,
        ]),
        ...visibleRjfmCandidates.flatMap((item) =>
          item.path.map<[number, number]>((point) => [
            point.latitude_deg,
            point.longitude_deg,
          ]),
        ),
        ...pcaFitCoordinates,
      ].map(([latitude, longitude]) => `${latitude},${longitude}`).join(";"),
    [
      checkPointPlanning.projections,
      checkPoints,
      coordinates,
      pcaFitCoordinates,
      rjfmGuidance,
      visibleRjfmCandidates,
    ],
  );
  const fitCoordinates = useMemo<[number, number][]>(
    () => [
      ...coordinates,
      ...checkPoints.map<[number, number]>((item) => [item.latitude_deg, item.longitude_deg]),
      ...checkPointPlanning.projections.map<[number, number]>((item) => [
        item.abeam_latitude_deg,
        item.abeam_longitude_deg,
      ]),
      ...(rjfmGuidance?.center_route ?? []).map<[number, number]>((item) => [
        item.latitude_deg,
        item.longitude_deg,
      ]),
      ...visibleRjfmCandidates.flatMap((item) =>
        item.path.map<[number, number]>((point) => [
          point.latitude_deg,
          point.longitude_deg,
        ]),
      ),
      ...pcaFitCoordinates,
    ],
    [
      checkPointPlanning.projections,
      checkPoints,
      coordinates,
      pcaFitCoordinates,
      rjfmGuidance,
      visibleRjfmCandidates,
    ],
  );
  const projectionById = useMemo(
    () => new Map(checkPointPlanning.projections.map((item) => [item.checkpoint_id, item])),
    [checkPointPlanning.projections],
  );
  const handleMapPick = useCallback((latitude: number, longitude: number) => {
    setPickedCoordinate((current) => ({
      latitude,
      longitude,
      revision: (current?.revision ?? 0) + 1,
    }));
  }, []);
  const handlePickingChange = useCallback((active: boolean) => {
    setPickingCheckPoint(active);
  }, []);
  const clearPickedCoordinate = useCallback(() => {
    setPickedCoordinate(null);
  }, []);
  const mapFrameStyle = {
    "--route-map-height": `${mapHeight}px`,
  } as CSSProperties;

  return (
    <section className="route-workspace" aria-label="経路地図とLeg設定">
      <div className="workspace-heading">
        <div>
          <h2>経路</h2>
          <p>
            {project
              ? `${project.departure_airport_id} → ${project.destination_airport_id}`
              : candidate?.name ?? "飛行経路候補を選択すると地図へ表示します"}
          </p>
        </div>
        {project && <span className="route-count">{nodes.length}点 / {sections.length} Leg</span>}
      </div>
      <div
        id="route-map-frame"
        className={`map-frame${pickingCheckPoint ? " is-picking-checkpoint" : ""}`}
        style={mapFrameStyle}
      >
        <MapContainer
          center={[32.6, 131.3]}
          zoom={7}
          scrollWheelZoom
          className="route-map"
          aria-label="飛行経路地図"
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          {rjfmMapReference && pcaCoordinates.length >= 3 && (
            <>
              <Polygon
                className="rjfm-pca-boundary"
                positions={pcaCoordinates}
                pathOptions={{
                  color: "#a45b13",
                  fillColor: "#f2a43a",
                  fillOpacity: 0.12,
                  opacity: 0.95,
                  weight: 3,
                }}
              >
                <Tooltip sticky>
                  <span>
                    宮崎特別管制区（PCA）水平境界 / 原告示高度
                    {" "}{rjfmMapReference.pca.sourceAltitudeLowerM.toLocaleString("ja-JP")}
                    –{rjfmMapReference.pca.sourceAltitudeUpperM.toLocaleString("ja-JP")} m
                    <br />
                    運用判定 {rjfmMapReference.pca.operationalAltitudeLowerFtMsl.toLocaleString("ja-JP")}
                    –{rjfmMapReference.pca.operationalAltitudeUpperFtMsl.toLocaleString("ja-JP")} ft MSL
                    （利用者承認値）
                  </span>
                </Tooltip>
              </Polygon>
              <Circle
                className="rjfm-pca-exclusion"
                center={[
                  rjfmMapReference.pca.exclusionCenter.latitudeDeg,
                  rjfmMapReference.pca.exclusionCenter.longitudeDeg,
                ]}
                radius={rjfmMapReference.pca.exclusionRadiusKm * 1000}
                pathOptions={{
                  color: "#087b78",
                  dashArray: "7 6",
                  fillColor: "#ffffff",
                  fillOpacity: 0.68,
                  opacity: 0.95,
                  weight: 3,
                }}
              >
                <Tooltip sticky>
                  PCA中心除外 {rjfmMapReference.pca.exclusionRadiusKm.toLocaleString("ja-JP")} km
                  （国交省告示）
                </Tooltip>
              </Circle>
            </>
          )}
          {trainingAirspace.polygons.map((airspace) => (
            <Polygon
              key={airspace.id}
              className="rjfm-training-airspace"
              positions={airspace.positions}
              pathOptions={{
                fillColor: "#5b83cf",
                fillOpacity: 0.1,
                stroke: false,
              }}
            >
              <Tooltip sticky>
                <span>
                  <strong>{airspace.name}</strong>
                  <br />
                  {airspace.lowerLimit} – {airspace.upperLimit}
                  <br />
                  {airspace.authority}
                  <br />
                  出典: 国土地理院（表示専用）
                </span>
              </Tooltip>
            </Polygon>
          ))}
          {trainingAirspace.boundaries.map((boundary) => (
            <Polyline
              key={boundary.id}
              className="rjfm-training-airspace-boundary"
              positions={boundary.positions}
              pathOptions={{
                color: "#315caa",
                opacity: 0.82,
                weight: 2,
              }}
            >
              <Tooltip sticky>
                <span>
                  <strong>{boundary.name}</strong>
                  <br />
                  {boundary.lowerLimit} – {boundary.upperLimit}
                  <br />
                  {boundary.authority}
                  <br />
                  出典: 国土地理院（表示専用）
                </span>
              </Tooltip>
            </Polyline>
          ))}
          {coordinates.length >= 2 && (
            <Polyline positions={coordinates} pathOptions={{ color: "#173b5e", weight: 4 }} />
          )}
          {rjfmGuidance && rjfmGuidance.center_route.length >= 2 && (
            <Polyline
              className="rjfm-center-route"
              positions={rjfmGuidance.center_route.map<[number, number]>((point) => [
                point.latitude_deg,
                point.longitude_deg,
              ])}
              pathOptions={{
                color: "#287a45",
                dashArray: "8 6",
                opacity: 0.95,
                weight: 4,
              }}
            />
          )}
          {visibleRjfmCandidates.map((item) => (
            <Polyline
              key={`rjfm-path-${item.runway}`}
              className={`rjfm-guidance-path ${rjfmCandidateClass(item)}`}
              positions={item.path.map<[number, number]>((point) => [
                point.latitude_deg,
                point.longitude_deg,
              ])}
              pathOptions={{
                color: item.status === "HARD_INVALID"
                  ? "#b42318"
                  : item.runway === "09" ? "#2368a2" : "#6e4aa0",
                dashArray: item.status === "WARNING" ? "9 5" : undefined,
                opacity: item.status === "HARD_INVALID" ? 0.82 : 0.92,
                weight: item.status === "HARD_INVALID" ? 3 : 4,
              }}
            />
          ))}
          {rjfmGuidance?.center_route.map((point, index) => (
            <CircleMarker
              key={`rjfm-center-${point.latitude_deg}-${point.longitude_deg}`}
              className="rjfm-center-marker"
              center={[point.latitude_deg, point.longitude_deg]}
              radius={index === 1 ? 7 : 6}
              pathOptions={{
                color: "#1f663a",
                fillColor: "#effaf3",
                fillOpacity: 1,
                weight: 3,
              }}
            >
              <Tooltip direction="right" offset={[7, 0]}>
                {centerRoutePointLabels[index] ?? `CENTER ${index + 1}`}
              </Tooltip>
            </CircleMarker>
          ))}
          {nodes.map((node, index) => {
            const isVrep = node.role === "VISUAL_REPORTING_POINT";
            const isAirport = node.role === "AIRPORT" || node.role === "DESTINATION";
            return (
              <CircleMarker
                key={node.id}
                center={[node.latitude_deg, node.longitude_deg]}
                radius={isAirport ? 9 : isVrep ? 8 : 6}
                pathOptions={{
                  color: isVrep ? "#0c7c78" : "#0b1f33",
                  fillColor: isVrep ? "#0c7c78" : isAirport ? "#0b1f33" : "#ffffff",
                  fillOpacity: 1,
                  weight: 3,
                }}
              >
                <Tooltip permanent direction="top" offset={[0, -7]}>
                  {isVrep ? node.name + " (VREP)" : node.name}
                </Tooltip>
              </CircleMarker>
            );
          })}
          {outcome?.derived_points.map((point) => (
            <CircleMarker
              key={`${point.type}-${point.along_route_distance_nm}`}
              center={[point.latitude_deg, point.longitude_deg]}
              radius={7}
              pathOptions={{
                color: point.type === "RCA" ? "#0c7c78" : "#b42318",
                fillColor: "#ffffff",
                fillOpacity: 1,
                weight: 3,
              }}
            >
              <Tooltip permanent direction="bottom" offset={[0, 7]}>
                {point.type}
              </Tooltip>
            </CircleMarker>
          ))}
          {checkPoints.map((checkPoint) => {
            const projection = projectionById.get(checkPoint.id);
            return (
              <Fragment key={checkPoint.id}>
                {projection && (
                  <Polyline
                    positions={[
                      [checkPoint.latitude_deg, checkPoint.longitude_deg],
                      [projection.abeam_latitude_deg, projection.abeam_longitude_deg],
                    ]}
                    pathOptions={{ color: "#9b5b13", weight: 2, dashArray: "5 5" }}
                  />
                )}
                <CircleMarker
                  className="checkpoint-confirmed-marker"
                  center={[checkPoint.latitude_deg, checkPoint.longitude_deg]}
                  radius={7}
                  pathOptions={{
                    color: "#9b5b13",
                    fillColor: "#fff7e8",
                    fillOpacity: 1,
                    weight: 3,
                  }}
                >
                  <Tooltip permanent direction="right" offset={[8, 0]}>
                    CP {checkPoint.name}
                  </Tooltip>
                </CircleMarker>
                {projection && (
                  <CircleMarker
                    center={[projection.abeam_latitude_deg, projection.abeam_longitude_deg]}
                    radius={4}
                    pathOptions={{
                      color: "#9b5b13",
                      fillColor: "#9b5b13",
                      fillOpacity: 1,
                      weight: 2,
                    }}
                  >
                    <Tooltip direction="bottom">abeam {checkPoint.name}</Tooltip>
                  </CircleMarker>
                )}
              </Fragment>
            );
          })}
          {pickedCoordinate && (
            <CircleMarker
              className="checkpoint-draft-marker"
              center={[pickedCoordinate.latitude, pickedCoordinate.longitude]}
              radius={9}
              pathOptions={{
                color: "#6e4aa0",
                dashArray: "4 3",
                fillColor: "#f4edfb",
                fillOpacity: 0.95,
                weight: 3,
              }}
            >
              <Tooltip permanent direction="right" offset={[10, 0]}>
                仮CP（未保存）
              </Tooltip>
            </CircleMarker>
          )}
          <CheckPointMapPicker active={pickingCheckPoint} onPick={handleMapPick} />
          <FitBounds
            coordinates={fitCoordinates}
            signature={coordinateSignature}
            viewportRevision={mapHeight}
          />
        </MapContainer>
        {(rjfmMapReference || rjfmGuidance) && (
          <div
            className="rjfm-map-legend"
            role="group"
            aria-label="RJFMガイダンス凡例"
          >
            <strong>RJFM空域</strong>
            {rjfmMapReference && (
              <>
                <span>
                  <i className="is-pca" aria-hidden="true" />
                  PCA {rjfmMapReference.pca.sourceAltitudeLowerM.toLocaleString("ja-JP")}
                  –{rjfmMapReference.pca.sourceAltitudeUpperM.toLocaleString("ja-JP")} m
                </span>
                <span>
                  <i className="is-pca-exclusion" aria-hidden="true" />
                  中心除外 {rjfmMapReference.pca.exclusionRadiusKm.toLocaleString("ja-JP")} km
                </span>
                {trainingAirspace.status === "ready" && (
                  <span>
                    <i className="is-training-airspace" aria-hidden="true" />
                    民間訓練試験空域 KS4（GSI）
                  </span>
                )}
              </>
            )}
            {rjfmGuidance && (
              <>
                <span><i className="is-center" aria-hidden="true" />Newta CENTER</span>
                {visibleRjfmCandidates.map((item) => (
                  <span key={`legend-${item.runway}`}>
                    <i className={rjfmCandidateClass(item)} aria-hidden="true" />
                    RWY {item.runway} {rjfmStatusLabels[item.status]}
                  </span>
                ))}
              </>
            )}
            {rjfmMapReference && (
              <span className="rjfm-airspace-status" aria-live="polite">
                {trainingAirspace.status === "loading"
                  ? "GSI空域を取得中…"
                  : trainingAirspace.status === "unavailable"
                    ? "GSI空域は取得できず非表示"
                    : trainingAirspace.status === "ready"
                      ? `GSI: ${trainingAirspace.polygons.length}区画を表示`
                      : ""}
              </span>
            )}
          </div>
        )}
        {!coordinates.length && (
          <div className="map-empty">
            <strong>経路はまだありません</strong>
            <span>KML/KMZを読み込み、飛行経路候補を選択してください。</span>
          </div>
        )}
      </div>
      <MapResizeHandle value={mapHeight} min={320} max={900} onChange={setMapHeight} />
      {rjfmMapReference && (
        <aside className="rjfm-airspace-note" aria-label="RJFM空域データ注記">
          <span>{rjfmMapReference.civilTrainingTestAirspace.caution}</span>
          {civilAirspaceReferenceIsApproved && (
            <span>
              出典: <a
                href={rjfmMapReference.civilTrainingTestAirspace.sourcePageUrl}
                target="_blank"
                rel="noreferrer"
              >国土交通省</a>
              ・<a
                href={rjfmMapReference.civilTrainingTestAirspace.layerMetadataUrl}
                target="_blank"
                rel="noreferrer"
              >国土地理院レイヤー</a>
            </span>
          )}
          <span>
            レイヤー設定確認: <time
              dateTime={rjfmMapReference.civilTrainingTestAirspace.checkedAtUtc}
            >{rjfmMapReference.civilTrainingTestAirspace.checkedAtUtc}</time>
          </span>
        </aside>
      )}

      <RouteConfirmation
        visible={Boolean(candidate && !project)}
        polygon={candidate?.kind === "polygon"}
        routeUseConfirmed={routeUseConfirmed}
        polygonRouteConfirmed={polygonRouteConfirmed}
        canConfirm={canConfirmRoute}
        busy={busy}
        onRouteUseConfirmedChange={onRouteUseConfirmedChange}
        onPolygonRouteConfirmedChange={onPolygonRouteConfirmedChange}
        onConfirm={onConfirmRoute}
      />

      {project && (
        <CheckPointEditor
          nodes={nodes}
          sections={sections}
          checkPoints={checkPoints}
          planning={checkPointPlanning}
          busy={busy}
          pickedCoordinate={pickedCoordinate}
          pickingFromMap={pickingCheckPoint}
          onPickingFromMapChange={handlePickingChange}
          onPickedCoordinateClear={clearPickedCoordinate}
          onReplace={onReplaceCheckPoints}
        />
      )}

      {project && phaseEditing && (
        <div className="phase-edit-note" role="status">
          CLIMB / DESCENT を別Legへ選ぶと、既存の同Phaseは自動でCRUISEへ戻ります。
          RCA・EOC・性能・時間・燃料計算が変わるため、計画意図を確認して変更してください。
        </div>
      )}
      <div className="table-scroll route-table-scroll">
        <table className="route-table route-table-phase-compact">
          <thead>
            <tr>
              <th>POINT</th>
              <th>ALT ft MSL / MC候補</th>
              <th>
                <div className="phase-heading">
                  <span>PHASE</span>
                  {project && hasEditablePhaseSections && (
                    <button
                      className={`phase-edit-toggle${phaseEditing ? " is-active" : ""}`}
                      type="button"
                      disabled={busy}
                      aria-pressed={phaseEditing}
                      onClick={() => setPhaseEditing((current) => !current)}
                    >
                      {phaseEditing ? "完了" : "変更"}
                    </button>
                  )}
                </div>
              </th>
            </tr>
          </thead>
          <tbody>
            {!project && candidate && (
              <tr>
                <td colSpan={3}>{candidate.vertexCount}点の形状を確認中</td>
              </tr>
            )}
            {!project && !candidate && (
              <tr>
                <td colSpan={3} className="empty-table-cell">
                  経路を取り込むとRoute点が表示されます
                </td>
              </tr>
            )}
            {nodes.map((node, index) => {
              const section = sectionByFromNode.get(node.id);
              const guidance = section
                ? guidanceBySection.get(section.id)
                : undefined;
              const fixedAltitude = section && isFixedAltitudeMode(guidance?.inputMode)
                ? guidance?.fixedAltitudeFtMsl ?? section.planned_altitude_ft_msl
                : null;
              const fixedLabels = fixedAltitude !== null && guidance
                ? fixedAltitudeLabels(guidance.inputMode, fixedAltitude)
                : null;
              const enteredAltitude = section ? altitudeInputs[section.id] : undefined;
              const effectiveAltitude = section
                ? enteredAltitude === undefined
                  ? section.planned_altitude_ft_msl
                  : enteredAltitude.trim() === ""
                    ? null
                    : Number(enteredAltitude)
                : null;
              const isCandidateAltitude = Boolean(
                effectiveAltitude !== null &&
                Number.isFinite(effectiveAltitude) &&
                guidance?.candidateAltitudesFtMsl.some(
                  (candidateAltitude) =>
                    candidateAltitude === effectiveAltitude,
                ),
              );
              const hasAltitudeCandidateWarning =
                fixedAltitude === null &&
                Boolean(guidance?.appliesToCruisingAltitudeInput) &&
                effectiveAltitude !== null &&
                Number.isFinite(effectiveAltitude) &&
                effectiveAltitude > 3000 &&
                !isCandidateAltitude;
              const phaseLocked = Boolean(section && isPhaseLocked(section));
              return (
                <tr
                  key={node.id}
                  className={[
                    node.role === "VISUAL_REPORTING_POINT" ? "vrep-row" : "",
                    node.role === "DESTINATION" ? "destination-row" : "",
                    hasAltitudeCandidateWarning ? "altitude-warning-row" : "",
                  ].filter(Boolean).join(" ")}
                >
                  <td>
                    <span className="point-index">{index}</span>
                    {editingNodeId === node.id ? (
                      <div className="route-name-editor">
                        <input
                          aria-label={`${node.name}の名称`}
                          autoFocus
                          disabled={busy || nodeNameSaving}
                          value={nodeNameDraft}
                          onChange={(event) => setNodeNameDraft(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              void saveNodeNameEdit();
                            }
                            if (event.key === "Escape") {
                              event.preventDefault();
                              cancelNodeNameEdit();
                            }
                          }}
                        />
                        <button
                          className="route-name-save"
                          type="button"
                          disabled={busy || nodeNameSaving}
                          onClick={() => void saveNodeNameEdit()}
                        >
                          {nodeNameSaving ? "保存中" : "保存"}
                        </button>
                        <button
                          className="route-name-cancel"
                          type="button"
                          disabled={busy || nodeNameSaving}
                          onClick={cancelNodeNameEdit}
                        >
                          取消
                        </button>
                        {nodeNameError && <small className="field-error">{nodeNameError}</small>}
                      </div>
                    ) : (
                      <div className="route-name-display">
                        <strong>{node.name}</strong>
                        {node.role !== "AIRPORT" && node.role !== "DESTINATION" && (
                          <button
                            className="route-name-edit"
                            type="button"
                            disabled={busy}
                            onClick={() => {
                              setEditingNodeId(node.id);
                              setNodeNameDraft(node.name);
                              setNodeNameError(null);
                            }}
                          >
                            編集
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                  <td className={hasAltitudeCandidateWarning ? "altitude-warning-cell" : ""}>
                    {section && fixedLabels ? (
                      <div
                        className="fixed-altitude-control"
                        aria-label={`${node.name}出発Legの固定高度`}
                      >
                        <strong>{fixedLabels.primary}</strong>
                        <small>{fixedLabels.detail}</small>
                      </div>
                    ) : section ? (
                      <div className="altitude-controls">
                        {guidance?.appliesToCruisingAltitudeInput && (
                          <select
                            className="table-select altitude-candidate-select"
                            aria-label={
                              section.phase === "CRUISE"
                                ? node.name + "出発Legの巡航高度候補"
                                : node.name +
                                  "出発Legの" +
                                  altitudeBasisLabels[section.phase] +
                                  "候補"
                            }
                            value={
                              isCandidateAltitude
                                ? String(effectiveAltitude)
                                : "custom"
                            }
                            onChange={(event) => {
                              if (event.target.value !== "custom") {
                                onAltitudeInputChange(section.id, event.target.value);
                              }
                            }}
                          >
                            {guidance.candidateAltitudesFtMsl.map((altitude) => (
                              <option key={altitude} value={altitude}>
                                {altitude.toLocaleString("ja-JP")} ft
                              </option>
                            ))}
                            <option value="custom">任意値</option>
                          </select>
                        )}
                        <input
                          className="table-number-input"
                          aria-label={node.name + "出発Legの計画高度"}
                          type="number"
                          min="100"
                          max="25000"
                          step="100"
                          value={altitudeInputs[section.id] ?? String(section.planned_altitude_ft_msl)}
                          placeholder="入力"
                          onChange={(event) =>
                            onAltitudeInputChange(section.id, event.target.value)
                          }
                        />
                        {guidance && (
                          <small className="altitude-course">
                            {guidance.appliesToCruisingAltitudeInput
                              ? altitudeBasisLabels[section.phase] + "・"
                              : ""}
                            VAR {guidance.variationDegEast > 0 ? "+" : ""}{guidance.variationDegEast}°
                            {" / MC "}{formatOperationalMagneticCourse(
                              guidance.vfrCruisingAltitudeMagneticCourseDeg,
                            )}
                            {hasAltitudeCandidateWarning ? "・候補外（警告）" : ""}
                          </small>
                        )}
                      </div>
                    ) : node.role === "DESTINATION" ? (
                      <div className="arrival-altitude-control">
                        <div className="arrival-airport-reference">
                          <span>飛行場標高</span>
                          <strong>
                            {destinationAirport?.elevationFtMsl.toLocaleString("ja-JP") ?? "—"}
                            {" ft MSL"}
                          </strong>
                          {destinationAirport && (
                            <small>
                              {destinationAirport.icao} {destinationAirport.name}
                            </small>
                          )}
                        </div>
                        <label>
                          <span>今回採用する場周経路高度</span>
                          <input
                            className="arrival-pattern-input"
                            aria-label="今回採用する場周経路高度"
                            aria-describedby={`${node.id}-pattern-altitude-help`}
                            aria-invalid={validPatternAltitude === null}
                            type="number"
                            min="100"
                            max="25000"
                            step="100"
                            value={destinationPatternAltitudeFtMsl}
                            onChange={(event) =>
                              onDestinationPatternAltitudeChange(event.target.value)
                            }
                          />
                        </label>
                        <small
                          id={`${node.id}-pattern-altitude-help`}
                          className={`arrival-altitude-help ${validPatternAltitude === null ? "field-error" : ""}`}
                        >
                          {destinationAirport
                            ? `master ${destinationAirport.patternAltitudeFtMsl.toLocaleString("ja-JP")} ft MSL（標高差 ${(destinationAirport.patternAltitudeFtMsl - destinationAirport.elevationFtMsl).toLocaleString("ja-JP")} ft）`
                            : "目的地空港のmaster値を確認してください。"}
                          <br />
                          {validPatternAltitude === null
                            ? patternAltitudeBelowAirport
                              ? "採用場周経路高度は飛行場標高より高くしてください。"
                              : "100～25,000 ftの範囲で100 ft単位の整数を入力してください。"
                            : "運用差がある場合は、今回使用するMSL高度へ編集してください。"}
                        </small>
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="phase-cell">
                    {section && fixedLabels ? (
                      <span
                        className="fixed-phase"
                        aria-label={`${node.name}出発Legの固定Phase`}
                      >
                        {fixedLabels.phase}
                      </span>
                    ) : section && phaseLocked ? (
                      <span
                        className="phase-locked-value"
                        aria-label={`${node.name}出発Legの固定Phase`}
                      >
                        {phaseLabels[section.phase]}（固定）
                      </span>
                    ) : section ? (
                      <select
                        className={`table-select phase-select${phaseEditing ? " is-editing" : " is-readonly"}`}
                        aria-label={`${node.name}出発LegのPhase`}
                        aria-disabled={!phaseEditing}
                        tabIndex={phaseEditing ? 0 : -1}
                        title={
                          phaseEditing
                            ? "CLIMBまたはDESCENTを選ぶと同Phaseの基準Legが移動します。"
                            : "変更する場合はPHASE列の「変更」を押してください。"
                        }
                        value={section.phase}
                        onPointerDown={(event) => {
                          if (!phaseEditing) event.preventDefault();
                        }}
                        onKeyDown={(event) => {
                          if (!phaseEditing) event.preventDefault();
                        }}
                        onChange={(event) =>
                          changePhaseBasis(section, event.target.value as FlightPhase)
                        }
                      >
                        {phaseOptionsForSection(section).map((phase) => (
                          <option key={phase} value={phase}>
                            {phaseLabels[phase]}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="phase-arrival-value">到着</span>
                    )}
                    {section?.phase === "DESCENT" && displayedDescentRate !== null && (
                      <small className="descent-rate-result">
                        計算結果 {displayedDescentRate} fpm
                      </small>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {project && (
        <p className="altitude-guidance-note">
          上昇のALTは上昇先、降下のALTは降下開始時の巡航高度です。いずれもMC候補から選択できます。
          <br />
          {altitudeGuidance.legalThresholdNote}
          <br />
          {altitudeGuidance.terrainLimitationNote}
        </p>
      )}
    </section>
  );
}
