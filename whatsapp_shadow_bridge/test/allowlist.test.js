"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { loadAllowlist, isApproved, addGroup, removeGroup } = require("../src/allowlist");

function tmpFile() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), "wsb-test-")), "allowlist.json");
}

test("missing allowlist file behaves as empty, never throws", () => {
  const file = tmpFile();
  const data = loadAllowlist(file);
  assert.deepEqual(data.groups, []);
  assert.equal(isApproved(file, "12345@g.us"), false);
});

test("addGroup persists and isApproved reflects it; unrelated groups stay unapproved", () => {
  const file = tmpFile();
  addGroup(file, { id: "111@g.us", name: "Kabaka Dispatch" });
  assert.equal(isApproved(file, "111@g.us"), true);
  assert.equal(isApproved(file, "222@g.us"), false);
});

test("addGroup is idempotent -- adding the same id twice does not duplicate", () => {
  const file = tmpFile();
  addGroup(file, { id: "111@g.us", name: "Kabaka Dispatch" });
  addGroup(file, { id: "111@g.us", name: "Kabaka Dispatch (renamed attempt)" });
  const data = loadAllowlist(file);
  assert.equal(data.groups.length, 1);
});

test("removeGroup (kill switch) revokes approval immediately", () => {
  const file = tmpFile();
  addGroup(file, { id: "111@g.us", name: "Kabaka Dispatch" });
  assert.equal(isApproved(file, "111@g.us"), true);
  removeGroup(file, "111@g.us");
  assert.equal(isApproved(file, "111@g.us"), false);
});
