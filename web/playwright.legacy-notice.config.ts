import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e", testMatch: "legacy-notice.spec.ts", workers: 1,
  use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:5188" },
  webServer: { command: "npx vite --host 127.0.0.1 --port 5188", url: "http://127.0.0.1:5188", reuseExistingServer: false },
});
