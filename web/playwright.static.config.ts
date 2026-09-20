import { defineConfig } from "@playwright/test";
import local from "./playwright.local.config";
export default defineConfig(local, {
  testMatch: ["static-production.spec.ts", "local-calculation.spec.ts", "persistence.spec.ts", "platform.spec.ts", "session.spec.ts"],
  grep: /production artifact|static FTD Golden|browser process restart|local asset|picker opens compressed KMZ|reload preserves raw invalid/,
  outputDir: "/tmp/autonavlog-static-browser",
  use: { baseURL: process.env.AUTONAVLOG_LOCAL_URL ?? "http://127.0.0.1:4121" },
});
