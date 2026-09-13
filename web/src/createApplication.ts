import type { AutoNavLogApplication } from "./application";
import { LegacyApplication } from "./legacyApplication";

export async function createApplication(): Promise<AutoNavLogApplication> {
  // Literal build condition keeps Python/Worker assets out of the Legacy build.
  if (import.meta.env.VITE_CALCULATION_MODE === "local") {
    const { LocalApplication } = await import("./localApplication");
    return new LocalApplication();
  }
  return new LegacyApplication();
}
