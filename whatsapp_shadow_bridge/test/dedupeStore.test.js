"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { markIfNew, isSeen, markSeen } = require("../src/dedupeStore");

function tmpFile() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), "wsb-test-")), "seen.json");
}

test("first sighting of a message id is new", () => {
  const file = tmpFile();
  assert.equal(markIfNew(file, "msg-1"), true);
});

test("second sighting of the same message id is a duplicate", () => {
  const file = tmpFile();
  assert.equal(markIfNew(file, "msg-1"), true);
  assert.equal(markIfNew(file, "msg-1"), false);
  assert.equal(markIfNew(file, "msg-1"), false);
});

test("different message ids are independently tracked", () => {
  const file = tmpFile();
  assert.equal(markIfNew(file, "msg-1"), true);
  assert.equal(markIfNew(file, "msg-2"), true);
  assert.equal(markIfNew(file, "msg-1"), false);
});

test("requires a message id", () => {
  const file = tmpFile();
  assert.throws(() => markIfNew(file, null));
  assert.throws(() => markIfNew(file, ""));
});

test("isSeen never mutates the file -- pure peek", () => {
  const file = tmpFile();
  assert.equal(isSeen(file, "m1"), false);
  assert.equal(isSeen(file, "m1"), false); // still false -- isSeen never marks
  markSeen(file, "m1");
  assert.equal(isSeen(file, "m1"), true);
});

test("markSeen returns true for a genuinely new id, false for an already-seen one", () => {
  const file = tmpFile();
  assert.equal(markSeen(file, "m1"), true);
  assert.equal(markSeen(file, "m1"), false);
});
