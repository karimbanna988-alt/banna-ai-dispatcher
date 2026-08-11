/* Explicit, human-triggered pairing flow. Never runs automatically --
 * only invoked by `node src/index.js --pair`, and only if
 * WHATSAPP_SHADOW_ENABLED=true. Renders the QR code as terminal text
 * only; it is never written to a file, never sent over HTTP, never
 * shown in the operator browser, never logged with any other bridge
 * output that could end up in a shared log file. */

"use strict";

// client.initialize() can reject with a transient Puppeteer/Chromium
// race (observed live: "ProtocolError: Execution context was
// destroyed", thrown while whatsapp-web.js's inject() step evaluates
// page script during WhatsApp Web's own internal reload while
// restoring an existing session -- nothing to do with whether the
// session itself is valid). Before this fix, nothing caught that
// rejection: it was an unhandled promise rejection, which crashes the
// entire Node process outright -- taking the admin/health server on
// this same port down with it, even though the health server has no
// dependency on WhatsApp pairing at all. A fresh browser launch
// (a new Client instance, same on-disk session directory) generally
// clears this race, so this retries a bounded number of times with a
// fresh Client per attempt before giving up -- it never deletes or
// logs out of the session (client.destroy() only closes the crashed
// browser process; LocalAuth has no destroy() override, so the
// on-disk session files are never touched here), and it never asks
// for a new QR beyond whatever initializeBridge()/LocalAuth would
// naturally require anyway.
const DEFAULT_MAX_INIT_ATTEMPTS = 3;
const DEFAULT_RETRY_DELAY_MS = 3000;

async function attemptPairingWithRetry(config, initializeBridge, log, hooks, options = {}) {
  const maxAttempts = options.maxAttempts || DEFAULT_MAX_INIT_ATTEMPTS;
  const retryDelayMs = options.retryDelayMs != null ? options.retryDelayMs : DEFAULT_RETRY_DELAY_MS;
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));

  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const client = initializeBridge(config, hooks);
    try {
      await client.initialize();
      // Resolves once the browser/page setup call returns -- pairing
      // itself is still event-driven from here (onQr/onReady fire
      // later, exactly as before this change). A resolved initialize()
      // means this attempt's browser is alive, so no more retries.
      return client;
    } catch (err) {
      lastError = err;
      log(`WhatsApp Shadow Bridge: pairing attempt ${attempt}/${maxAttempts} failed during browser initialization (${(err && err.message) || err}). This is a browser-injection failure, not evidence the paired session is invalid.`);
      if (typeof client.destroy === "function") {
        try {
          await client.destroy();
        } catch (destroyErr) {
          // Best-effort cleanup of the already-broken browser process only.
        }
      }
      if (attempt < maxAttempts) {
        log(`Retrying with a fresh browser instance in ${retryDelayMs}ms (reusing the existing paired session -- no new QR requested)...`);
        await sleep(retryDelayMs);
      }
    }
  }
  log(`WhatsApp Shadow Bridge: pairing failed after ${maxAttempts} attempts. Staying disconnected -- the admin server keeps running so /health is still answerable. Last error: ${(lastError && lastError.message) || lastError}`);
  return null;
}

function runPairing(config, initializeBridge, log = console.log, hooks = {}, options = {}) {
  if (!config.enabled) {
    log("WHATSAPP_SHADOW_ENABLED is not true -- refusing to pair. Set it explicitly to begin pairing.");
    return null;
  }
  // eslint-disable-next-line global-require -- optional terminal-only rendering dependency
  const qrcodeTerminal = require("qrcode-terminal");

  log("=".repeat(60));
  log("WhatsApp Shadow Bridge -- linked-device pairing");
  log("This uses WhatsApp's own 'Linked Devices' feature, the same");
  log("mechanism WhatsApp Web/Desktop already use. It is NOT the");
  log("official Meta Cloud API and is unsupported by WhatsApp --");
  log("your account could be flagged, rate-limited, or logged out");
  log("of this session unexpectedly. Only proceed if you accept that risk.");
  log("=".repeat(60));

  const clientHooks = {
    log,
    onQr: (qr) => {
      log("Scan this QR code from your phone: WhatsApp > Settings > Linked Devices > Link a Device");
      qrcodeTerminal.generate(qr, { small: true });
    },
    onReady: (client) => {
      log("Pairing complete. The bridge is now connected (incoming/read-only).");
      if (hooks.onReady) hooks.onReady(client);
    },
    onDisconnected: (reason) => {
      if (hooks.onDisconnected) hooks.onDisconnected(reason);
    },
    onAuthFailure: (message) => {
      if (hooks.onAuthFailure) hooks.onAuthFailure(message);
    },
  };

  return attemptPairingWithRetry(config, initializeBridge, log, clientHooks, options);
}

module.exports = { runPairing, attemptPairingWithRetry };
