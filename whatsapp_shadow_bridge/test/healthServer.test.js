"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const http = require("node:http");
const { createHealthServer } = require("../src/healthServer");

function tmpConfig() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "wsb-test-"));
  return {
    enabled: false,
    localAdminPort: 0, // OS-assigned free port for the test
    allowlistFile: path.join(dir, "allowlist.json"),
  };
}

function request(port, method, urlPath, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(
      { host: "127.0.0.1", port, method, path: urlPath, headers: data ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } : {} },
      (res) => {
        let raw = "";
        res.on("data", (c) => { raw += c; });
        res.on("end", () => {
          let json = null;
          try { json = JSON.parse(raw); } catch (e) { /* not json */ }
          resolve({ status: res.statusCode, json });
        });
      },
    );
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

test("health server binds to 127.0.0.1 only, never 0.0.0.0", async () => {
  const config = tmpConfig();
  const state = { paired: false };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const address = server.address();
  assert.equal(address.address, "127.0.0.1");
  server.close();
});

test("GET /health reports disabled state honestly when the bridge is off", async () => {
  const config = tmpConfig();
  const state = { paired: false };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const port = server.address().port;

  const res = await request(port, "GET", "/health");
  assert.equal(res.status, 200);
  assert.equal(res.json.enabled, false);
  assert.equal(res.json.paired, false);
  assert.equal(res.json.status, "disabled");
  server.close();
});

test("allowlist add/list/remove round-trip over HTTP", async () => {
  const config = tmpConfig();
  const state = { paired: false };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const port = server.address().port;

  const add = await request(port, "POST", "/allowlist", { id: "111@g.us", name: "Kabaka Dispatch" });
  assert.equal(add.status, 200);
  assert.equal(add.json.groups.length, 1);

  const list = await request(port, "GET", "/allowlist");
  assert.equal(list.json.groups[0].id, "111@g.us");

  const del = await request(port, "DELETE", "/allowlist/" + encodeURIComponent("111@g.us"));
  assert.equal(del.status, 200);
  assert.equal(del.json.groups.length, 0);
  server.close();
});

test("malformed JSON body on POST /allowlist is rejected, not crashed on", async () => {
  const config = tmpConfig();
  const state = { paired: false };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const port = server.address().port;

  const res = await new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, method: "POST", path: "/allowlist", headers: { "Content-Type": "application/json" } }, (r) => {
      let raw = "";
      r.on("data", (c) => { raw += c; });
      r.on("end", () => resolve({ status: r.statusCode, body: raw }));
    });
    req.on("error", reject);
    req.write("{not valid json");
    req.end();
  });
  assert.equal(res.status, 400);
  server.close();
});

test("POST /allowlist with a malformed (non-JID) group id is rejected, not crashed on", async () => {
  const config = tmpConfig();
  const state = { paired: false };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const port = server.address().port;

  const res = await request(port, "POST", "/allowlist", { id: "not-a-real-group-id", name: "Fake" });
  assert.equal(res.status, 400);

  const list = await request(port, "GET", "/allowlist");
  assert.equal(list.json.groups.length, 0);
  server.close();
});

test("GET /groups before pairing returns 503 bridge_not_ready, not a crash", async () => {
  const config = tmpConfig();
  const state = { paired: false, getGroups: null };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const port = server.address().port;

  const res = await request(port, "GET", "/groups");
  assert.equal(res.status, 503);
  assert.equal(res.json.error, "bridge_not_ready");
  server.close();
});

test("GET /groups returns 503 even if getGroups exists but paired is still false", async () => {
  // Defense in depth: paired=false must gate /groups even if something
  // else left a stale getGroups function around (e.g. after a
  // disconnect that didn't fully clear state).
  const config = tmpConfig();
  const state = { paired: false, getGroups: async () => ([{ id: "1@g.us", name: "Should not be reachable" }]) };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const port = server.address().port;

  const res = await request(port, "GET", "/groups");
  assert.equal(res.status, 503);
  server.close();
});

test("GET /groups succeeds once paired is true and getGroups is set", async () => {
  const config = tmpConfig();
  const state = { paired: true, getGroups: async () => ([{ id: "111@g.us", name: "Kabaka Dispatch" }]) };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const port = server.address().port;

  const res = await request(port, "GET", "/groups");
  assert.equal(res.status, 200);
  assert.deepEqual(res.json.groups, [{ id: "111@g.us", name: "Kabaka Dispatch" }]);
  server.close();
});

test("GET /groups sanitizes an internal getGroups failure instead of echoing raw internal error text", async () => {
  const config = tmpConfig();
  const state = { paired: true, getGroups: async () => { throw new Error("r"); } };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const port = server.address().port;

  const res = await request(port, "GET", "/groups");
  assert.equal(res.status, 500);
  assert.equal(res.json.error, "group_discovery_failed");
  assert.notEqual(res.json.error, "r");
  server.close();
});

// Whatever shape the rejection takes -- Error, bare string, minified
// object, circular object -- the HTTP response must always be the same
// fixed, sanitized code. Full detail only ever goes to this process's
// own console (see src/diagnostics.js), never over the wire.
const REJECTION_SHAPES = [
  ["a normal Error", () => { throw new Error("r"); }],
  ["a bare thrown string", () => { throw "r"; }], // eslint-disable-line no-throw-literal
  ["a minified/non-Error object", () => { throw { code: 1, m: "r" }; }], // eslint-disable-line no-throw-literal
  ["a circular object", () => { const c = { name: "loop" }; c.self = c; throw c; }],
];

for (const [label, thrower] of REJECTION_SHAPES) {
  test(`GET /groups returns only group_discovery_failed for ${label}`, async () => {
    const config = tmpConfig();
    const state = { paired: true, getGroups: async () => thrower() };
    const server = createHealthServer(config, state);
    await new Promise((resolve) => server.once("listening", resolve));
    const port = server.address().port;

    const res = await request(port, "GET", "/groups");
    assert.equal(res.status, 500);
    assert.deepEqual(Object.keys(res.json).sort(), ["error", "message"]);
    assert.equal(res.json.error, "group_discovery_failed");
    server.close();
  });
}

test("unknown route returns 404", async () => {
  const config = tmpConfig();
  const state = { paired: false };
  const server = createHealthServer(config, state);
  await new Promise((resolve) => server.once("listening", resolve));
  const port = server.address().port;

  const res = await request(port, "GET", "/nonexistent");
  assert.equal(res.status, 404);
  server.close();
});
