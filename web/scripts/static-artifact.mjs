import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, lstatSync } from "node:fs";
import { join, relative } from "node:path";

// Release-time snapshot; recheck the linked official limits before every release.
export const limits = { checked: "2026-09-19", files: 20_000, assetBytes: 25 * 1024 * 1024,
  source: "https://developers.cloudflare.com/pages/platform/limits/" };
export const sha256 = bytes => createHash("sha256").update(bytes).digest("hex");
export function inventory(root) {
  const files = {};
  function walk(directory) {
    for (const entry of readdirSync(directory).sort()) {
      const path = join(directory, entry);
      const name = relative(root, path).replaceAll("\\", "/");
      const stat = lstatSync(path);
      if (stat.isSymbolicLink()) throw new Error("Symlink in artifact: " + name);
      if (stat.isDirectory()) walk(path);
      else {
        if (stat.size > limits.assetBytes) throw new Error("Pages asset exceeds 25 MiB: " + name);
        files[name] = { bytes: stat.size, sha256: sha256(readFileSync(path)) };
      }
    }
  }
  walk(root);
  if (Object.keys(files).length > limits.files) throw new Error("Pages file count exceeds 20000");
  return files;
}
export function checkArtifact(root, { freshWeather = true, allowDirty = false } = {}) {
  const files = inventory(root);
  const release = JSON.parse(readFileSync(join(root, "release.json")));
  if (typeof release.dirty !== "boolean") throw new Error("Missing or invalid source dirty flag");
  if (release.dirty && !allowDirty) throw new Error("Dirty source artifact cannot pass a production check");
  const recorded = { ...files };
  delete recorded["release.json"];
  if (JSON.stringify(recorded) !== JSON.stringify(release.files)) throw new Error("Artifact inventory mismatch");
  for (const required of ["index.html", "_headers", "404.html", "local/manifest.json", "weather/msm/catalog.json"]) {
    if (!files[required]) throw new Error("Missing static asset: " + required);
  }
  if (Object.keys(files).some(name => /(^|\/)(_worker\.js|functions|node_modules|\.env[^/]*)(\/|$)/.test(name))) {
    throw new Error("Runtime or private configuration in static artifact");
  }
  const manifest = JSON.parse(readFileSync(join(root, "local/manifest.json")));
  if (manifest.version !== release.version || manifest.pyodideVersion !== release.pyodideVersion) {
    throw new Error("Release / Local runtime version mismatch");
  }
  for (const name of [...manifest.wheels, manifest.data]) {
    if (files["local/" + name]?.sha256 !== manifest.sha256[name]) throw new Error("Local asset hash mismatch: " + name);
  }
  const catalog = JSON.parse(readFileSync(join(root, "weather/msm/catalog.json")));
  if (freshWeather && (!Number.isFinite(Date.parse(catalog.expires_at)) || Date.parse(catalog.expires_at) <= Date.now())) {
    throw new Error("MSM catalog expired; regenerate the feed before deployment");
  }
  if (!catalog.assets?.length) throw new Error("Empty MSM feed");
  for (const asset of catalog.assets) {
    const file = files["weather/msm/" + asset.file];
    if (file?.bytes !== asset.bytes || file?.sha256 !== asset.sha256) throw new Error("MSM asset mismatch: " + asset.file);
  }
  const entries = Object.entries(files);
  return { version: release.version, dirty: release.dirty, allowDirty, fileCount: entries.length,
    totalBytes: entries.reduce((sum, [, file]) => sum + file.bytes, 0),
    largest: entries.sort((a, b) => b[1].bytes - a[1].bytes).slice(0, 5),
    limits, weatherExpiresAt: catalog.expires_at, freshWeather };
}

export function sourceDirty(root) {
  return !!execFileSync("git", ["status", "--porcelain", "--untracked-files=normal", "--", ".", ":!.codex"],
    { cwd: root, encoding: "utf8" }).trim();
}
