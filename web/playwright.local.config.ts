import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "local-calculation.spec.ts",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  workers: 1,
  outputDir: "/tmp/autonavlog-issue117-playwright",
  reporter: "line",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.AUTONAVLOG_LOCAL_URL ?? "http://127.0.0.1:4174",
    trace: "retain-on-failure",
  },
});
