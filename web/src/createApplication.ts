import type { PlatformCapabilities } from "./platform";
import type { AutoNavLogApplication } from "./application";
import { LegacyApplication } from "./legacyApplication";

export async function createApplication(platform: PlatformCapabilities): Promise<AutoNavLogApplication> {
  // Literal build condition keeps Python/Worker assets out of the Legacy build.
  if (import.meta.env.VITE_CALCULATION_MODE === "local") {
    const { LocalApplication } = await import("./localApplication");
    platform.persistence.requestRetention();
    return new LocalApplication(platform.persistence.createProjectRepository);
  }
  return new LegacyApplication();
}
