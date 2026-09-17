import { defineConfig } from "@playwright/test";
export default defineConfig({ testDir: "./e2e", testMatch: "firestore-sync.spec.ts", workers: 1,
  timeout: 60_000, reporter: "line", outputDir: "/tmp/autonavlog-firestore-tests" });
