import { useEffect, useMemo } from "react";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import type {
  AltitudeGuidance,
  CalculationOutcome,
  FlightPhase,
  NavSection,
  Project,
  RouteCandidate,
} from "../types";

interface RouteWorkspaceProps {
  candidate: RouteCandidate | null;
  project: Project | null;
  outcome: CalculationOutcome | null;
  altitudeGuidance: AltitudeGuidance;
  altitudeInputs: Record<string, string>;
  onAltitudeInputChange: (sectionId: string, value: string) => void;
  onSectionChange: (sectionId: string, changes: Partial<NavSection>) => void;
}

const phaseLabels: Record<FlightPhase, string> = {
  CLIMB: "上昇",
  CRUISE: "巡航",
  DESCENT: "降下",
  VISUAL_ARRIVAL: "場周進入",
};

const roleLabels: Record<string, string> = {
  AIRPORT: "出発",
  ROUTE_POINT: "経路点",
  VISUAL_REPORTING_POINT: "VREP",
  DESTINATION: "到着",
};

function FitBounds({
  coordinates,
  signature,
}: {
  coordinates: [number, number][];
  signature: string;
}) {
  const map = useMap();
  useEffect(() => {
    if (coordinates.length >= 2) {
      map.fitBounds(coordinates as LatLngBoundsExpression, { padding: [28, 28] });
    } else if (coordinates.length === 1 && coordinates[0]) {
      map.setView(coordinates[0], 10);
    }
  }, [map, signature]);
  return null;
}

export function RouteWorkspace({
  candidate,
  project,
  outcome,
  altitudeGuidance,
  altitudeInputs,
  onAltitudeInputChange,
  onSectionChange,
}: RouteWorkspaceProps) {
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

  const coordinates = useMemo<[number, number][]>(
    () =>
      nodes.length
        ? nodes.map((node) => [node.latitude_deg, node.longitude_deg])
        : candidate?.coordinates ?? [],
    [candidate, nodes],
  );
  const coordinateSignature = useMemo(
    () => coordinates.map(([latitude, longitude]) => `${latitude},${longitude}`).join(";"),
    [coordinates],
  );

  return (
    <section className="route-workspace" aria-label="経路地図とLeg設定">
      <div className="workspace-heading">
        <div>
          <h2>経路</h2>
          <p>
            {project
              ? `${project.departure_airport_id} → ${project.destination_airport_id}`
              : candidate?.name ?? "形状を選択すると地図へ表示します"}
          </p>
        </div>
        {project && <span className="route-count">{nodes.length}点 / {sections.length} Leg</span>}
      </div>
      <div className="map-frame">
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
          <FitBounds coordinates={coordinates} signature={coordinateSignature} />
        </MapContainer>
        {!coordinates.length && (
          <div className="map-empty">
            <strong>経路はまだありません</strong>
            <span>KML/KMZを読み込み、飛行経路にする形状を選択してください。</span>
          </div>
        )}
      </div>

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
              const isCandidateAltitude = Boolean(
                guidance?.candidateAltitudesFtMsl.some(
                  (candidateAltitude) =>
                    candidateAltitude === section?.planned_altitude_ft_msl,
                ),
              );
              const requiresAltitudeReview =
                section?.phase === "CRUISE" && !isCandidateAltitude;
              return (
                <tr
                  key={node.id}
                  className={[
                    node.role === "VISUAL_REPORTING_POINT" ? "vrep-row" : "",
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
                        {guidance && section.phase === "CRUISE" && (
                          <select
                            className="table-select altitude-candidate-select"
                            aria-label={node.name + "出発Legの巡航高度候補"}
                            value={
                              isCandidateAltitude
                                ? String(section.planned_altitude_ft_msl)
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
                            MC {Math.round(guidance.magneticCourseDeg)}°
                            {requiresAltitudeReview ? "・候補外（要確認）" : ""}
                          </small>
                        )}
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
          {altitudeGuidance.legalThresholdNote}
          <br />
          {altitudeGuidance.terrainLimitationNote}
        </p>
      )}
    </section>
  );
}
