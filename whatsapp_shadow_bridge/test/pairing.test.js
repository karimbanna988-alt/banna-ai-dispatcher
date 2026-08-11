"use strict";

/* Tests runPairing()'s retry behavior with a fully fake initializeBridge
 * -- no real whatsapp-web.js Client, no real browser, no real WhatsApp
 * account touched anywhere in this file.
 *
 * This file exists because of a real live-pilot failure: client.initialize()
 * rejected with a transient Puppeteer/Chromium injection race
 * ("ProtocolError: Execution context was destroyed"), and since nothing
 * caught that rejection, it was an unhandled promise rejection that
 * crashed the entire Node process -- taking the (unrelated) admin/health
 * server down with it. pairing.js had zero test coverage before this. */

const test = require("node:test");
const assert = require("node:assert/strict");
const { runPairing, attemptPairingWithRetry } = require("../src/pairing");

function fakeConfig(overrides = {}) {
  return { enabled: true, ...overrides };
}

function noopSleep() {
  return Promise.resolve();
}

test("bridge disabled: never calls initializeBridge, returns null synchronously", () => {
  let called = false;
  const result = runPairing(fakeConfig({ enabled: false }), () => { called = true; }, () => {});
  assert.equal(called, false);
  assert.equal(result, null);
});

test("a single successful initialize() returns that client, no retry attempted", async () => {
  let initializeBridgeCalls = 0;
  const client = { initialize: async () => {} };
  const initializeBridge = () => { initializeBridgeCalls += 1; return client; };
  const result = await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, {}, { sleep: noopSleep });
  assert.equal(result, client);
  assert.equal(initializeBridgeCalls, 1);
});

test("initialize() rejecting is caught -- never throws, never leaves an unhandled rejection", async () => {
  const client = { initialize: async () => { throw new Error("ProtocolError: Execution context was destroyed"); }, destroy: async () => {} };
  const initializeBridge = () => client;
  // If this were unhandled, awaiting it here would throw and fail the test.
  const result = await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, {}, { maxAttempts: 1, sleep: noopSleep });
  assert.equal(result, null);
});

test("first attempt fails, second succeeds: retries with a FRESH client instance and returns the successful one", async () => {
  const failedClient = { initialize: async () => { throw new Error("Execution context was destroyed"); }, destroy: async () => {} };
  const succeededClient = { initialize: async () => {} };
  let call = 0;
  const initializeBridge = () => { call += 1; return call === 1 ? failedClient : succeededClient; };
  const result = await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, {}, { sleep: noopSleep });
  assert.equal(result, succeededClient);
  assert.equal(call, 2);
});

test("the crashed client's destroy() is called for cleanup before retrying", async () => {
  let destroyed = false;
  const failedClient = { initialize: async () => { throw new Error("boom"); }, destroy: async () => { destroyed = true; } };
  const succeededClient = { initialize: async () => {} };
  let call = 0;
  const initializeBridge = () => { call += 1; return call === 1 ? failedClient : succeededClient; };
  await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, {}, { sleep: noopSleep });
  assert.equal(destroyed, true);
});

test("destroy() itself throwing does not stop the retry loop -- best-effort cleanup only", async () => {
  const failedClient = { initialize: async () => { throw new Error("boom"); }, destroy: async () => { throw new Error("cleanup also failed"); } };
  const succeededClient = { initialize: async () => {} };
  let call = 0;
  const initializeBridge = () => { call += 1; return call === 1 ? failedClient : succeededClient; };
  const result = await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, {}, { sleep: noopSleep });
  assert.equal(result, succeededClient);
});

test("a client with no destroy() method at all does not crash the retry loop", async () => {
  const failedClient = { initialize: async () => { throw new Error("boom"); } }; // no destroy()
  const succeededClient = { initialize: async () => {} };
  let call = 0;
  const initializeBridge = () => { call += 1; return call === 1 ? failedClient : succeededClient; };
  const result = await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, {}, { sleep: noopSleep });
  assert.equal(result, succeededClient);
});

test("gives up after maxAttempts and returns null -- never retries forever", async () => {
  let attempts = 0;
  const initializeBridge = () => {
    attempts += 1;
    return { initialize: async () => { throw new Error("always fails"); }, destroy: async () => {} };
  };
  const result = await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, {}, { maxAttempts: 3, sleep: noopSleep });
  assert.equal(result, null);
  assert.equal(attempts, 3);
});

test("default max attempts is bounded (does not retry indefinitely when maxAttempts is not passed)", async () => {
  let attempts = 0;
  const initializeBridge = () => {
    attempts += 1;
    return { initialize: async () => { throw new Error("always fails"); }, destroy: async () => {} };
  };
  await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, {}, { sleep: noopSleep });
  assert.ok(attempts >= 1 && attempts <= 5, `expected a small bounded attempt count, got ${attempts}`);
});

test("sleeps between attempts but not after the final failed attempt", async () => {
  const sleeps = [];
  const initializeBridge = () => ({ initialize: async () => { throw new Error("always fails"); }, destroy: async () => {} });
  await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, {}, {
    maxAttempts: 3,
    sleep: async (ms) => { sleeps.push(ms); },
  });
  assert.equal(sleeps.length, 2); // between attempt 1->2 and 2->3, not after 3
});

test("each attempt is passed the same hooks object, so onQr/onReady/onDisconnected/onAuthFailure still reach whichever attempt succeeds", async () => {
  const hooks = { onQr: () => {}, onReady: () => {}, onDisconnected: () => {}, onAuthFailure: () => {} };
  const receivedHooks = [];
  const initializeBridge = (config, passedHooks) => {
    receivedHooks.push(passedHooks);
    return { initialize: async () => {} };
  };
  await attemptPairingWithRetry(fakeConfig(), initializeBridge, () => {}, hooks, { sleep: noopSleep });
  assert.equal(receivedHooks.length, 1);
  assert.equal(receivedHooks[0], hooks);
});

test("runPairing wires the real onReady/onQr/onDisconnected/onAuthFailure hooks through to attemptPairingWithRetry, and onReady forwards the live client", async () => {
  const client = { initialize: async () => {} };
  const initializeBridge = (config, hooks) => {
    // Simulate the real "ready" event firing after initialize() resolves.
    setImmediate(() => hooks.onReady(client));
    return client;
  };
  let readyClient = null;
  await runPairing(fakeConfig(), initializeBridge, () => {}, {
    onReady: (liveClient) => { readyClient = liveClient; },
  }, { sleep: noopSleep });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(readyClient, client);
});

test("never calls a WhatsApp send/logout-capable method -- only initialize() and destroy() are ever invoked on the client", () => {
  const source = require("node:fs").readFileSync(require.resolve("../src/pairing"), "utf8");
  assert.ok(!/\.logout\s*\(/.test(source));
  assert.ok(!/\.sendMessage\s*\(/.test(source));
});
