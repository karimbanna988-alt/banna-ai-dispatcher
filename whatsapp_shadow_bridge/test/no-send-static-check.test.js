"use strict";

/* Permanent static check: the entire bridge source tree must never
 * contain a call to a WhatsApp send-capable, group-modifying, contact-
 * modifying, member-management, or status/story-access method --
 * regardless of which file it might be added to in the future. This is
 * intentionally broader and more paranoid than client.test.js's
 * client.js-only check. */

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const SRC_DIR = path.join(__dirname, "..", "src");

const FORBIDDEN_PATTERNS = [
  /\.sendMessage\s*\(/,
  /\.sendSeen\s*\(/,
  /\.sendStateTyping\s*\(/,
  /\.sendStateRecording\s*\(/,
  /\.setStatus\s*\(/,
  /\.setDisplayName\s*\(/,
  /\.createGroup\s*\(/,
  /\.addParticipants\s*\(/,
  /\.removeParticipants\s*\(/,
  /\.promoteParticipants\s*\(/,
  /\.demoteParticipants\s*\(/,
  /\.acceptInvite\s*\(/,
  /\.deleteChat\s*\(/,
  /\.archiveChat\s*\(/,
  /\.pinChat\s*\(/,
  /\.setProfilePicture\s*\(/,
  /\.blockContact\s*\(/,
  /\.unblockContact\s*\(/,
];

function allJsFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...allJsFiles(full));
    else if (entry.name.endsWith(".js")) out.push(full);
  }
  return out;
}

test("no file under whatsapp_shadow_bridge/src/ calls a WhatsApp send/modify-capable method", () => {
  const files = allJsFiles(SRC_DIR);
  assert.ok(files.length > 0, "expected to find source files to check");
  for (const file of files) {
    const source = fs.readFileSync(file, "utf8");
    for (const pattern of FORBIDDEN_PATTERNS) {
      assert.ok(
        !pattern.test(source),
        `${path.relative(SRC_DIR, file)} must not match forbidden send-capable pattern ${pattern}`,
      );
    }
  }
});

test("no bulk-messaging helper (loop calling sendMessage) exists anywhere in src/", () => {
  const files = allJsFiles(SRC_DIR);
  for (const file of files) {
    const source = fs.readFileSync(file, "utf8");
    assert.ok(!/for\s*\([^)]*\)\s*\{[^}]*sendMessage/s.test(source), `${file} looks like a bulk-send loop`);
  }
});

// Permanent static check: nothing under src/ may fetch historical/earlier
// messages. This bridge only ever reacts to the live "message" event as
// it happens -- it must never reach backward into chat history.
const FORBIDDEN_HISTORY_PATTERNS = [
  /\.fetchMessages\s*\(/,
  /\.loadEarlierMessages\s*\(/,
  /\.loadPreviousMessages\s*\(/,
  /searchMessages\s*\(/,
];

test("no file under whatsapp_shadow_bridge/src/ calls a historical-message-fetch method", () => {
  const files = allJsFiles(SRC_DIR);
  for (const file of files) {
    const source = fs.readFileSync(file, "utf8");
    for (const pattern of FORBIDDEN_HISTORY_PATTERNS) {
      assert.ok(
        !pattern.test(source),
        `${path.relative(SRC_DIR, file)} must not match forbidden history-fetch pattern ${pattern}`,
      );
    }
  }
});
