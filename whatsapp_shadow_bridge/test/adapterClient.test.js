"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { postObservation, checkHealth, validateObservation } = require("../src/adapterClient");

const config = {
  adapterUrl: "http://127.0.0.1:8513",
  adapterSecret: "test-secret",
  maxMessageBytes: 8000,
};

function fakeFetch(handler) {
  return async (url, opts) => handler(url, opts);
}

test("validateObservation rejects a missing required field", () => {
  assert.throws(() => validateObservation({ groupId: "g", groupName: "G", text: "hi", timestamp: 1 }, 8000), /messageId/);
});

test("validateObservation rejects an oversized message", () => {
  const big = { messageId: "m1", groupId: "g", groupName: "G", text: "x".repeat(9000), timestamp: 1 };
  assert.throws(() => validateObservation(big, 8000), /exceeds max size/);
});

test("postObservation sends the shared secret header and never a send-capable payload", async () => {
  let captured = null;
  const fetchImpl = fakeFetch(async (url, opts) => {
    captured = { url, opts };
    return { ok: true, json: async () => ({ accepted: true }) };
  });
  await postObservation(config, {
    messageId: "m1", groupId: "g@g.us", groupName: "G", sender: "s", text: "hi", timestamp: 1,
  }, fetchImpl);

  assert.equal(captured.url, "http://127.0.0.1:8513/observation");
  assert.equal(captured.opts.headers["X-Bridge-Secret"], "test-secret");
  const body = JSON.parse(captured.opts.body);
  assert.equal(body.event_type, "incoming_message");
  assert.equal(body.message_id, "m1");
  // No field here could plausibly be a "send" instruction -- observation
  // payloads are strictly capture-only.
  assert.ok(!("send" in body));
  assert.ok(!("reply" in body));
});

test("postObservation throws on a non-OK response instead of silently succeeding", async () => {
  const fetchImpl = fakeFetch(async () => ({ ok: false, status: 403, text: async () => "bad secret" }));
  await assert.rejects(
    () => postObservation(config, { messageId: "m1", groupId: "g", groupName: "G", text: "hi", timestamp: 1 }, fetchImpl),
    /403/,
  );
});

test("checkHealth returns false (not a throw) when the adapter is unreachable", async () => {
  const fetchImpl = async () => { throw new Error("ECONNREFUSED"); };
  const healthy = await checkHealth(config, fetchImpl);
  assert.equal(healthy, false);
});

test("checkHealth returns true when the adapter responds ok", async () => {
  const fetchImpl = async () => ({ ok: true });
  const healthy = await checkHealth(config, fetchImpl);
  assert.equal(healthy, true);
});
