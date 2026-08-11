"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { handleIncomingMessage, handleOutgoingMessage } = require("../src/messageHandler");

function fakeDeps(overrides = {}) {
  const posted = [];
  const seenMarked = [];
  const enqueued = [];
  const dequeued = [];
  return {
    config: { allowlistFile: "fake-allowlist", dedupeFile: "fake-dedupe", retryQueueFile: "fake-retry-queue" },
    isApproved: overrides.isApproved || (() => true),
    isSeen: overrides.isSeen || (() => false),
    markSeen: overrides.markSeen || ((file, id) => { seenMarked.push(id); return true; }),
    enqueueRetry: overrides.enqueueRetry || ((file, obs) => { enqueued.push(obs); }),
    dequeueRetry: overrides.dequeueRetry || ((file, id) => { dequeued.push(id); }),
    postObservation: overrides.postObservation || (async (config, obs) => { posted.push(obs); return { ok: true }; }),
    log: () => {},
    _posted: posted,
    _seenMarked: seenMarked,
    _enqueued: enqueued,
    _dequeued: dequeued,
  };
}

test("an outgoing message (fromMe) is rejected and never forwarded, even if it targets an allowlisted group", async () => {
  const deps = fakeDeps();
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", text: "sent by the paired account", fromMe: true },
    deps,
  );
  assert.equal(result.forwarded, false);
  assert.equal(result.reason, "outgoing_message");
  assert.equal(deps._posted.length, 0);
});

test("a direct (non-group) message is ignored and never forwarded", async () => {
  const deps = fakeDeps();
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: false, text: "hey", groupId: null },
    deps,
  );
  assert.equal(result.forwarded, false);
  assert.equal(result.reason, "not_group_message");
  assert.equal(deps._posted.length, 0);
});

test("a message from a non-allowlisted group is ignored and never forwarded", async () => {
  const deps = fakeDeps({ isApproved: () => false });
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "unrelated@g.us", text: "hi", timestamp: 1 },
    deps,
  );
  assert.equal(result.forwarded, false);
  assert.equal(result.reason, "group_not_allowlisted");
  assert.equal(deps._posted.length, 0);
});

test("a duplicate message id (already seen) is ignored and never forwarded twice", async () => {
  const deps = fakeDeps({ isSeen: () => true });
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", text: "hi", timestamp: 1 },
    deps,
  );
  assert.equal(result.forwarded, false);
  assert.equal(result.reason, "duplicate");
  assert.equal(deps._posted.length, 0);
});

test("an allowlisted, new, group message is forwarded with its extracted ticket guess", async () => {
  const deps = fakeDeps();
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "kabaka@g.us", groupName: "Kabaka Dispatch", sender: "1555", text: "PDL: IYRT1 done", timestamp: 123 },
    deps,
  );
  assert.equal(result.forwarded, true);
  assert.equal(result.extractedTicket, "IYRT1");
  assert.equal(deps._posted.length, 1);
  assert.equal(deps._posted[0].messageId, "m1");
  assert.equal(deps._posted[0].text, "PDL: IYRT1 done");
});

test("forwarding preserves the exact original message text, unmodified", async () => {
  const deps = fakeDeps();
  const rawText = "  Job done -- customer paid $95 CASH, no PDL mentioned  ";
  await handleIncomingMessage(
    { id: "m2", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: rawText, timestamp: 1 },
    deps,
  );
  assert.equal(deps._posted[0].text, rawText);
});

test("no ticket found -> extractedTicket is null, message is still forwarded (never dropped for lack of a ticket)", async () => {
  const deps = fakeDeps();
  const result = await handleIncomingMessage(
    { id: "m3", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "running late, 10 min", timestamp: 1 },
    deps,
  );
  assert.equal(result.forwarded, true);
  assert.equal(result.extractedTicket, null);
});

// ============================== LIVE RECEIVE FIX: ORDERING GUARANTEES ====
// Regression coverage for the real live-pilot failure: a message was
// received and deduped by the bridge, but the adapter rejected it (see
// tests/test_whatsapp_bridge_adapter.py for the matching Python-side
// timestamp-type bug) and the failure was silently swallowed -- the
// message was already marked "seen" before delivery was even attempted,
// so it was unrecoverable. These tests pin the corrected ordering.

test("adapter unavailable (throws): the message is enqueued for retry but NOT marked seen", async () => {
  const deps = fakeDeps({ postObservation: async () => { throw new Error("ECONNREFUSED"); } });
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "PDL: A100 done", timestamp: 1 },
    deps,
  );
  assert.equal(result.forwarded, false);
  assert.equal(result.reason, "adapter_delivery_failed");
  assert.deepEqual(deps._seenMarked, []); // never marked seen
  assert.equal(deps._enqueued.length, 1); // durably captured for retry
  assert.deepEqual(deps._dequeued, []); // never dequeued -- it was never delivered
});

test("adapter returns non-2xx (postObservation rejects): message remains retryable, not marked seen", async () => {
  const deps = fakeDeps({ postObservation: async () => { throw new Error("adapter rejected observation: 400 bad timestamp"); } });
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "hi", timestamp: 1 },
    deps,
  );
  assert.equal(result.forwarded, false);
  assert.deepEqual(deps._seenMarked, []);
  assert.equal(deps._enqueued.length, 1);
});

test("successful delivery: message is enqueued first, delivered, then marked seen and dequeued -- in that order", async () => {
  const callOrder = [];
  const deps = fakeDeps({
    enqueueRetry: () => callOrder.push("enqueue"),
    postObservation: async () => { callOrder.push("post"); return { ok: true }; },
    markSeen: () => { callOrder.push("markSeen"); return true; },
    dequeueRetry: () => callOrder.push("dequeue"),
  });
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "hi", timestamp: 1 },
    deps,
  );
  assert.equal(result.forwarded, true);
  assert.deepEqual(callOrder, ["enqueue", "post", "markSeen", "dequeue"]);
});

test("a message that quotes an earlier one forwards quotedMessageId to the adapter", async () => {
  const deps = fakeDeps();
  await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "k", timestamp: 1, quotedMessageId: "original-post-id" },
    deps,
  );
  assert.equal(deps._posted[0].quotedMessageId, "original-post-id");
});

test("a message with no quoted message forwards quotedMessageId as null", async () => {
  const deps = fakeDeps();
  await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "hi", timestamp: 1 },
    deps,
  );
  assert.equal(deps._posted[0].quotedMessageId, null);
});

test("never changes job status or attaches to anything -- this module has no such function to call", async () => {
  // Structural: handleIncomingMessage's only side effect is postObservation.
  // There is no job-status or job-attachment parameter anywhere in its
  // signature or the deps it accepts.
  const source = require("node:fs").readFileSync(require.resolve("../src/messageHandler"), "utf8");
  assert.ok(!/job\.status/.test(source));
  assert.ok(!/attach/i.test(source) || /never attach/i.test(source));
});

// ===================================== MEDIA / EMPTY-BODY OBSERVATIONS ====
// A media message with no caption arrives from whatsapp-web.js with an
// empty text body. Before this fix, such a message could never satisfy
// adapterClient's "text is required" validation, so postObservation
// threw on every single retry forever and the message piled up
// unrecoverably in the retry queue. These pin the fix: a synthetic,
// honest, metadata-only label is used instead of empty text, so the
// observation is captured and delivered like any other.

test("a media message with no caption is forwarded with an honest metadata-only label, not dropped or endlessly retried", async () => {
  const deps = fakeDeps();
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "", timestamp: 1, hasMedia: true, mediaType: "image" },
    deps,
  );
  assert.equal(result.forwarded, true);
  assert.equal(deps._posted[0].text, "[media message, no caption -- type: image]");
  assert.deepEqual(deps._dequeued, ["m1"]); // successfully drained from the retry queue, not stuck
});

test("a media message with a caption keeps the real caption text -- the fallback never overrides real content", async () => {
  const deps = fakeDeps();
  await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "PDL: A100 here's the door", timestamp: 1, hasMedia: true, mediaType: "image" },
    deps,
  );
  assert.equal(deps._posted[0].text, "PDL: A100 here's the door");
});

test("a non-text, non-media system message (e.g. a voice call notice) still gets a metadata label, never a thrown validation error", async () => {
  const deps = fakeDeps();
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "", timestamp: 1, hasMedia: false, mediaType: "call_log" },
    deps,
  );
  assert.equal(result.forwarded, true);
  assert.equal(deps._posted[0].text, "[non-text message -- type: call_log]");
});

test("an empty message with no media metadata at all still gets a non-empty, honest fallback label", async () => {
  const deps = fakeDeps();
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "", timestamp: 1 },
    deps,
  );
  assert.equal(result.forwarded, true);
  assert.equal(deps._posted[0].text, "[empty message, no text content]");
});

test("the media fallback never invents ticket, price, or customer content -- extractedTicket stays null", async () => {
  const deps = fakeDeps();
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", sender: "s", text: "", timestamp: 1, hasMedia: true, mediaType: "ptt" },
    deps,
  );
  assert.equal(result.extractedTicket, null);
});

test("an outgoing media message (Safe sending a job photo with no caption) is still audited with a metadata-only label", async () => {
  const deps = fakeOutgoingDeps();
  const result = await handleOutgoingMessage(
    { id: "out1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", text: "", timestamp: 1, fromMe: true, hasMedia: true, mediaType: "image" },
    deps,
  );
  assert.equal(result.audited, true);
  assert.equal(deps._posted[0].text, "[media message, no caption -- type: image]");
});

// ==================================================== handleOutgoingMessage
function fakeOutgoingDeps(overrides = {}) {
  const posted = [];
  const seenMarked = [];
  return {
    config: { allowlistFile: "fake-allowlist", dedupeFile: "fake-dedupe" },
    isApproved: overrides.isApproved || (() => true),
    isSeen: overrides.isSeen || (() => false),
    markSeen: overrides.markSeen || ((file, id) => { seenMarked.push(id); return true; }),
    postOutgoingObservation: overrides.postOutgoingObservation || (async (config, obs) => { posted.push(obs); return { ok: true }; }),
    log: () => {},
    _posted: posted,
    _seenMarked: seenMarked,
  };
}

test("a self-sent (fromMe) message in an allowlisted group is audited via postOutgoingObservation", async () => {
  const deps = fakeOutgoingDeps();
  const result = await handleOutgoingMessage(
    { id: "out1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", text: "k sending tech", timestamp: 1, fromMe: true },
    deps,
  );
  assert.equal(result.audited, true);
  assert.equal(deps._posted.length, 1);
  assert.equal(deps._posted[0].messageId, "out1");
  assert.equal(deps._seenMarked[0], "out1");
});

test("handleOutgoingMessage is a defensive no-op when fromMe is not true", async () => {
  const deps = fakeOutgoingDeps();
  const result = await handleOutgoingMessage(
    { id: "in1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", text: "hi", timestamp: 1, fromMe: false },
    deps,
  );
  assert.equal(result.audited, false);
  assert.equal(result.reason, "not_outgoing");
  assert.equal(deps._posted.length, 0);
});

test("an outgoing message in a non-allowlisted group is not audited", async () => {
  const deps = fakeOutgoingDeps({ isApproved: () => false });
  const result = await handleOutgoingMessage(
    { id: "out1", isGroupMessage: true, groupId: "unrelated@g.us", text: "hi", timestamp: 1, fromMe: true },
    deps,
  );
  assert.equal(result.audited, false);
  assert.equal(result.reason, "group_not_allowlisted");
  assert.equal(deps._posted.length, 0);
});

test("a duplicate outgoing message id is not audited twice", async () => {
  const deps = fakeOutgoingDeps({ isSeen: () => true });
  const result = await handleOutgoingMessage(
    { id: "out1", isGroupMessage: true, groupId: "g@g.us", groupName: "G", text: "hi", timestamp: 1, fromMe: true },
    deps,
  );
  assert.equal(result.audited, false);
  assert.equal(result.reason, "duplicate");
});

test("handleOutgoingMessage never calls postObservation (the incoming-only function) or enqueues to the incoming retry queue", async () => {
  const source = require("node:fs").readFileSync(require.resolve("../src/messageHandler"), "utf8");
  const fnSource = source.slice(source.indexOf("async function handleOutgoingMessage"));
  assert.ok(!/enqueueRetry\(/.test(fnSource));
  assert.ok(!/postObservation\(/.test(fnSource)); // "postObservation" is not a substring of "postOutgoingObservation"
});
