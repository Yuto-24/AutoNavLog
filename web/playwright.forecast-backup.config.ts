import { defineConfig } from "@playwright/test";
import local from "./playwright.local.config";

// Requires the explicit matching fixture server and backup-enabled Local build.
export default defineConfig({ ...local, testMatch: ["forecast-backup.spec.ts"],
  outputDir: "/tmp/autonavlog-forecast-backup-browser" });
