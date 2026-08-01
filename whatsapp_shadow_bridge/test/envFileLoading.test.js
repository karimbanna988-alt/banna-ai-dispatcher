"use strict";

/* Regression coverage for the live-pilot bridge/adapter secret mismatch:
 * the Node bridge had no mechanism at all to load whatsapp_shadow_bridge/.env
 * into process.env -- no dotenv package, no manual parsing anywhere in
 * src/, and the launch scripts only ever set WHATSAPP_SHADOW_ENABLED.
 * WHATSAPP_SHADOW_ADAPTER_SECRET therefore silently defaulted to "" in
 * the real running process regardless of what the .env file on disk said,
 * which could never match the Python side's real secret -- every
 * /observation POST 401'd. Fixed by adding Node's built-in
 * --env-file-if-exists=.env flag to both launch scripts (no new
 * dependency; tolerates a missing .env so config.js's own "safe with no
 * .env at all" default-disabled design goal still holds). These tests
 * exercise the real mechanism end-to-end -- not just that a flag string
 * is present somewhere -- by actually spawning node against a real
 * temporary .env file. */

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const BRIDGE_DIR = path.join(__dirname, "..");

function withTempEnvFile(contents, fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "wsb-envfile-test-"));
  const envPath = path.join(dir, ".env");
  fs.writeFileSync(envPath, contents, "utf8");
  try {
    return fn(envPath);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test("node --env-file-if-exists loads WHATSAPP_SHADOW_ADAPTER_SECRET from a real .env file into process.env", () => {
  const secretValue = "abc123secretvalue";
  withTempEnvFile(`WHATSAPP_SHADOW_ADAPTER_SECRET=${secretValue}\n`, (envPath) => {
    const out = execFileSync(
      process.execPath,
      [`--env-file-if-exists=${envPath}`, "-e", "process.stdout.write(String((process.env.WHATSAPP_SHADOW_ADAPTER_SECRET || '').length))"],
      { encoding: "utf8" },
    );
    assert.equal(out.trim(), String(secretValue.length));
  });
});

test("node --env-file-if-exists does not crash when the .env file is missing (safe-by-default is preserved)", () => {
  const missingPath = path.join(os.tmpdir(), "wsb-envfile-test-does-not-exist-" + Date.now(), ".env");
  const out = execFileSync(
    process.execPath,
    [`--env-file-if-exists=${missingPath}`, "-e", "process.stdout.write('ran')"],
    { encoding: "utf8" },
  );
  assert.equal(out.trim(), "ran");
});

test("loadConfig() picks up the adapter secret once the env-file flag has populated process.env (full chain, not just config.js in isolation)", () => {
  withTempEnvFile("WHATSAPP_SHADOW_ADAPTER_SECRET=full-chain-secret-value\n", (envPath) => {
    const out = execFileSync(
      process.execPath,
      [
        `--env-file-if-exists=${envPath}`,
        "-e",
        "const { loadConfig } = require('./src/config'); process.stdout.write(loadConfig().adapterSecret);",
      ],
      { encoding: "utf8", cwd: BRIDGE_DIR },
    );
    assert.equal(out.trim(), "full-chain-secret-value");
  });
});

test("both launch scripts pass an env-file flag to node before starting the bridge (regression guard)", () => {
  const scriptsDir = path.join(BRIDGE_DIR, "scripts");
  for (const script of ["start_pairing.ps1", "start_normal.ps1"]) {
    const source = fs.readFileSync(path.join(scriptsDir, script), "utf8");
    assert.match(
      source,
      /node\s+--env-file(-if-exists)?=\.env\s+src[\\/]index\.js/,
      `${script} must load whatsapp_shadow_bridge/.env before starting the bridge`,
    );
  }
});
