/* The history importer is the ONE deliberate exception to "never reach
 * backward into chat history" (see tools/historyImport.js header). That
 * exception is narrow, and these tests keep it narrow: it may read, and
 * it may never send. The permanent src/ bans are untouched and still
 * enforced by no-send-static-check.test.js. */

"use strict";

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const TOOLS_DIR = path.join(__dirname, "..", "tools");

const FORBIDDEN_SEND_PATTERNS = [
  /\.sendMessage\s*\(/,
  /\.sendSeen\s*\(/,
  /\.reply\s*\(/,
  /\.logout\s*\(/,
];

test("the history importer contains no send-capable call", () => {
  for (const file of fs.readdirSync(TOOLS_DIR).filter((f) => f.endsWith(".js"))) {
    const source = fs.readFileSync(path.join(TOOLS_DIR, file), "utf8");
    for (const pattern of FORBIDDEN_SEND_PATTERNS) {
      assert.ok(!pattern.test(source), `tools/${file} must not match send pattern ${pattern}`);
    }
  }
});

test("the permanent no-history ban on src/ is still present and unweakened", () => {
  const check = fs.readFileSync(path.join(__dirname, "no-send-static-check.test.js"), "utf8");
  assert.match(check, /FORBIDDEN_HISTORY_PATTERNS/);
  assert.match(check, /\\.fetchMessages/);
});

test("importHistory only imports from allowlisted groups and respects the time window", async () => {
  const { importHistory } = require("../tools/historyImport");
  const mk = (id, ts, body, fromMe = false) => ({
    id: { _serialized: id }, timestamp: ts, body, fromMe,
    hasQuotedMsg: false, type: "chat", hasMedia: false, from: "x@c.us",
  });
  const now = Math.floor(Date.now() / 1000);
  const chats = [
    { isGroup: true, name: "Allowed", id: { _serialized: "ok@g.us" },
      fetchMessages: async () => [mk("a", now - 60, "Ticket: AAA1"), mk("b", now - 99999, "too old")] },
    { isGroup: true, name: "Blocked", id: { _serialized: "no@g.us" },
      fetchMessages: async () => [mk("c", now - 60, "Ticket: BBB2")] },
    { isGroup: false, name: "DM", id: { _serialized: "dm@c.us" },
      fetchMessages: async () => { throw new Error("a DM must never be read"); } },
  ];

  const posted = [];
  const report = await importHistory({
    client: { getChats: async () => chats },
    config: { allowlistFile: "x", dedupeFile: "y", retryQueueFile: "z" },
    sinceMs: Date.now() - 3600 * 1000,
    deps: {
      isApproved: (_f, id) => id === "ok@g.us",
      isSeen: () => false,
      markSeen: () => {},
      enqueueRetry: () => {},
      dequeueRetry: () => {},
      postObservation: async (_c, o) => { posted.push(o); },
      postOutgoingObservation: async () => {},
      log: () => {},
    },
  });

  assert.strictEqual(report.groupsScanned, 1, "only the allowlisted group is read");
  assert.strictEqual(posted.length, 1, "only the in-window message is forwarded");
  assert.strictEqual(posted[0].messageId, "a");
  assert.strictEqual(posted[0].extractedTicket, "AAA1");
});

test("an already-seen message is not imported twice", async () => {
  const { importHistory } = require("../tools/historyImport");
  const now = Math.floor(Date.now() / 1000);
  const posted = [];
  const report = await importHistory({
    client: { getChats: async () => [{
      isGroup: true, name: "G", id: { _serialized: "ok@g.us" },
      fetchMessages: async () => [{
        id: { _serialized: "dup" }, timestamp: now - 10, body: "Ticket: CCC3",
        fromMe: false, hasQuotedMsg: false, type: "chat", hasMedia: false, from: "x@c.us",
      }],
    }] },
    config: { allowlistFile: "x", dedupeFile: "y", retryQueueFile: "z" },
    sinceMs: Date.now() - 3600 * 1000,
    deps: {
      isApproved: () => true,
      isSeen: () => true,
      markSeen: () => {},
      enqueueRetry: () => {},
      dequeueRetry: () => {},
      postObservation: async (_c, o) => { posted.push(o); },
      postOutgoingObservation: async () => {},
      log: () => {},
    },
  });
  assert.strictEqual(posted.length, 0, "dedupe must block a re-import");
  assert.strictEqual(report.forwarded, 0);
  assert.strictEqual(report.skipped, 1);
});
