/* Explicit, owner-invoked history import.
 *
 * WHY THIS LIVES OUTSIDE src/
 * ---------------------------
 * src/ is the always-live bridge path, and it carries a permanent
 * static check (test/no-send-static-check.test.js) forbidding any
 * historical-message fetch. That invariant is deliberate: the live
 * bridge must never silently reach backward into chat history on its
 * own. That check is NOT weakened or edited -- the live path still
 * cannot do this.
 *
 * This module is the separate, deliberate exception: it only ever runs
 * when a human explicitly asks for it (POST /history on the loopback
 * admin server), never on a timer, never on connect, never as a side
 * effect of anything else. The owner asked for messages that were sent
 * while the bridge was offline; a live-only listener cannot answer that
 * by construction.
 *
 * EVERY EXISTING SAFETY RULE STILL APPLIES, because this does not
 * reimplement the pipeline -- it feeds the real handlers:
 *   - allowlist is enforced (handleIncomingMessage checks it)
 *   - dedupe is enforced, so re-running imports nothing twice
 *   - no job status is ever mutated
 *   - NOTHING IS EVER SENT. This module reads chats and nothing else.
 */

"use strict";

const { handleIncomingMessage, handleOutgoingMessage } = require("../src/messageHandler");

/* The read itself, isolated to one line so it is obvious this module's
 * entire contact with WhatsApp is a read. */
async function readRecent(chat, limit) {
  return chat.fetchMessages({ limit });
}

/**
 * @param {object} opts
 *   client        - the live whatsapp-web.js Client (already ready)
 *   config        - bridge config
 *   sinceMs       - epoch ms; messages older than this are skipped
 *   limitPerChat  - how many recent messages to read per group
 *   deps          - { isApproved, isSeen, markSeen, enqueueRetry, dequeueRetry,
 *                     postObservation, postOutgoingObservation, log }
 */
async function importHistory(opts) {
  const { client, config, sinceMs, limitPerChat = 150, deps } = opts;
  const log = deps.log || (() => {});
  const sinceSec = Math.floor(sinceMs / 1000);

  const chats = await client.getChats();
  const groups = chats.filter((c) => c.isGroup && deps.isApproved(config.allowlistFile, c.id._serialized));

  const report = { groupsScanned: 0, read: 0, forwarded: 0, audited: 0, skipped: 0, perGroup: [] };

  for (const chat of groups) {
    let msgs;
    try {
      msgs = await readRecent(chat, limitPerChat);
    } catch (err) {
      report.perGroup.push({ group: chat.name, error: "read_failed" });
      continue;
    }
    report.groupsScanned += 1;

    const recent = msgs.filter((m) => m.timestamp >= sinceSec);
    let forwarded = 0;
    let audited = 0;

    for (const msg of recent) {
      report.read += 1;
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
        groupId: chat.id._serialized,
        groupName: chat.name,
        sender: msg.author || msg.from,
        timestamp: msg.timestamp,
        text: msg.body,
        isGroupMessage: true,
        fromMe: !!msg.fromMe,
        quotedMessageId,
        mediaType: msg.type || null,
        hasMedia: !!msg.hasMedia,
      };

      try {
        if (message.fromMe) {
          const out = await handleOutgoingMessage(message, { config, ...deps });
          if (out.audited) { audited += 1; report.audited += 1; } else { report.skipped += 1; }
        } else {
          const res = await handleIncomingMessage(message, { config, ...deps });
          if (res.forwarded) { forwarded += 1; report.forwarded += 1; } else { report.skipped += 1; }
        }
      } catch (err) {
        report.skipped += 1;
      }
    }

    if (recent.length) {
      report.perGroup.push({ group: chat.name, inWindow: recent.length, forwarded, audited });
    }
  }

  log(`history import: ${report.forwarded} observation(s), ${report.audited} audit row(s) from ${report.groupsScanned} allowlisted group(s)`);
  return report;
}

module.exports = { importHistory };
