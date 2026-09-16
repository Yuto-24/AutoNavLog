import { defineConfig } from "@playwright/test";

// Adapter and architecture tests require no browser, Docker, or application server.
export default defineConfig({
  testDir: "./e2e", testMatch: ["taf-unit.spec.ts", "application.spec.ts", "auth-unit.spec.ts", "persistence-unit.spec.ts", "platform-unit.spec.ts", "local-weather-unit.spec.ts", "information-state.spec.ts"], workers: 1, outputDir: "/tmp/autonavlog-application-tests", reporter: "line",
});
