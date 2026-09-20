import { execFileSync } from "node:child_process";
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, truncateSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { inventory, checkArtifact, limits, sha256, sourceDirty } from "./static-artifact.mjs";
function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "static-artifact-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const name of ["local", "weather/msm"]) mkdirSync(join(root, name), { recursive: true });
  const put = (name, value) => writeFileSync(join(root, name), typeof value === "string" ? value : JSON.stringify(value));
  for (const name of ["index.html", "_headers", "404.html", "local/core.whl", "local/data.zip", "weather/msm/run.npz"]) put(name, name);
  put("local/manifest.json", { version: "1.0.0", pyodideVersion: "0.27.7", wheels: ["core.whl"], data: "data.zip",
    sha256: { "core.whl": sha256("local/core.whl"), "data.zip": sha256("local/data.zip") } });
  put("weather/msm/catalog.json", { expires_at: new Date(Date.now() + 60000).toISOString(),
    assets: [{ file: "run.npz", bytes: 19, sha256: sha256("weather/msm/run.npz") }] });
  // Compute size instead of depending on the test string's length.
  const catalog = JSON.parse(readFileSync(join(root, "weather/msm/catalog.json")));
  catalog.assets[0].bytes = Buffer.byteLength("weather/msm/run.npz");
  put("weather/msm/catalog.json", catalog);
  const seal = () => {
    const files = inventory(root); delete files["release.json"];
    put("release.json", { version: "1.0.0", pyodideVersion: "0.27.7", dirty: false, files });
  };
  seal(); return { root, put, seal };
}
test("portable artifact inventory includes all files, validates hashes and weather", t => {
  const { root } = fixture(t); const report = checkArtifact(root);
  assert.equal(report.version, "1.0.0"); assert.ok(report.totalBytes > 0); assert.equal(report.fileCount, 9);
});
test("tampering or an unrecorded asset rejects deployment", t => {
  const { root, put } = fixture(t); put("index.html", "changed");
  assert.throws(() => checkArtifact(root), /inventory mismatch/);
});
test("oversize files fail before upload, including sparse files", t => {
  const { root, put } = fixture(t); put("oversize", "");
  truncateSync(join(root, "oversize"), limits.assetBytes + 1);
  assert.throws(() => checkArtifact(root), /exceeds 25 MiB/);
});
test("expired feed blocks deployment; historical inspection is explicitly labelled", t => {
  const { root, put, seal } = fixture(t);
  const catalog = JSON.parse(readFileSync(join(root, "weather/msm/catalog.json")));
  catalog.expires_at = "2000-01-01T00:00:00Z"; put("weather/msm/catalog.json", catalog); seal();
  assert.throws(() => checkArtifact(root), /expired/);
  assert.equal(checkArtifact(root, { freshWeather: false }).freshWeather, false);
});
test("a sealed but mismatched Local package is rejected", t => {
  const { root, put, seal } = fixture(t); put("local/core.whl", "other build"); seal();
  assert.throws(() => checkArtifact(root), /Local asset hash mismatch/);
});
test("provider runtime is rejected even with a fresh inventory", t => {
  const { root, put, seal } = fixture(t); put("_worker.js", "export default {}"); seal();
  assert.throws(() => checkArtifact(root), /Runtime or private/);
});

test("untracked source is dirty while unrelated Codex state is excluded", t => {
  const root = mkdtempSync(join(tmpdir(), "static-source-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  execFileSync("git", ["init", "--quiet", root]);
  mkdirSync(join(root, ".codex")); writeFileSync(join(root, ".codex", "state"), "unrelated");
  assert.equal(sourceDirty(root), false);
  mkdirSync(join(root, "src")); writeFileSync(join(root, "src", "untracked.py"), "source");
  assert.equal(sourceDirty(root), true);
});

test("dirty artifacts fail production checks unless explicitly allowed for development", t => {
  const { root, put } = fixture(t);
  const release = JSON.parse(readFileSync(join(root, "release.json")));
  put("release.json", { ...release, dirty: true });
  assert.throws(() => checkArtifact(root), /Dirty source artifact/);
  const report = checkArtifact(root, { allowDirty: true });
  assert.equal(report.dirty, true);
  assert.equal(report.allowDirty, true);
  const cli = join(import.meta.dirname, "check-static.mjs");
  assert.throws(() => execFileSync(process.execPath, [cli, root], { stdio: "pipe" }));
  const accepted = JSON.parse(execFileSync(process.execPath, [cli, root, "--allow-dirty"], { encoding: "utf8" }));
  assert.equal(accepted.dirty, true);
});
test("a missing dirty flag is not proof of a clean source even with a development opt-out", t => {
  const { root, put } = fixture(t);
  const release = JSON.parse(readFileSync(join(root, "release.json")));
  delete release.dirty; put("release.json", release);
  assert.throws(() => checkArtifact(root), /Missing or invalid/);
  assert.throws(() => checkArtifact(root, { allowDirty: true }), /Missing or invalid/);
});
