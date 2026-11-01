import { useEffect, useState } from "react";

interface CalculationProgressOverlayProps {
  title?: string;
  showElapsed?: boolean;
  percent: number;
  message: string;
  onSignOut?: () => void;
}

export function CalculationProgressOverlay({
  title = "NAV LOGを計算中",
  showElapsed = false,
  percent,
  message,
  onSignOut,
}: CalculationProgressOverlayProps) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => {
    if (!showElapsed) return;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsedSeconds(Math.floor((Date.now() - started) / 1_000)), 1_000);
    return () => window.clearInterval(timer);
  }, [showElapsed]);
  return (
    <div className="calculation-progress-backdrop" role="presentation">
      <section
        className="calculation-progress-dialog"
        role="status"
        aria-live="polite"
        aria-label={title}
      >
        <p className="calculation-progress-eyebrow">{title}</p>
        <div className="calculation-progress-heading">
          <strong>{message}</strong>
          <span>{percent}%</span>
        </div>
        <progress value={percent} max={100} aria-label={`${title} ${percent}%`} />
        {showElapsed && <small>経過時間 {Math.floor(elapsedSeconds / 60)}:{String(elapsedSeconds % 60).padStart(2, "0")}</small>}
        <small>このまま画面を開いてお待ちください。</small>
        {onSignOut && <button type="button" className="secondary-button" onClick={onSignOut}>ログアウト</button>}
      </section>
    </div>
  );
}
