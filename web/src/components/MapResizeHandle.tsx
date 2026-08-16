import { useRef } from "react";
import type { KeyboardEvent, PointerEvent } from "react";

interface MapResizeHandleProps {
  value: number;
  min: number;
  max: number;
  onChange: (height: number) => void;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function MapResizeHandle({
  value,
  min,
  max,
  onChange,
}: MapResizeHandleProps) {
  const drag = useRef<{ pointerId: number; startY: number; startHeight: number } | null>(
    null,
  );

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    let next: number | null = null;
    if (event.key === "ArrowDown") next = value + 25;
    if (event.key === "ArrowUp") next = value - 25;
    if (event.key === "PageDown") next = value + 100;
    if (event.key === "PageUp") next = value - 100;
    if (event.key === "Home") next = min;
    if (event.key === "End") next = max;
    if (next === null) return;
    event.preventDefault();
    onChange(clamp(next, min, max));
  };

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    onChange(clamp(current.startHeight + event.clientY - current.startY, min, max));
  };

  const finishDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
  };

  return (
    <div
      className="map-resize-handle"
      role="separator"
      aria-label="地図の高さを調整"
      aria-orientation="horizontal"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-controls="route-map-frame"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      onPointerDown={(event) => {
        drag.current = {
          pointerId: event.pointerId,
          startY: event.clientY,
          startHeight: value,
        };
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={handlePointerMove}
      onPointerUp={finishDrag}
      onPointerCancel={finishDrag}
    >
      <span aria-hidden="true" />
    </div>
  );
}
