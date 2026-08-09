import { Check } from "lucide-react";

const steps = ["経路", "飛行計画", "確認・出力"] as const;

export function ProgressRail({ activeStep }: { activeStep: number }) {
  return (
    <nav className="progress-rail" aria-label="作業進捗">
      {steps.map((label, index) => {
        const step = index + 1;
        const completed = step < activeStep;
        const active = step === activeStep;
        return (
          <div
            className={`progress-step${active ? " is-active" : ""}${
              completed ? " is-complete" : ""
            }`}
            key={label}
            aria-current={active ? "step" : undefined}
          >
            <span className="progress-number">
              {completed ? <Check aria-hidden="true" size={16} /> : step}
            </span>
            <span>{label}</span>
            {step < steps.length && <span className="progress-line" aria-hidden="true" />}
          </div>
        );
      })}
    </nav>
  );
}
