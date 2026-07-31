"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { handleIncomingMessage } = require("../src/messageHandler");

function fakeDeps(overrides = {}) {
  const posted = [];
  return {
    config: { allowlistFile: "fake-allowlist", dedupeFile: "fake-dedupe" },
    isApproved: overrides.isApproved || (() => true),
    markIfNew: overrides.markIfNew || (() => true),
    postObservation: overrides.postObservation || (async (config, obs) => { posted.push(obs); return { ok: true }; }),
    log: () => {},
    _posted: posted,
  };
}

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
  const deps = fakeDeps({ markIfNew: () => false });
  const result = await handleIncomingMessage(
    { id: "m1", isGroupMessage: true, groupId: "g@g.us", text: "hi", timestamp: 1 },
    deps,
  );
  assert.equal(result.forwarded, false);
  assert.equal(result.reason, "duplicate");
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

test("never changes job status or attaches to anything -- this module has no such function to call", async () => {
  // Structural: handleIncomingMessage's only side effect is postObservation.
  // There is no job-status or job-attachment parameter anywhere in its
  // signature or the deps it accepts.
  const source = require("node:fs").readFileSync(require.resolve("../src/messageHandler"), "utf8");
  assert.ok(!/job\.status/.test(source));
  assert.ok(!/attach/i.test(source) || /never attach/i.test(source));
});
