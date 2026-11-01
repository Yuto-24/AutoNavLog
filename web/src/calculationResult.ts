import type { Project } from "./types";

/** Keep only locally editable arrival inputs when an older calculation finishes. */
export function preserveArrivalInputs(next: Project, current: Project): Project {
  const draft = current.metadata.ui_state?.arrival_plan;
  const result = next.metadata.ui_state?.arrival_plan;
  if (current.id !== next.id || !draft || !result) return next;
  return {
    ...next,
    metadata: {
      ...next.metadata,
      ui_state: {
        ...next.metadata.ui_state,
        arrival_plan: {
          ...result,
          altitude_mode: draft.altitude_mode,
          manual_vrep_altitude_ft_msl: draft.manual_vrep_altitude_ft_msl,
          manual_override_reason: draft.manual_override_reason,
        },
      },
    },
  };
}
