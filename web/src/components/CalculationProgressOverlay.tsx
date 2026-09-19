interface CalculationProgressOverlayProps {
  title?: string;
  percent: number;
  message: string;
  onSignOut?: () => void;
}

export function CalculationProgressOverlay({
  title = "NAV LOGを計算中",
  percent,
  message,
  onSignOut,
}: CalculationProgressOverlayProps) {
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
        <small>このまま画面を開いてお待ちください。</small>
        {onSignOut && <button type="button" className="secondary-button" onClick={onSignOut}>ログアウト</button>}
      </section>
    </div>
  );
}
