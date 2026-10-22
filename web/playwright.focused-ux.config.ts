import { defineConfig, devices } from "@playwright/test";
import local from "./playwright.local.config";

// Hosted Local-browser CI supplies the built static app; no Legacy/FastAPI calls.
export default defineConfig(local, {
  testMatch: ["map-checkpoint.spec.ts", "altitude-layout.spec.ts"],
  timeout: 180_000,
  outputDir: "/tmp/autonavlog-focused-ux-browser",
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "desktop-webkit", use: { ...devices["Desktop Safari"] } },
    {
      name: "iphone-webkit",
      testMatch: "map-checkpoint.spec.ts",
      use: { ...devices["iPhone 13"], browserName: "webkit" },
    },
  ],
});
