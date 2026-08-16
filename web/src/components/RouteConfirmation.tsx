interface RouteConfirmationProps {
  visible: boolean;
  polygon: boolean;
  routeUseConfirmed: boolean;
  polygonRouteConfirmed: boolean;
  canConfirm: boolean;
  busy: boolean;
  onRouteUseConfirmedChange: (checked: boolean) => void;
  onPolygonRouteConfirmedChange: (checked: boolean) => void;
  onConfirm: () => void;
}

export function RouteConfirmation({
  visible,
  polygon,
  routeUseConfirmed,
  polygonRouteConfirmed,
  canConfirm,
  busy,
  onRouteUseConfirmedChange,
  onPolygonRouteConfirmedChange,
  onConfirm,
}: RouteConfirmationProps) {
  if (!visible) return null;
  return (
    <section className="route-confirmation" aria-label="経路確認">
      <div className="confirmation-box">
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={routeUseConfirmed}
            onChange={(event) => onRouteUseConfirmedChange(event.target.checked)}
          />
          <span>地図とKML記載順を確認しました</span>
        </label>
        {polygon && (
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={polygonRouteConfirmed}
              onChange={(event) =>
                onPolygonRouteConfirmedChange(event.target.checked)
              }
            />
            <span>Polygon境界の開始点・進行方向を記載順で使います</span>
          </label>
        )}
      </div>
      <button
        className="primary-button full-width"
        type="button"
        onClick={onConfirm}
        disabled={!canConfirm || busy}
      >
        経路を確定
      </button>
    </section>
  );
}
