interface CalculationProgressOverlayProps {
  percent: number;
  message: string;
}

export function CalculationProgressOverlay({
  percent,
  message,
}: CalculationProgressOverlayProps) {
  return (
    <div className="calculation-progress-backdrop" role="presentation">
      <section
        className="calculation-progress-dialog"
        role="status"
        aria-live="polite"
        aria-label="NAV LOGを計算中"
      >
        <p className="calculation-progress-eyebrow">NAV LOGを計算中</p>
        <div className="calculation-progress-heading">
          <strong>{message}</strong>
          <span>{percent}%</span>
        </div>
        <progress value={percent} max={100} aria-label={`計算進捗 ${percent}%`} />
        <small>このまま画面を開いてお待ちください。</small>
      </section>
    </div>
  );
}
