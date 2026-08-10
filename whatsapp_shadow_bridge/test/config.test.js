"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { loadConfig } = require("../src/config");

test("bridge is disabled by default with no env at all", () => {
  const config = loadConfig({});
  assert.equal(config.enabled, false);
});

test("only the literal string 'true' enables the bridge", () => {
  assert.equal(loadConfig({ WHATSAPP_SHADOW_ENABLED: "true" }).enabled, true);
  assert.equal(loadConfig({ WHATSAPP_SHADOW_ENABLED: "TRUE" }).enabled, true);
  assert.equal(loadConfig({ WHATSAPP_SHADOW_ENABLED: "1" }).enabled, false);
  assert.equal(loadConfig({ WHATSAPP_SHADOW_ENABLED: "yes" }).enabled, false);
  assert.equal(loadConfig({ WHATSAPP_SHADOW_ENABLED: "" }).enabled, false);
});

test("session dir defaults outside the bridge package, under repo-root .runtime/", () => {
  const config = loadConfig({});
  assert.ok(config.sessionDir.includes(".runtime"));
  assert.ok(config.sessionDir.includes("whatsapp-shadow-session"));
});

test("local admin port defaults away from the tunneled connected-CRM port", () => {
  const config = loadConfig({});
  assert.notEqual(config.localAdminPort, 8512);
});

test("puppeteer protocol timeout has a generous default and is env-overridable", () => {
  assert.equal(loadConfig({}).puppeteerProtocolTimeoutMs, 300000);
  assert.equal(
    loadConfig({ WHATSAPP_SHADOW_PUPPETEER_PROTOCOL_TIMEOUT_MS: "600000" }).puppeteerProtocolTimeoutMs,
    600000
  );
});
