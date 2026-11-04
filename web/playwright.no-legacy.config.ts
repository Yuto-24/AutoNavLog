import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e", testMatch: "no-legacy.spec.ts", workers: 1,
  timeout: 240_000, expect: { timeout: 45_000 }, reporter: "line",
  outputDir: "/tmp/autonavlog-no-legacy-browser",
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"],
      launchOptions: { executablePath: process.env.AUTONAVLOG_CHROMIUM_EXECUTABLE } } },
    { name: "ipad-webkit", use: { ...devices["iPad Pro 11"], browserName: "webkit" } },
    { name: "iphone-webkit", use: { ...devices["iPhone 13"], browserName: "webkit" } },
  ],
  use: { baseURL: "http://127.0.0.1:5188", serviceWorkers: "block", trace: "retain-on-failure" },
  webServer: [{
    command: "npx vite --host 127.0.0.1 --port 5188", url: "http://127.0.0.1:5188",
    reuseExistingServer: false,
    env: { VITE_CALCULATION_MODE: "local", VITE_LEGACY_MIGRATION_URL: "" },
  }, {
    command: "python3 -m http.server 4188 --bind 127.0.0.1 --directory dist-static",
    url: "http://127.0.0.1:4188", reuseExistingServer: false,
  }],
});
