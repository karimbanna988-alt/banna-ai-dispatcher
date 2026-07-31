"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { loadAllowlist, isApproved, addGroup, removeGroup, isValidGroupId } = require("../src/allowlist");

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

test("isValidGroupId accepts real WhatsApp group JID shapes", () => {
  assert.equal(isValidGroupId("111@g.us"), true);
  assert.equal(isValidGroupId("1700000000-1700000001@g.us"), true);
});

test("isValidGroupId rejects non-JID strings", () => {
  assert.equal(isValidGroupId("not-a-group"), false);
  assert.equal(isValidGroupId("15555550100@c.us"), false); // a personal/DM JID, not a group
  assert.equal(isValidGroupId(""), false);
  assert.equal(isValidGroupId(null), false);
  assert.equal(isValidGroupId(undefined), false);
});

test("addGroup rejects a malformed group id instead of silently accepting it", () => {
  const file = tmpFile();
  assert.throws(() => addGroup(file, { id: "totally not a jid", name: "Fake" }));
  const data = loadAllowlist(file);
  assert.equal(data.groups.length, 0);
});
