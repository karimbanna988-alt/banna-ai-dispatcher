"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const deliveryQueue = require("../src/deliveryQueue");

function tmpFile() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), "wsb-retryq-test-")), "pending-delivery.json");
}

test("a fresh queue file is empty", () => {
  const file = tmpFile();
  assert.deepEqual(deliveryQueue.load(file), []);
});

test("enqueue durably records an observation, readable by a fresh load", () => {
  const file = tmpFile();
  deliveryQueue.enqueue(file, { messageId: "m1", text: "hi" });
  assert.deepEqual(deliveryQueue.load(file), [{ messageId: "m1", text: "hi" }]);
});

test("enqueue for the same message id replaces the prior entry, never duplicates", () => {
  const file = tmpFile();
  deliveryQueue.enqueue(file, { messageId: "m1", text: "first" });
  deliveryQueue.enqueue(file, { messageId: "m1", text: "second" });
  const queued = deliveryQueue.load(file);
  assert.equal(queued.length, 1);
  assert.equal(queued[0].text, "second");
});

test("dequeue removes exactly the matching message id", () => {
  const file = tmpFile();
  deliveryQueue.enqueue(file, { messageId: "m1", text: "a" });
  deliveryQueue.enqueue(file, { messageId: "m2", text: "b" });
  deliveryQueue.dequeue(file, "m1");
  assert.deepEqual(deliveryQueue.load(file), [{ messageId: "m2", text: "b" }]);
});

test("a queued observation survives being re-read from disk (simulated bridge restart)", () => {
  const file = tmpFile();
  deliveryQueue.enqueue(file, { messageId: "restart-test", text: "still here" });
  // A real restart is just a fresh process re-reading the same file --
  // load() has no in-memory state to lose, so this is exactly that.
  const reloaded = deliveryQueue.load(file);
  assert.equal(reloaded.length, 1);
  assert.equal(reloaded[0].messageId, "restart-test");
});

test("enqueue requires a messageId", () => {
  const file = tmpFile();
  assert.throws(() => deliveryQueue.enqueue(file, { text: "no id" }));
});
