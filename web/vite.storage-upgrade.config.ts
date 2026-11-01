import { defineConfig } from "vite";
import { resolve } from "node:path";
export default defineConfig({
  define: { __LOCAL_MANIFEST_SHA256__: '""', __PYODIDE_VERSION__: '"0.27.7"' },
  plugins: [{ name: "historical-repository-dependencies", enforce: "pre", resolveId(source, importer) {
    if (importer?.includes("/e2e/fixtures/v1.20.1/") && source.startsWith("./") && source !== "./localProjectRepository") {
      return resolve("src", source + ".ts");
    }
  } }],
  server: { host: "127.0.0.1", port: 5189, strictPort: true },
});
