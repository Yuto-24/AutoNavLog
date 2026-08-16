import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import { patternAltitudeFtMsl } from "../forms";
import type {
  AltitudeGuidance,
  AirportOption,
  CalculationOutcome,
  CheckPointInput,
  CheckPointPlanning,
  FlightPhase,
  NavSection,
  Project,
  RjfmDepartureGuidance,
  RjfmGuidanceStatus,
  RjfmRunwayGuidance,
  RouteCandidate,
} from "../types";
import { CheckPointEditor } from "./CheckPointEditor";
import { MapResizeHandle } from "./MapResizeHandle";
import { RouteConfirmation } from "./RouteConfirmation";

interface RouteWorkspaceProps {
  candidate: RouteCandidate | null;
  project: Project | null;
  outcome: CalculationOutcome | null;
  calculationIsCurrent: boolean;
  altitudeGuidance: AltitudeGuidance;
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
  onSectionChange: (sectionId: string, changes: Partial<NavSection>) => void;
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

const roleLabels: Record<string, string> = {
  AIRPORT: "出発",
  ROUTE_POINT: "経路点",
  VISUAL_REPORTING_POINT: "VREP",
  DESTINATION: "到着",
};

const rjfmStatusLabels: Record<RjfmGuidanceStatus, string> = {
  VALID: "成立",
  WARNING: "成立（注意）",
  HARD_INVALID: "不成立",
  UNAVAILABLE: "算出不可",
};

const rjfmTurnMethodLabels: Record<RjfmRunwayGuidance["turn_method"], string> = {
  FIXED_BANK_20: "左20°バンク",
  ADJUSTED_MAX_RADIUS: "最大半径へ調整",
  NONE: "旋回解なし",
};

const centerRoutePointLabels = ["UMK", "OVER FIELD", "OMARU"] as const;

function rjfmCandidateClass(candidate: RjfmRunwayGuidance): string {
  if (candidate.status === "HARD_INVALID") return "is-invalid";
  if (candidate.status === "UNAVAILABLE") return "is-unavailable";
  if (candidate.status === "WARNING") return `is-rwy-${candidate.runway} is-warning`;
  return `is-rwy-${candidate.runway}`;
}

function formatRjfmTurns(candidate: RjfmRunwayGuidance): string {
  const partial = candidate.partial_left_turn_deg;
  if (candidate.full_left_turns === 0 && partial === null) return "旋回なし";
  return [
    `左旋回 ${candidate.full_left_turns}周`,
    partial === null ? null : `+ ${partial.toFixed(1)}°`,
  ].filter(Boolean).join(" ");
}

function formatMzePosition(candidate: RjfmRunwayGuidance): string {
  if (candidate.turn_entry_radial_deg === null || candidate.turn_entry_dme_nm === null) {
    return "—";
  }
  const radial = String(Math.round(candidate.turn_entry_radial_deg) % 360).padStart(3, "0");
  return `R${radial}° / ${candidate.turn_entry_dme_nm.toFixed(1)} DME`;
}

function formatRjfmTimeDelta(seconds: number | null): string {
  if (seconds === null) return "—";
  const halfMinuteValue = Math.round(Math.abs(seconds) / 30) * 0.5;
  if (halfMinuteValue === 0) return "±0.0 min";
  const value = halfMinuteValue.toFixed(1);
  return seconds > 0 ? `LOSS +${value} min` : `GAIN −${value} min`;
}

function formatRjfmResiduals(candidate: RjfmRunwayGuidance): string {
  const values = [
    candidate.position_residual_nm === null
      ? null
      : `位置 ${candidate.position_residual_nm.toFixed(3)} NM`,
    candidate.altitude_residual_ft === null
      ? null
      : `高度 ${Math.round(candidate.altitude_residual_ft)} ft`,
    candidate.tangent_residual_deg === null
      ? null
      : `接線 ${candidate.tangent_residual_deg.toFixed(1)}°`,
  ].filter((value): value is string => value !== null);
  return values.length ? values.join(" / ") : "—";
}

function rjfmSourceLabel(key: string): string {
  const normalized = key.toLowerCase();
  if (normalized.includes("training") || normalized.includes("procedure")) {
    return "訓練飛行実施要領";
  }
  if (normalized.includes("aip")) return "AIP RJFM";
  if (normalized.includes("pca")) return "宮崎空港PCA";
  if (normalized.includes("mze") || normalized.includes("navaid")) return "MZE資料";
  return key.replaceAll("_", " ");
}

function RjfmGuidancePanel({ guidance }: { guidance: RjfmDepartureGuidance }) {
  const sourceDates = Object.entries(guidance.source_effective_dates)
    .sort(([left], [right]) => left.localeCompare(right));
  return (
    <section className="rjfm-guidance" aria-label="RJFM北方面出発ガイダンス">
      <div className="rjfm-guidance-heading">
        <div>
          <span className="rjfm-guidance-eyebrow">RJFM NORTHBOUND EXCEPTION</span>
          <h3>Newta CENTER Route 出発ガイダンス</h3>
        </div>
        <span className="rjfm-reference-revision">参照 {guidance.reference_revision}</span>
      </div>
      <p className="rjfm-guidance-intro">
        UMKを5,500 ft MSLで通過するPOH上昇時間を基準に、RWY別の左旋回候補を表示しています。
      </p>
      <div className="rjfm-candidate-grid">
        {guidance.candidates.map((candidate) => {
          const passedConstraints = candidate.constraints.filter((item) => item.passed).length;
          const dmeWarning = candidate.turn_entry_dme_nm !== null
            && candidate.turn_entry_dme_nm < 4;
          return (
            <article
              key={candidate.runway}
              className={`rjfm-candidate ${rjfmCandidateClass(candidate)}`}
              aria-label={`RWY ${candidate.runway} 候補 ${rjfmStatusLabels[candidate.status]}`}
            >
              <div className="rjfm-candidate-heading">
                <h4>RWY {candidate.runway}</h4>
                <span className="rjfm-status">{rjfmStatusLabels[candidate.status]}</span>
              </div>
              <dl className="rjfm-candidate-metrics">
                <div>
                  <dt>左旋回</dt>
                  <dd>{formatRjfmTurns(candidate)}</dd>
                </div>
                <div>
                  <dt>旋回モデル</dt>
                  <dd>{rjfmTurnMethodLabels[candidate.turn_method]}</dd>
                </div>
                <div>
                  <dt>MZE位置</dt>
                  <dd>{formatMzePosition(candidate)}</dd>
                </div>
                <div>
                  <dt>旋回開始高度</dt>
                  <dd>
                    {candidate.turn_entry_altitude_ft_msl === null
                      ? "—"
                      : `${Math.round(candidate.turn_entry_altitude_ft_msl).toLocaleString("ja-JP")} ft MSL`}
                  </dd>
                </div>
                <div>
                  <dt>到達条件</dt>
                  <dd>UMK 5,500 ft MSL</dd>
                </div>
                <div>
                  <dt>直線Legとの差</dt>
                  <dd>{formatRjfmTimeDelta(candidate.expected_time_delta_seconds)}</dd>
                </div>
                <div>
                  <dt>全周旋回後ドリフト</dt>
                  <dd>
                    {candidate.exit_drift_nm === null
                      ? "—"
                      : `${candidate.exit_drift_nm.toFixed(2)} NM`}
                  </dd>
                </div>
                <div>
                  <dt>制約判定</dt>
                  <dd>
                    {candidate.constraints.length
                      ? `${passedConstraints}/${candidate.constraints.length} 適合`
                      : "判定なし"}
                  </dd>
                </div>
              </dl>
              <p className="rjfm-residuals">
                <strong>解の残差</strong>
                {formatRjfmResiduals(candidate)}
              </p>
              {dmeWarning && (
                <p className="rjfm-dme-warning">
                  MZE 4 DME未満です。これは非ブロッキング注意で、候補自体は表示を継続します。
                </p>
              )}
              {candidate.constraints.length > 0 && (
                <ul className="rjfm-constraint-list" aria-label={`RWY ${candidate.runway} 制約判定`}>
                  {candidate.constraints.map((constraint) => (
                    <li
                      key={constraint.code}
                      className={constraint.passed
                        ? "is-passed"
                        : constraint.hard ? "is-failed" : "is-advisory"}
                    >
                      <span>
                        {constraint.passed ? "適合" : constraint.hard ? "不適合" : "注意"}
                      </span>
                      <p>{constraint.message}</p>
                    </li>
                  ))}
                </ul>
              )}
              {candidate.notes.length > 0 && (
                <ul className="rjfm-candidate-notes">
                  {candidate.notes.map((note) => <li key={note}>{note}</li>)}
                </ul>
              )}
            </article>
          );
        })}
      </div>
      <div className="rjfm-provenance">
        <div>
          <strong>適用資料</strong>
          {sourceDates.length ? (
            <dl>
              {sourceDates.map(([key, value]) => (
                <div key={key}>
                  <dt>{rjfmSourceLabel(key)}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p>資料日付なし</p>
          )}
        </div>
        <div>
          <strong>制限事項</strong>
          {guidance.limitations.length ? (
            <ul>{guidance.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
          ) : (
            <p>追加の制限事項なし</p>
          )}
        </div>
      </div>
    </section>
  );
}

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
  onRouteUseConfirmedChange,
  onPolygonRouteConfirmedChange,
  onConfirmRoute,
  onReplaceCheckPoints,
}: RouteWorkspaceProps) {
  const [mapHeight, setMapHeight] = useState(425);
  const [pickingCheckPoint, setPickingCheckPoint] = useState(false);
  const [pickedCoordinate, setPickedCoordinate] = useState<{
    latitude: number;
    longitude: number;
    revision: number;
  } | null>(null);
  const validPatternAltitude = patternAltitudeFtMsl(
    destinationPatternAltitudeFtMsl,
  );
  const nodes = useMemo(
    () => [...(project?.route_nodes ?? [])].sort((a, b) => a.sequence - b.sequence),
    [project],
  );
  const sections = useMemo(
    () => [...(project?.sections ?? [])].sort((a, b) => a.sequence - b.sequence),
    [project],
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
      ].map(([latitude, longitude]) => `${latitude},${longitude}`).join(";"),
    [
      checkPointPlanning.projections,
      checkPoints,
      coordinates,
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
    ],
    [
      checkPointPlanning.projections,
      checkPoints,
      coordinates,
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
          {coordinates.length >= 2 && (
            <Polyline positions={coordinates} pathOptions={{ color: "#173b5e", weight: 4 }} />
          )}
          {rjfmGuidance && rjfmGuidance.center_route.length >= 2 && (
            <Polyline
              positions={rjfmGuidance.center_route.map<[number, number]>((point) => [
                point.latitude_deg,
                point.longitude_deg,
              ])}
              pathOptions={{
                className: "rjfm-center-route",
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
              positions={item.path.map<[number, number]>((point) => [
                point.latitude_deg,
                point.longitude_deg,
              ])}
              pathOptions={{
                className: `rjfm-guidance-path ${rjfmCandidateClass(item)}`,
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
              center={[point.latitude_deg, point.longitude_deg]}
              radius={index === 1 ? 7 : 6}
              pathOptions={{
                className: "rjfm-center-marker",
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
          <CheckPointMapPicker active={pickingCheckPoint} onPick={handleMapPick} />
          <FitBounds
            coordinates={fitCoordinates}
            signature={coordinateSignature}
            viewportRevision={mapHeight}
          />
        </MapContainer>
        {rjfmGuidance && (
          <div
            className="rjfm-map-legend"
            role="group"
            aria-label="RJFMガイダンス凡例"
          >
            <strong>RJFM出発</strong>
            <span><i className="is-center" aria-hidden="true" />Newta CENTER</span>
            {visibleRjfmCandidates.map((item) => (
              <span key={`legend-${item.runway}`}>
                <i className={rjfmCandidateClass(item)} aria-hidden="true" />
                RWY {item.runway} {rjfmStatusLabels[item.status]}
              </span>
            ))}
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

      {rjfmGuidance && <RjfmGuidancePanel guidance={rjfmGuidance} />}

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

      <div className="table-scroll route-table-scroll">
        <table className="route-table">
          <thead>
            <tr>
              <th>POINT</th>
              <th>ROLE</th>
              <th>ALT ft MSL / MC候補</th>
              <th>PHASE</th>
            </tr>
          </thead>
          <tbody>
            {!project && candidate && (
              <tr>
                <td colSpan={4}>{candidate.vertexCount}点の形状を確認中</td>
              </tr>
            )}
            {!project && !candidate && (
              <tr>
                <td colSpan={4} className="empty-table-cell">
                  経路を取り込むとRoute点が表示されます
                </td>
              </tr>
            )}
            {nodes.map((node, index) => {
              const section = sectionByFromNode.get(node.id);
              const guidance = section
                ? guidanceBySection.get(section.id)
                : undefined;
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
              const requiresAltitudeReview =
                Boolean(guidance?.appliesToCruisingAltitudeInput) &&
                !isCandidateAltitude;
              return (
                <tr
                  key={node.id}
                  className={[
                    node.role === "VISUAL_REPORTING_POINT" ? "vrep-row" : "",
                    node.role === "DESTINATION" ? "destination-row" : "",
                    requiresAltitudeReview ? "altitude-review-row" : "",
                  ].filter(Boolean).join(" ")}
                >
                  <td>
                    <span className="point-index">{index}</span>
                    <strong>{node.name}</strong>
                  </td>
                  <td>{roleLabels[node.role] ?? node.role}</td>
                  <td className={requiresAltitudeReview ? "altitude-review-cell" : ""}>
                    {section ? (
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
                            {" / MC "}{Math.round(guidance.magneticCourseDeg)}
                            {requiresAltitudeReview ? "・候補外（要確認）" : ""}
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
                            ? "100～25,000 ftの範囲で100 ft単位の整数を入力してください。"
                            : "運用差がある場合は、今回使用するMSL高度へ編集してください。"}
                        </small>
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    {section ? (
                      <select
                        className="table-select"
                        aria-label={`${node.name}出発LegのPhase`}
                        value={section.phase}
                        onChange={(event) =>
                          onSectionChange(section.id, {
                            phase: event.target.value as FlightPhase,
                          })
                        }
                      >
                        {Object.entries(phaseLabels).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      "到着"
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
          onReplace={onReplaceCheckPoints}
        />
      )}
    </section>
  );
}
