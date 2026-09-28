import { defineConfig, devices } from "@playwright/test";
import local from "./playwright.local.config";

export default defineConfig(local, {
  testMatch: "browser-acceptance.spec.ts",
  timeout: 180_000,
  outputDir: "/tmp/autonavlog-issue122-browser",
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "desktop-webkit", use: { ...devices["Desktop Safari"] } },
    { name: "iphone-webkit", use: { ...devices["iPhone 13"], browserName: "webkit" } },
    { name: "ipad-webkit", use: { ...devices["iPad Pro 11"], browserName: "webkit" } },
    { name: "android-chromium", use: { ...devices["Pixel 7"], browserName: "chromium" } },
    { name: "android-tablet-chromium", use: { ...devices["Galaxy Tab S9"], browserName: "chromium" } },
  ],
});
