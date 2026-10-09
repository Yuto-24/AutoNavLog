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
