import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

// Exercise the locked CLI, with no inherited credentials and no possible network I/O.
test("Pages upload discovers only the isolated cwd config without --config", () => {
  const root = mkdtempSync(join(tmpdir(), "autonavlog-pages-cli-"));
  try {
    const cwd = join(root, "upload");
    mkdirSync(cwd);
    mkdirSync(join(cwd, "dist-static"));
    const config = {
      name: "navmate", pages_build_output_dir: "./dist-static",
      compatibility_date: "2026-09-19",
    };
    writeFileSync(join(cwd, "wrangler.json"), JSON.stringify(config));
    // A parent/repository config must not replace the sealed upload config.
    writeFileSync(join(root, "wrangler.toml"), "invalid parent config [[[\n");
    const guard = join(root, "no-network.cjs");
    writeFileSync(guard, `
      const stop = () => { process.stderr.write('NETWORK_FORBIDDEN'); process.exit(98); };
      require('node:net').Socket.prototype.connect = stop;
      require('node:tls').connect = stop;
      for (const name of ['node:http', 'node:https']) {
        require(name).request = stop; require(name).get = stop;
      }
      globalThis.fetch = stop;
      require('node:module').syncBuiltinESMExports();
    `);
    const workflow = readFileSync(new URL("../../.github/workflows/static-production.yml",
      import.meta.url), "utf8");
    const command = workflow.split("\n").find(line => line.includes('" pages deploy '));
    assert.ok(command, "production upload command must exist");
    const args = command.trim().split('" pages ')[1]
      .replace('"$EXPECTED_SOURCE"', "a".repeat(40)).split(/\s+/);
    args.unshift("pages");
    assert.deepEqual(args, ["pages", "deploy", "dist-static", "--project-name", "navmate",
      "--branch", "main", "--commit-hash", "a".repeat(40)]);
    assert.match(workflow, /working-directory: \$\{\{ runner.temp \}\}\/static-upload/);
    const cli = fileURLToPath(new URL("node_modules/wrangler/bin/wrangler.js", import.meta.url));
    const run = extra => {
      const result = spawnSync(process.execPath, [cli, ...args, ...extra], {
        cwd, encoding: "utf8", timeout: 20000,
        env: { PATH: process.env.PATH, HOME: root, XDG_CONFIG_HOME: root,
          CI: "true", WRANGLER_SEND_METRICS: "false", WRANGLER_HIDE_BANNER: "true", NO_COLOR: "1",
          WRANGLER_LOG_PATH: join(root, "logs"), NODE_OPTIONS: `--require=${guard}` },
      });
      assert.ifError(result.error);
      const output = result.stdout + result.stderr;
      assert.doesNotMatch(output, /NETWORK_FORBIDDEN/);
      assert.equal(result.status, 1, output);
      return output;
    };
    const accepted = run([]);
    assert.match(accepted, /CLOUDFLARE_API_TOKEN/); // Reaches auth, never publishes.
    assert.doesNotMatch(accepted, /does not support custom paths|Ignoring configuration|ValueExpected/);
    assert.match(run(["--config", join(cwd, "wrangler.json")]),
      /Pages does not support custom paths/);
    writeFileSync(join(cwd, "wrangler.json"), '{"name":');
    const invalid = run([]);
    assert.match(invalid, /ValueExpected/);
    assert.match(invalid, /upload\/wrangler.json/); // Cwd configuration is actually read.
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// Execute the installed, lockfile-pinned deploy implementation with injected I/O.
// This covers request construction; the test above covers CLI/config discovery.
test("locked static deploy leaves Pages env untouched and sends no Worker bundle", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const crypto = await import("node:crypto");
  const { runInNewContext } = await import("node:vm");
  const { File } = await import("node:buffer");
  const source = readFileSync(new URL("node_modules/wrangler/wrangler-dist/cli.js",
    import.meta.url), "utf8");
  const start = source.indexOf("async function deploy2({");
  const end = source.indexOf("\nvar import_undici26, MAX_COMMIT_MESSAGE_BYTES;", start);
  assert.ok(start >= 0 && end > start, "review harness when the locked CLI changes");
  const root = mkdtempSync(join(tmpdir(), "autonavlog-pages-payload-"));
  try {
    const directory = join(root, "dist-static");
    mkdirSync(directory);
    const content = "<html>approved static build</html>";
    writeFileSync(join(directory, "index.html"), content);
    const configPath = join(root, "wrangler.json");
    const config = { name: "navmate", pages_build_output_dir: "./dist-static",
      compatibility_date: "2026-09-19" };
    writeFileSync(configPath, JSON.stringify(config));
    const requests = [];
    const forbidden = () => { throw new Error("unexpected runtime operation"); };
    for (const env_vars of [{}, {
      VITE_TAF_PROXY_URL: { type: "plain_text", value: "REMOTE_ENV_CANARY" },
      UNKNOWN_SECRET: { type: "secret_text", value: "PRIVATE_ENV_CANARY" },
    }]) {
      const project = { production_branch: "main", deployment_configs: {
        production: { env_vars, compatibility_date: "2026-09-19" },
      } };
      const before = JSON.stringify(project);
      const calls = [];
      const deploy = runInNewContext(source.slice(start, end) + "\ndeploy2", {
        fs12: fs, fs$1: fs.promises, path25: path, path25__namespace: path,
        crypto3: crypto, process18: { cwd: () => root }, process: { cwd: () => root },
        import_undici26: { FormData }, File, COMPLIANCE_REGION_CONFIG_PUBLIC: {},
        readPagesConfig: () => ({ ...config, configPath }),
        validateNodeCompatMode: () => undefined, isNavigatorDefined: () => false,
        shouldCheckFetch: () => false, getPagesTmpDir: () => root,
        maxFileCountAllowedFromClaims: () => 20000,
        validate: async ({ directory: actual }) => {
          assert.equal(actual, directory);
          return { "index.html": readFileSync(join(actual, "index.html")) };
        },
        upload: async ({ fileMap }) => {
          assert.equal(fileMap["index.html"].toString(), content);
          return { "/index.html": crypto.createHash("sha256").update(fileMap["index.html"]).digest("hex") };
        },
        fetchResult2: async (_region, url, options) => {
          calls.push({ url, method: options?.method ?? "GET" });
          const base = "/accounts/test-account/pages/projects/navmate";
          if (url === base && !options) return project;
          if (url === base + "/upload-token" && !options) return { jwt: "fixture" };
          if (url === base + "/deployments" && options?.method === "POST") {
            requests.push([...options.body.entries()]);
            return { id: "fixture-deployment" };
          }
          throw new Error("unexpected API request");
        },
        buildFunctions: forbidden, buildRawWorker: forbidden,
        produceWorkerBundleForWorkerJSDirectory: forbidden,
        createUploadWorkerBundleContents: forbidden,
        MAX_DEPLOYMENT_ATTEMPTS: 1,
        logger2: { log: forbidden, warn: forbidden, debug: forbidden },
      });
      await deploy({ directory, accountId: "test-account", projectName: "navmate",
        branch: "main", commitHash: "a".repeat(40), args: {} });
      assert.equal(JSON.stringify(project), before);
      assert.deepEqual(calls.map(call => call.method), ["GET", "GET", "POST"]);
      assert.equal(readFileSync(join(directory, "index.html"), "utf8"), content);
    }
    assert.deepEqual(requests[0], requests[1]);
    assert.deepEqual(requests[1].map(([key]) => key).sort(),
      ["branch", "commit_hash", "manifest", "pages_build_output_dir", "wrangler_config_hash"]);
    assert.doesNotMatch(JSON.stringify(requests), /REMOTE_ENV_CANARY|PRIVATE_ENV_CANARY|env_vars|_worker/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
