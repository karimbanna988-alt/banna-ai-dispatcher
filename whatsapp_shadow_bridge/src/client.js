/* The ONLY file in this bridge that imports whatsapp-web.js. Requiring
 * the real package is deferred to initializeBridge() itself (not at
 * module load time) so every other module in this project -- and every
 * test -- can run without whatsapp-web.js installed at all.
 *
 * SEND CAPABILITY: this file never invokes the Client's message-send
 * capability, nor any other WhatsApp send/reply function. See
 * test/no-send-static-check.test.js, which greps this entire directory
 * for such calls and fails the build if one ever appears. The
 * whatsapp-web.js library exposes that capability as part of its own
 * Client class -- this file simply never reaches for it.
 *
 * GROUP/CONTACT MODIFICATION: this file never calls any group-admin,
 * contact-modification, or status/story-access method either -- read
 * (list groups, receive messages) and nothing else.
 */

"use strict";

const { handleIncomingMessage, handleOutgoingMessage } = require("./messageHandler");
const { isApproved } = require("./allowlist");
const { isSeen, markSeen } = require("./dedupeStore");
const { enqueue: enqueueRetry, dequeue: dequeueRetry } = require("./deliveryQueue");
const { postObservation, postOutgoingObservation } = require("./adapterClient");

function initializeBridge(config, options = {}) {
  if (!config.enabled) {
    throw new Error("initializeBridge() called while WHATSAPP_SHADOW_ENABLED=false -- refusing to start.");
  }
  const log = options.log || console.log;

  // eslint-disable-next-line global-require -- deliberately lazy, see file docstring
  const { Client, LocalAuth } = require("whatsapp-web.js");

  const client = new Client({
    authStrategy: new LocalAuth({ dataPath: config.sessionDir }),
    // No autoplay/status features enabled; default puppeteer headless
    // browser instance whatsapp-web.js itself manages for the WhatsApp
    // Web protocol -- this is the library's own implementation, not
    // additional automation code written here.
    puppeteer: { protocolTimeout: config.puppeteerProtocolTimeoutMs },
  });

  client.on("qr", (qr) => {
    log("A WhatsApp linked-device QR code is ready. This bridge does NOT scan it automatically.");
    log("Run the pairing terminal flow explicitly and scan it yourself from WhatsApp > Linked Devices.");
    if (options.onQr) options.onQr(qr);
  });

  client.on("ready", () => {
    log("WhatsApp Shadow Bridge: linked-device session ready (read-only, incoming-only).");
    if (options.onReady) options.onReady(client);
  });

  client.on("disconnected", (reason) => {
    log(`WhatsApp Shadow Bridge: disconnected (${reason}). The connected CRM keeps working normally without it.`);
    if (options.onDisconnected) options.onDisconnected(reason);
  });

  client.on("auth_failure", (message) => {
    log("WhatsApp Shadow Bridge: authentication failed -- the bridge is not connected.");
    if (options.onAuthFailure) options.onAuthFailure(message);
  });

  client.on("message", async (msg) => {
    try {
      const chat = await msg.getChat();
      // Best-effort only: if this message is a reply, capture which
      // message it quotes so a technician's reply (e.g. "k" under a
      // dispatch post) can later be correlated back to that job. Never
      // fetches anything beyond this one message's own quoted-message
      // reference -- not a history fetch.
      let quotedMessageId = null;
      if (msg.hasQuotedMsg) {
        try {
          const quoted = await msg.getQuotedMessage();
          quotedMessageId = quoted && quoted.id ? quoted.id._serialized : null;
        } catch (quoteErr) {
          quotedMessageId = null;
        }
      }
      const message = {
        id: msg.id && msg.id._serialized,
        groupId: chat.isGroup ? chat.id._serialized : null,
        groupName: chat.isGroup ? chat.name : null,
        sender: msg.author || msg.from,
        timestamp: msg.timestamp,
        text: msg.body,
        isGroupMessage: !!chat.isGroup,
        fromMe: !!msg.fromMe,
        quotedMessageId,
        mediaType: msg.type || null,
        hasMedia: !!msg.hasMedia,
      };
      await handleIncomingMessage(message, { config, isApproved, isSeen, markSeen, enqueueRetry, dequeueRetry, postObservation, log });
    } catch (err) {
      log(`WhatsApp Shadow Bridge: error handling incoming message: ${err.message}`);
    }
  });

  // "message_create" fires for BOTH incoming and self-sent messages
  // (unlike "message" above, which whatsapp-web.js only emits for
  // incoming ones). This listener exists solely to capture the
  // dispatcher's own outgoing messages for audit -- it explicitly
  // no-ops on every message that isn't fromMe, so it can never
  // duplicate or interfere with the incoming pipeline above, which
  // keeps listening on "message" exactly as before, unchanged.
  client.on("message_create", async (msg) => {
    try {
      if (!msg.fromMe) return; // incoming messages are handled by "message" above, not here
      const chat = await msg.getChat();
      const message = {
        id: msg.id && msg.id._serialized,
        groupId: chat.isGroup ? chat.id._serialized : null,
        groupName: chat.isGroup ? chat.name : null,
        timestamp: msg.timestamp,
        text: msg.body,
        isGroupMessage: !!chat.isGroup,
        fromMe: true,
        mediaType: msg.type || null,
        hasMedia: !!msg.hasMedia,
      };
      await handleOutgoingMessage(message, { config, isApproved, isSeen, markSeen, postOutgoingObservation, log });
    } catch (err) {
      log(`WhatsApp Shadow Bridge: error handling outgoing message audit: ${err.message}`);
    }
  });

  return client;
}

module.exports = { initializeBridge };
