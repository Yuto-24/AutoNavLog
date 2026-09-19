import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", testMatch: "migration.spec.ts", timeout: 180_000,
  expect: { timeout: 60_000 }, workers: 1, reporter: "line", outputDir: "/tmp/autonavlog-migration-browser",
  projects: [{ name: "chromium", use: devices["Desktop Chrome"] },
    { name: "ipad-webkit", use: { ...devices["iPad Pro 11"], browserName: "webkit" } }],
  use: { baseURL: "http://127.0.0.1:5179", trace: "retain-on-failure" },
  webServer: [{ command: "npx vite --host 127.0.0.1 --port 5179", url: "http://127.0.0.1:5179", reuseExistingServer: !process.env.CI,
    env: { VITE_CALCULATION_MODE: "local", VITE_LEGACY_MIGRATION_URL: "http://127.0.0.1:8186" } },
    { command: "npx vite --host 127.0.0.1 --port 5180", url: "http://127.0.0.1:5180", reuseExistingServer: !process.env.CI,
      env: { VITE_CALCULATION_MODE: "legacy" } }],
});
