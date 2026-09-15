import type { PlatformFile } from "./platform";
import type { WebState } from "./types";

// Export the displayed calculation snapshot, without relabelling it with edited inputs.
// This is a result document, not a Project backup or a new persistence format.
export function exportNavLog(state: WebState): PlatformFile {
  if (!state.outcome) throw new Error("出力するNAV LOGがありません。");
  return {
    name: "autonavlog-navlog.json",
    mediaType: "application/json",
    content: new TextEncoder().encode(JSON.stringify({
      format: "autonavlog.navlog", version: 1,
      outcome: state.outcome, destinationWind: state.destinationWind,
    }, null, 2) + "\n"),
  };
}
