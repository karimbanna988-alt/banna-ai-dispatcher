"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { initializeBridge } = require("../src/client");

test("initializeBridge refuses to run when the bridge is disabled -- never even requires whatsapp-web.js", () => {
  // If this reached `require("whatsapp-web.js")` it would throw a
  // MODULE_NOT_FOUND error instead (the real package isn't installed in
  // this test environment) -- asserting the *disabled* message proves
  // the guard runs first and whatsapp-web.js is never touched.
  assert.throws(
    () => initializeBridge({ enabled: false }),
    /WHATSAPP_SHADOW_ENABLED=false/,
  );
});

test("client.js contains no call to a WhatsApp send-capable method", () => {
  const source = fs.readFileSync(require.resolve("../src/client"), "utf8");
  const forbidden = [
    /\.sendMessage\s*\(/,
    /\.sendSeen\s*\(/,
    /\.sendStateTyping\s*\(/,
    /\.setStatus\s*\(/,
    /\.createGroup\s*\(/,
    /\.addParticipants\s*\(/,
    /\.removeParticipants\s*\(/,
  ];
  for (const pattern of forbidden) {
    assert.ok(!pattern.test(source), `client.js must not call ${pattern}`);
  }
});
