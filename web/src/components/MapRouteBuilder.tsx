import rjfmReference from "../../../data/reference/rjfm/rjfm-reference.json";
import { useMemo, useState } from "react";
import { clusterCandidates, referencePoints, type ReferencePointSnapshot } from "../referencePoints";
import { CircleMarker, Marker, Polyline, Tooltip, useMapEvents } from "react-leaflet";
import { divIcon } from "leaflet";
import type { AirportOption } from "../types";
import { canConfirmMapDraft, type MapRoutePoint } from "../mapRouteDraft";

const omaruMarkerHint = `運用参照図より推定（未検証・誤差${rjfmReference.points.OMARU.estimated_error_nm}NM）`;
const omaruPositionNotice = `小丸は運用参照図からの未検証デジタイズによる推定位置（誤差${rjfmReference.points.OMARU.estimated_error_nm} NM）であり、精度は保証されません。`;

function candidateIcon(kind: "airport" | "vrep", selected: boolean) {
  return divIcon({
    className: `${kind}-draft-marker${selected ? " is-selected" : ""}`,
    html: `<span class="map-candidate-symbol" aria-hidden="true">${kind === "airport" ? "✈" : '<span class="vrep-inner-ring"></span>'}</span>`,
    // Keep a usable hit area around the smaller, centered map symbol.
    iconSize: [28, 28], iconAnchor: [14, 14],
  });
}
const candidateIcons = {
  airport: { unselected: candidateIcon("airport", false), selected: candidateIcon("airport", true) },
  vrep: { unselected: candidateIcon("vrep", false), selected: candidateIcon("vrep", true) },
};
export interface MapRouteBuilderProps {
  points: MapRoutePoint[];
  airports: AirportOption[];
  busy: boolean;
  onAirport: (airport: AirportOption) => void;
  onReference: (reference: ReferencePointSnapshot) => void;
  onPoint: (latitude: number, longitude: number) => void;
  onNamedPoint: (name: string, latitude: number, longitude: number) => void;
  onRemove: (id: string) => void;
  onClear: () => void;
  onConfirm: () => void;
}
export function MapDraftLayers({ points, airports, busy, onAirport, onReference, onPoint, onNamedPoint }: MapRouteBuilderProps) {
  const [zoom, setZoom] = useState<number | null>(null);
  const map = useMapEvents({
    zoomend: () => setZoom(map.getZoom()),
    click: event => {
      if (!busy && points.length > 0 && points.length < 500) onPoint(event.latlng.lat, event.latlng.wrap().lng);
    },
  });
  const currentZoom = zoom ?? map.getZoom();
  const omaru = rjfmReference.points.OMARU.position;
  const omaruSelected = points.some(point => point.latitude_deg === omaru.latitude_deg && point.longitude_deg === omaru.longitude_deg);
  const selectedIds = new Set(points.flatMap(point => point.airport_id ? [`airport:${point.airport_id}`]
    : point.reference_snapshot ? [point.reference_snapshot.reference_id] : []));
  type Candidate = { id: string; latitude: number; longitude: number; airport?: AirportOption; reference?: ReferencePointSnapshot; routePoint?: boolean };
  const candidates = useMemo<Candidate[]>(() => [
    { id: "RJFM:OMARU", latitude: omaru.latitude_deg, longitude: omaru.longitude_deg, routePoint: true },
    ...airports.map(airport => ({ id: `airport:${airport.id}`, latitude: airport.latitudeDeg, longitude: airport.longitudeDeg, airport })),
    ...referencePoints.map(reference => ({ id: reference.reference_id, latitude: reference.latitude_deg, longitude: reference.longitude_deg, reference })),
  ], [airports]);
  if (omaruSelected) selectedIds.add("RJFM:OMARU");
  const groups = clusterCandidates(candidates.filter(candidate => !selectedIds.has(candidate.id)).map(candidate => ({
    ...candidate, ...map.project([candidate.latitude, candidate.longitude], currentZoom),
  })));
  // Selected reference markers use the saved selection, even if the master has changed.
  const selected = new Map<string, Candidate>();
  for (const point of points) {
    if (point.reference_snapshot) {
      const reference = point.reference_snapshot;
      selected.set(reference.reference_id, { id: reference.reference_id, latitude: point.latitude_deg, longitude: point.longitude_deg, reference });
    } else if (point.airport_id) {
      const airport = airports.find(item => item.id === point.airport_id);
      if (airport) selected.set(`airport:${airport.id}`, { id: `airport:${airport.id}`, latitude: point.latitude_deg, longitude: point.longitude_deg, airport });
    }
  }
  if (omaruSelected) selected.set("RJFM:OMARU", { id: "RJFM:OMARU", latitude: omaru.latitude_deg, longitude: omaru.longitude_deg, routePoint: true });
  const renderPoint = (candidate: Candidate) => {
    const { airport, reference, routePoint } = candidate;
    const title = routePoint ? "小丸" : airport ? `空港 ${airport.icao} ${airport.name}` : `VREP ${reference!.name} ${reference!.name_ja}`;
    const icon = candidateIcons[airport ? "airport" : "vrep"][selectedIds.has(candidate.id) ? "selected" : "unselected"];
    return <Marker key={candidate.id} icon={icon}
      position={[candidate.latitude, candidate.longitude]} title={title}
      bubblingMouseEvents={false} eventHandlers={{ click: () => {
        if (busy || points.length >= 500) return;
        if (airport) onAirport(airport);
        else if (points.length > 0 && routePoint) onNamedPoint("小丸", candidate.latitude, candidate.longitude);
        else if (points.length > 0 && reference) onReference(reference);
      } }}>
      <Tooltip>{routePoint ? <>小丸<br />{omaruMarkerHint}</> : airport ? `${airport.icao} ${airport.name}` : `${reference!.name} ${reference!.name_ja}`}</Tooltip>
    </Marker>;
  };
  return <>
    {points.length > 1 && <Polyline positions={points.map<[number, number]>(point => [point.latitude_deg, point.longitude_deg])}
      pathOptions={{ color: "#173b5e", weight: 4 }} interactive={false} />}
    {points.filter(point => !point.airport_id && !point.reference_snapshot).map(point => <CircleMarker key={point.id}
      center={[point.latitude_deg, point.longitude_deg]} radius={6} interactive={false}
      pathOptions={{ color: "#173b5e", fillColor: "white", fillOpacity: 1 }}>
      <Tooltip permanent>{point.name}</Tooltip>
    </CircleMarker>)}
    {groups.map(group => group.length === 1 ? renderPoint(group[0]!) : <Marker key={group.map(point => point.id).join("|")}
      position={[group.reduce((sum, point) => sum + point.latitude, 0) / group.length, group.reduce((sum, point) => sum + point.longitude, 0) / group.length]}
      icon={divIcon({ className: "candidate-cluster", html: `<span data-candidates="${group.map(point => point.id).join(" ")}">${group.length}</span>`, iconSize: [32, 32], iconAnchor: [16, 16] })}
      title={`${group.length}件の地点を拡大`} bubblingMouseEvents={false} eventHandlers={{ click: () => {
        map.fitBounds(group.map<[number, number]>(point => [point.latitude, point.longitude]), { padding: [45, 45], maxZoom: Math.min(map.getMaxZoom(), currentZoom + 3) });
      } }} />)}
    {[...selected.values()].map(renderPoint)}
  </>;
}
export function RouteStrip({ points, busy, onRemove, onClear, onConfirm }: MapRouteBuilderProps) {
  return <div className="map-route-controls">
    <p className="field-hint">VREPはAD2のARPから算出した参考位置です。公示座標ではなく精度は保証されません。<br />{omaruPositionNotice}</p>
    <p>{points.length ? "飛行順に地点を追加し、最後の空港で経路を確定してください。" : "地図上の空港を選んで、出発地（FROM）を設定してください。"}</p>
    {points.length > 0 && <ol className="route-strip" aria-label="経路リスト">
      {points.map((point, index) => <li key={point.id}>
        <span>{index + 1}. {point.name}{index === 0 ? " (FROM)" : ""}</span>
        {index > 0 && <button type="button" disabled={busy} onClick={() => onRemove(point.id)}
          aria-label={`${index + 1}番目の${point.name}を削除`}>削除</button>}
      </li>)}
    </ol>}
    {points.length >= 500 && <p role="status">経路は500地点までです。</p>}
    <div className="map-route-actions">
      <button className="secondary-button" disabled={busy || points.length === 0} onClick={onClear}>経路をクリア</button>
      <button className="primary-button" disabled={busy || !canConfirmMapDraft(points)} onClick={onConfirm}>経路を確定</button>
    </div>
  </div>;
}
