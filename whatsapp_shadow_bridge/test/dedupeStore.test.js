"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { markIfNew } = require("../src/dedupeStore");

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
