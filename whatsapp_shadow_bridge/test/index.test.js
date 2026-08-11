"use strict";

/* Tests main()'s readiness/status wiring using a fully mocked
 * runPairing/initializeBridge/createHealthServer -- no real
 * whatsapp-web.js Client, no real network call, no real WhatsApp
 * account touched anywhere in this file. */

const test = require("node:test");
const assert = require("node:assert/strict");
const { main } = require("../src/index");

function fakeConfig(overrides = {}) {
  return { enabled: true, localAdminPort: 0, allowlistFile: "fake", sessionDir: "fake", ...overrides };
}

// A minimal fake runPairing that captures the hooks main() passes in,
// so tests can fire onReady/onDisconnected/onAuthFailure manually --
// exactly like the real event-driven client would, but synchronously
// and without any real WhatsApp session.
function fakeRunPairingFactory() {
  let capturedHooks = null;
  const fakeClient = { getChats: async () => ([]) };
  const runPairing = (config, initializeBridge, log, hooks) => {
    capturedHooks = hooks;
    return fakeClient;
  };
  return { runPairing, getHooks: () => capturedHooks, fakeClient };
}

function fakeCreateHealthServer() {
  // main() only needs *a* server object back; the real HTTP behavior is
  // covered by test/healthServer.test.js.
  return { fake: true };
}

test("state.paired is false before the ready callback fires", () => {
  const { runPairing } = fakeRunPairingFactory();
  const { state } = main(["--pair"], {
    runPairing,
    initializeBridge: () => {},
    createHealthServer: fakeCreateHealthServer,
    startRetryDrainLoop: () => {},
    config: fakeConfig(),
  });
  assert.equal(state.paired, false);
  assert.equal(state.getGroups, null);
});

test("state.paired becomes true only after the ready callback fires", () => {
  const { runPairing, getHooks, fakeClient } = fakeRunPairingFactory();
  const { state } = main(["--pair"], {
    runPairing,
    initializeBridge: () => {},
    createHealthServer: fakeCreateHealthServer,
    startRetryDrainLoop: () => {},
    config: fakeConfig(),
  });
  assert.equal(state.paired, false);
  getHooks().onReady(fakeClient);
  assert.equal(state.paired, true);
  assert.equal(typeof state.getGroups, "function");
});

test("getGroups is not exposed until after ready", () => {
  const { runPairing, getHooks, fakeClient } = fakeRunPairingFactory();
  const { state } = main(["--pair"], {
    runPairing,
    initializeBridge: () => {},
    createHealthServer: fakeCreateHealthServer,
    startRetryDrainLoop: () => {},
    config: fakeConfig(),
  });
  assert.equal(state.getGroups, null, "getGroups must be null before ready");
  getHooks().onReady(fakeClient);
  assert.notEqual(state.getGroups, null, "getGroups must exist after ready");
});

test("disconnect resets paired to false and clears getGroups", () => {
  const { runPairing, getHooks, fakeClient } = fakeRunPairingFactory();
  const { state } = main(["--pair"], {
    runPairing,
    initializeBridge: () => {},
    createHealthServer: fakeCreateHealthServer,
    startRetryDrainLoop: () => {},
    config: fakeConfig(),
  });
  getHooks().onReady(fakeClient);
  assert.equal(state.paired, true);
  getHooks().onDisconnected("NAVIGATION");
  assert.equal(state.paired, false);
  assert.equal(state.getGroups, null);
});

test("auth failure resets paired to false and clears getGroups", () => {
  const { runPairing, getHooks, fakeClient } = fakeRunPairingFactory();
  const { state } = main(["--pair"], {
    runPairing,
    initializeBridge: () => {},
    createHealthServer: fakeCreateHealthServer,
    startRetryDrainLoop: () => {},
    config: fakeConfig(),
  });
  getHooks().onReady(fakeClient);
  assert.equal(state.paired, true);
  getHooks().onAuthFailure("bad session");
  assert.equal(state.paired, false);
  assert.equal(state.getGroups, null);
});

test("getGroups, once ready, returns only id and name for groups (no history/participants/etc)", async () => {
  const fakeClientWithChats = {
    getChats: async () => ([
      { isGroup: true, id: { _serialized: "111@g.us" }, name: "Kabaka Dispatch", participants: ["should not leak"], msgs: ["should not leak"] },
      { isGroup: false, id: { _serialized: "222@c.us" }, name: "Some Person" },
    ]),
  };
  const captured = {};
  const wrapped = (config, initializeBridge, log, hooks) => { captured.hooks = hooks; return fakeClientWithChats; };
  const { state } = main(["--pair"], {
    runPairing: wrapped,
    initializeBridge: () => {},
    createHealthServer: fakeCreateHealthServer,
    startRetryDrainLoop: () => {},
    config: fakeConfig(),
  });
  captured.hooks.onReady(fakeClientWithChats);
  const groups = await state.getGroups();
  assert.deepEqual(groups, [{ id: "111@g.us", name: "Kabaka Dispatch" }]);
  const serialized = JSON.stringify(groups);
  assert.ok(!serialized.includes("should not leak"));
});

// Regression coverage for the real live-pilot bug: runPairing can retry
// pairing internally with a FRESH Client instance per attempt (see
// pairing.js) after a browser-injection crash. Before this fix,
// getGroups() closed over main()'s own `client` variable (bound once,
// synchronously, to runPairing()'s return value) instead of whichever
// client instance actually reached "ready" -- so a client that
// succeeded on a retry would report ready, but getGroups() would still
// call into a stale/wrong client reference.
test("getGroups uses the client instance that actually became ready, not main()'s own outer client reference", async () => {
  const staleClient = { getChats: async () => { throw new Error("must never be called -- this is the stale pre-retry client"); } };
  const liveClient = { getChats: async () => ([{ isGroup: true, id: { _serialized: "999@g.us" }, name: "Real Group" }]) };
  const captured = {};
  // Simulates runPairing() returning its FIRST attempt's client
  // synchronously (as a fake test double would if unaware of retries),
  // while the real ready event later fires for a DIFFERENT (retried)
  // client instance -- exactly what happens for real when attempt 1
  // crashes and attempt 2 succeeds.
  const wrapped = (config, initializeBridge, log, hooks) => { captured.hooks = hooks; return staleClient; };
  const { state } = main(["--pair"], {
    runPairing: wrapped,
    initializeBridge: () => {},
    createHealthServer: fakeCreateHealthServer,
    startRetryDrainLoop: () => {},
    config: fakeConfig(),
  });
  captured.hooks.onReady(liveClient);
  const groups = await state.getGroups();
  assert.deepEqual(groups, [{ id: "999@g.us", name: "Real Group" }]);
});

test("bridge disabled: paired stays false, getGroups stays null, no pairing attempted", () => {
  let pairingCalled = false;
  const { state, client } = main([], {
    runPairing: () => { pairingCalled = true; },
    initializeBridge: () => {},
    createHealthServer: fakeCreateHealthServer,
    startRetryDrainLoop: () => {},
    config: fakeConfig(),
  });
  assert.equal(pairingCalled, false);
  assert.equal(client, null);
  assert.equal(state.paired, false);
  assert.equal(state.getGroups, null);
});
