import { defineConfig } from "@playwright/test";

// Adapter and architecture tests require no browser, Docker, or application server.
export default defineConfig({
  testDir: "./e2e", testMatch: "application.spec.ts", workers: 1, outputDir: "/tmp/autonavlog-application-tests", reporter: "line",
});
