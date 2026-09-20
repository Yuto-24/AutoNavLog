import { execFileSync } from "node:child_process";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnv } from "vite";
import { inventory, checkArtifact, sourceDirty } from "./static-artifact.mjs";

const args = process.argv.slice(2);
if (args.some(arg => arg !== "--allow-dirty")) throw new Error("Unknown build:static option");
const allowDirty = args.includes("--allow-dirty");
const env = { ...loadEnv("production", ".", ""), ...process.env, VITE_CALCULATION_MODE: "local" };
if (env.AUTONAVLOG_TEST_FIXTURES === "1") throw new Error("Production cannot contain test fixtures");
if (!env.AUTONAVLOG_MSM_FEED) throw new Error("AUTONAVLOG_MSM_FEED is required for production");
for (const name of ["VITE_FIREBASE_API_KEY", "VITE_FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_PROJECT_ID", "VITE_FIREBASE_APP_ID", "VITE_TAF_PROXY_URL"]) {
  if (!env[name]?.trim()) throw new Error(name + " is required for production");
}
for (const name of ["VITE_TAF_PROXY_URL", "VITE_LEGACY_MIGRATION_URL"]) {
  if (!env[name]) continue;
  const url = new URL(env[name]);
  if ((name === "VITE_TAF_PROXY_URL" && url.pathname !== "/taf") || url.protocol !== "https:" || url.username || url.password || url.hash || url.search) {
    throw new Error(name + " must be a public HTTPS URL without credentials, query or fragment");
  }
}
const run = args => execFileSync("npm", args, { env, stdio: "inherit" });
run(["run", "prepare:local"]);
run(["run", "generate:release-notes"]);
run(["exec", "--", "tsc", "-b"]);
run(["exec", "--", "vite", "build", "--outDir", "dist-static"]);
copyFileSync("static/_headers", "dist-static/_headers");
copyFileSync("static/404.html", "dist-static/404.html");
const manifest = JSON.parse(readFileSync("dist-static/local/manifest.json"));
const release = {
  version: manifest.version, pyodideVersion: manifest.pyodideVersion,
  commit: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
  dirty: sourceDirty(resolve("..")),
  // Public Web configuration, never tokens or server credentials.
  configuration: Object.fromEntries(["VITE_FIREBASE_API_KEY", "VITE_FIREBASE_AUTH_DOMAIN",
    "VITE_FIREBASE_PROJECT_ID", "VITE_FIREBASE_APP_ID", "VITE_TAF_PROXY_URL", "VITE_LEGACY_MIGRATION_URL"]
    .filter(name => env[name]).map(name => [name, env[name]])),
  files: inventory(resolve("dist-static")),
};
writeFileSync("dist-static/release.json", JSON.stringify(release, null, 2) + "\n");
console.log(JSON.stringify(checkArtifact(resolve("dist-static"), { allowDirty }), null, 2));
