// Build-time developer setting; no user-facing runtime selector or HTTP fallback.
export const localMode = import.meta.env?.VITE_CALCULATION_MODE === "local";
