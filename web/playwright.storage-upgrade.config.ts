import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", testMatch: "storage-upgrade.spec.ts", workers: 1, timeout: 60_000,
  outputDir: "/tmp/autonavlog-storage-upgrade", reporter: "line",
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit", use: { ...devices["iPad Pro 11"], browserName: "webkit" } }],
  use: { baseURL: "http://127.0.0.1:5189", trace: "retain-on-failure" },
  webServer: { command: "npx vite --config vite.storage-upgrade.config.ts", url: "http://127.0.0.1:5189", reuseExistingServer: false },
});
