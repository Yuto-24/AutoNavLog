import { defineConfig, devices } from "@playwright/test";
import local from "./playwright.local.config";

export default defineConfig(local, {
  testMatch: "navlog-scroll.spec.ts",
  timeout: 180_000,
  outputDir: "/tmp/autonavlog-navlog-scroll-browser",
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "ipad-webkit", use: { ...devices["iPad Pro 11"], browserName: "webkit" } },
    { name: "iphone-webkit", use: { ...devices["iPhone 13"], browserName: "webkit" } },
  ],
});
