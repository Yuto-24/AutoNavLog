import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", testMatch: "sync.spec.ts", timeout: 180_000, expect: { timeout: 40_000 }, workers: 1,
  reporter: "line", outputDir: process.env.AUTONAVLOG_SYNC_OUTPUT ?? "/tmp/autonavlog-sync-browser",
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], channel: process.env.AUTONAVLOG_SYNC_CHANNEL } },
    { name: "ipad-webkit", use: { ...devices["iPad Pro 11"], browserName: "webkit" } },
    { name: "iphone-webkit", use: { ...devices["iPhone 13"], browserName: "webkit" } }],
  use: { baseURL: "http://127.0.0.1:5178", trace: "retain-on-failure" },
  webServer: { command: "VITE_CALCULATION_MODE=local npx vite --host 127.0.0.1 --port 5178", url: "http://127.0.0.1:5178", reuseExistingServer: !process.env.CI },
});
