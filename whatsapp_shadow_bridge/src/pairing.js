/* Explicit, human-triggered pairing flow. Never runs automatically --
 * only invoked by `node src/index.js --pair`, and only if
 * WHATSAPP_SHADOW_ENABLED=true. Renders the QR code as terminal text
 * only; it is never written to a file, never sent over HTTP, never
 * shown in the operator browser, never logged with any other bridge
 * output that could end up in a shared log file. */

"use strict";

function runPairing(config, initializeBridge, log = console.log, hooks = {}) {
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

  const client = initializeBridge(config, {
    log,
    onQr: (qr) => {
      log("Scan this QR code from your phone: WhatsApp > Settings > Linked Devices > Link a Device");
      qrcodeTerminal.generate(qr, { small: true });
    },
    onReady: () => {
      log("Pairing complete. The bridge is now connected (incoming/read-only).");
      if (hooks.onReady) hooks.onReady();
    },
    onDisconnected: (reason) => {
      if (hooks.onDisconnected) hooks.onDisconnected(reason);
    },
    onAuthFailure: (message) => {
      if (hooks.onAuthFailure) hooks.onAuthFailure(message);
    },
  });
  client.initialize();
  return client;
}

module.exports = { runPairing };
