import { CircleMarker, Marker, Polyline, Tooltip, useMapEvents } from "react-leaflet";
import { divIcon } from "leaflet";
import type { AirportOption } from "../types";
import { canConfirmMapDraft, type MapRoutePoint } from "../mapRouteDraft";

const airportIcon = divIcon({ className: "airport-draft-marker", html: '<span aria-hidden="true">✈</span>', iconSize: [28, 28], iconAnchor: [14, 14] });
export interface MapRouteBuilderProps {
  points: MapRoutePoint[];
  airports: AirportOption[];
  busy: boolean;
  onAirport: (airport: AirportOption) => void;
  onPoint: (latitude: number, longitude: number) => void;
  onRemove: (id: string) => void;
  onClear: () => void;
  onConfirm: () => void;
}
export function MapDraftLayers({ points, airports, busy, onAirport, onPoint }: MapRouteBuilderProps) {
  useMapEvents({ click: event => {
    if (!busy && points.length > 0 && points.length < 500) onPoint(event.latlng.lat, event.latlng.wrap().lng);
  } });
  return <>
    {points.length > 1 && <Polyline positions={points.map<[number, number]>(point => [point.latitude_deg, point.longitude_deg])}
      pathOptions={{ color: "#173b5e", weight: 4 }} interactive={false} />}
    {points.filter(point => !point.airport_id).map(point => <CircleMarker key={point.id}
      center={[point.latitude_deg, point.longitude_deg]} radius={6} interactive={false}
      pathOptions={{ color: "#173b5e", fillColor: "white", fillOpacity: 1 }}>
      <Tooltip permanent>{point.name}</Tooltip>
    </CircleMarker>)}
    {airports.map(airport => <Marker key={airport.id} icon={airportIcon}
      position={[airport.latitudeDeg, airport.longitudeDeg]} title={`空港 ${airport.icao} ${airport.name}`}
      bubblingMouseEvents={false} eventHandlers={{ click: () => { if (!busy && points.length < 500) onAirport(airport); } }}>
      <Tooltip>{airport.icao} {airport.name}</Tooltip>
    </Marker>)}
  </>;
}
export function RouteStrip({ points, busy, onRemove, onClear, onConfirm }: MapRouteBuilderProps) {
  return <div className="map-route-controls">
    <p>{points.length ? "飛行順に地点を追加し、最後の空港で経路を確定してください。" : "空港Markerを選んでFROMを設定してください。"}</p>
    {points.length > 0 && <ol className="route-strip" aria-label="Route Strip">
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
