import { defineConfig, loadEnv } from "vite";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const local = loadEnv(mode, ".").VITE_CALCULATION_MODE === "local";
  const manifest = local ? readFileSync("public-local/local/manifest.json") : undefined;
  if (manifest) {
    const metadata = JSON.parse(manifest.toString());
    const version = readFileSync("../VERSION", "utf8").trim();
    const pyodide = JSON.parse(readFileSync("package.json", "utf8")).dependencies.pyodide;
    if (metadata.version !== version || metadata.pyodideVersion !== pyodide) {
      throw new Error("Local assets are stale; run npm run prepare:local");
    }
  }
  return {
    define: {
      __LOCAL_MANIFEST_SHA256__: JSON.stringify(manifest ? createHash("sha256").update(manifest).digest("hex") : ""),
      __PYODIDE_VERSION__: JSON.stringify(manifest ? JSON.parse(manifest.toString()).pyodideVersion : ""),
    },
    plugins: [react()],
    publicDir: local ? "public-local" : "public",
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: false,
    },
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": "http://127.0.0.1:8000",
        "/healthz": "http://127.0.0.1:8000",
      },
    },
  };
});
