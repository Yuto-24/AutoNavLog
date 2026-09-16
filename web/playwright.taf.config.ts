import { defineConfig } from "@playwright/test";
import local from "./playwright.local.config";

// Build with VITE_TAF_PROXY_URL before this suite. It intercepts the configured proxy.
export default defineConfig({ ...local, testMatch: ["taf.spec.ts"], outputDir: "/tmp/autonavlog-taf-playwright" });
