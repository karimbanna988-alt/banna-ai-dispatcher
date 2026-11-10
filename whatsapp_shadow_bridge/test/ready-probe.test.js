/* /ready must answer "does the session WORK", not "did it once connect".
 *
 * The outage this exists for: group discovery failed for hours while
 * /health kept reporting {paired:true,status:"connected"}. state.paired
 * is set once on "ready" and cleared only on an explicit disconnect or
 * auth failure; when the underlying page dies none of those fire. The
 * port stayed open, the supervisor saw health, and nothing restarted.
 *
 * So the probe must FAIL in every shape of that outage, and it must
 * never leak the raw internal error over HTTP. */

"use strict";

const test = require("node:test");
const assert = require("node:assert");
const http = require("node:http");

const { createHealthServer } = require("../src/healthServer");

const CONFIG = { enabled: true, localAdminPort: 0, allowlistFile: "x", dedupeFile: "y" };

function withServer(state, run) {
  return new Promise((resolve, reject) => {
    const server = createHealthServer(CONFIG, state);
    server.listen(0, "127.0.0.1", async () => {
      const port = server.address().port;
      try {
        const result = await run(port);
        server.close(() => resolve(result));
      } catch (err) {
        server.close(() => reject(err));
      }
    });
  });
}

function get(port, path) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port, path }, (res) => {
      let body = "";
      res.on("data", (c) => { body += c; });
      res.on("end", () => resolve({ status: res.statusCode, body: JSON.parse(body) }));
    }).on("error", reject);
  });
}

test("a working session reports ready", async () => {
  const state = { paired: true, getGroups: async () => [{ id: "a@g.us", name: "A" }] };
  const r = await withServer(state, (p) => get(p, "/ready"));
  assert.strictEqual(r.body.ready, true);
  assert.strictEqual(r.body.groups, 1);
});

test("THE OUTAGE: paired but discovery throws reports NOT ready", async () => {
  const state = {
    paired: true,
    getGroups: async () => { throw new Error("Execution context was destroyed"); },
  };
  const r = await withServer(state, (p) => get(p, "/ready"));
  assert.strictEqual(r.body.ready, false);
  assert.strictEqual(r.body.paired, true, "it must still admit the session CLAIMS to be paired");
  assert.strictEqual(r.body.reason, "probe_failed");
});

test("the raw internal error is never sent over HTTP", async () => {
  const secretish = "Execution context destroyed at /Users/someone/private/path";
  const state = { paired: true, getGroups: async () => { throw new Error(secretish); } };
  const r = await withServer(state, (p) => get(p, "/ready"));
  assert.ok(!JSON.stringify(r.body).includes("private/path"));
  assert.ok(!JSON.stringify(r.body).includes("Execution context"));
});

test("an empty chat list is treated as broken, not as success", async () => {
  /* A live account always has chats. Zero is the store answering
   * without being populated -- the same failure wearing a 200. */
  const state = { paired: true, getGroups: async () => [] };
  const r = await withServer(state, (p) => get(p, "/ready"));
  assert.strictEqual(r.body.ready, false);
  assert.strictEqual(r.body.reason, "no_groups_returned");
});

test("a session that never paired reports not ready, without probing", async () => {
  let probed = false;
  const state = { paired: false, getGroups: async () => { probed = true; return []; } };
  const r = await withServer(state, (p) => get(p, "/ready"));
  assert.strictEqual(r.body.ready, false);
  assert.strictEqual(r.body.reason, "session_not_ready");
  assert.strictEqual(probed, false);
});

test("/ready never sends anything -- it only reads", () => {
  const fs = require("node:fs");
  const src = fs.readFileSync(require.resolve("../src/healthServer.js"), "utf8");
  const start = src.indexOf('req.url === "/ready"');
  const block = src.slice(start, start + 2500);
  for (const forbidden of [".sendMessage(", ".reply(", ".logout("]) {
    assert.ok(!block.includes(forbidden), `/ready must not contain ${forbidden}`);
  }
});

test("/health still answers even while the session is broken", async () => {
  /* /health is not replaced: it says whether the process is up, which
   * is a different and still useful question. */
  const state = { paired: true, getGroups: async () => { throw new Error("dead"); } };
  const r = await withServer(state, (p) => get(p, "/health"));
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.paired, true);
});
