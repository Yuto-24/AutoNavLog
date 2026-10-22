import { defineConfig, devices } from "@playwright/test";
import local from "./playwright.local.config";

export default defineConfig(local, {
  testMatch: "platform.spec.ts",
  grep: /Clipboard preserves click activation|Clipboard read failure clears previous paste|pending platform Paste confirmation|confirmed route can resume a pasted draft/,
  outputDir: "/tmp/autonavlog-clipboard-playwright",
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
