"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const deliveryQueue = require("../src/deliveryQueue");
const dedupeStore = require("../src/dedupeStore");
const { drainOnce } = require("../src/retryDrain");

function fakeConfig() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "wsb-drain-test-"));
  return {
    retryQueueFile: path.join(dir, "pending-delivery.json"),
    dedupeFile: path.join(dir, "seen.json"),
  };
}

test("draining an empty queue does nothing and reports zero attempted", async () => {
  const config = fakeConfig();
  const result = await drainOnce(config, { postObservation: async () => ({ ok: true }) });
  assert.deepEqual(result, { attempted: 0, delivered: 0 });
});

test("a successfully redelivered observation is marked seen and removed from the queue", async () => {
  const config = fakeConfig();
  deliveryQueue.enqueue(config.retryQueueFile, { messageId: "m1", text: "hi" });
  const posted = [];
  const result = await drainOnce(config, { postObservation: async (cfg, obs) => { posted.push(obs); return { ok: true }; } });
  assert.deepEqual(result, { attempted: 1, delivered: 1 });
  assert.equal(posted.length, 1);
  assert.equal(dedupeStore.isSeen(config.dedupeFile, "m1"), true);
  assert.deepEqual(deliveryQueue.load(config.retryQueueFile), []);
});

test("an observation that still fails to deliver stays queued and is NOT marked seen", async () => {
  const config = fakeConfig();
  deliveryQueue.enqueue(config.retryQueueFile, { messageId: "m1", text: "hi" });
  const result = await drainOnce(config, { postObservation: async () => { throw new Error("adapter still down"); } });
  assert.deepEqual(result, { attempted: 1, delivered: 0 });
  assert.equal(dedupeStore.isSeen(config.dedupeFile, "m1"), false);
  assert.deepEqual(deliveryQueue.load(config.retryQueueFile), [{ messageId: "m1", text: "hi" }]);
});

test("one failing and one succeeding observation are handled independently", async () => {
  const config = fakeConfig();
  deliveryQueue.enqueue(config.retryQueueFile, { messageId: "bad", text: "fails" });
  deliveryQueue.enqueue(config.retryQueueFile, { messageId: "good", text: "succeeds" });
  const result = await drainOnce(config, {
    postObservation: async (cfg, obs) => {
      if (obs.messageId === "bad") throw new Error("still down");
      return { ok: true };
    },
  });
  assert.deepEqual(result, { attempted: 2, delivered: 1 });
  assert.equal(dedupeStore.isSeen(config.dedupeFile, "good"), true);
  assert.equal(dedupeStore.isSeen(config.dedupeFile, "bad"), false);
  assert.deepEqual(deliveryQueue.load(config.retryQueueFile), [{ messageId: "bad", text: "fails" }]);
});
