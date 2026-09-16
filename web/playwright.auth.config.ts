import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e", testMatch: "auth.spec.ts", timeout: 120_000,
  expect: { timeout: 30_000 }, workers: 1, reporter: "line", outputDir: "/tmp/autonavlog-auth-tests",
  use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:5176", trace: "retain-on-failure" },
  webServer: { command: "VITE_CALCULATION_MODE=local npx vite --host 127.0.0.1 --port 5176", url: "http://127.0.0.1:5176", reuseExistingServer: !process.env.CI },
});
