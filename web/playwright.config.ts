import { defineConfig, devices } from "@playwright/test";

const externalBaseUrl = process.env.AUTONAVLOG_WEB_URL;
const localBaseUrl = "http://127.0.0.1:8123";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: externalBaseUrl ?? localBaseUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: externalBaseUrl
    ? undefined
    : {
        command: "docker compose up --build",
        cwd: "..",
        env: {
          AUTONAVLOG_TRUSTED_LOCAL_IDENTITY: "playwright-local",
        },
        url: `${localBaseUrl}/healthz`,
        reuseExistingServer: false,
        timeout: 180_000,
      },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
