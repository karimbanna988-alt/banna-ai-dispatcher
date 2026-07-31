/* Incoming-message pipeline: allowlist check -> dedupe -> deterministic
 * ticket extraction -> forward to the Python adapter as an observation.
 * Every dependency (allowlist check, dedupe store, adapter client) is
 * injected, so this is fully testable with fakes and never needs a real
 * whatsapp-web.js Client or a real network call. Never attaches to a
 * job, never changes any job's status, never sends anything -- it only
 * ever forwards a captured observation for a human to review later. */

"use strict";

const { extractTicket } = require("./ticketExtractor");

/**
 * @param {object} message - { id, groupId, groupName, sender, timestamp, text, isGroupMessage, fromMe }
 * @param {object} deps - { config, isApproved, markIfNew, postObservation, log }
 * @returns {Promise<{forwarded: boolean, reason: string}>}
 */
async function handleIncomingMessage(message, deps) {
  const { config, isApproved, markIfNew, postObservation, log } = deps;
  const logger = log || (() => {});

  // Defense in depth: whatsapp-web.js's "message" event (the only one
  // client.js subscribes to) should never fire for the paired account's
  // own outgoing messages -- that's "message_create", which this bridge
  // never listens for. This check exists anyway, in case that library
  // guarantee is ever wrong, so an outgoing message can never be
  // mistaken for an incoming technician update.
  if (message.fromMe) {
    logger("ignored: outgoing message (sent by the paired account itself)");
    return { forwarded: false, reason: "outgoing_message" };
  }

  if (!message.isGroupMessage) {
    logger("ignored: not a group message (personal DM)");
    return { forwarded: false, reason: "not_group_message" };
  }
  if (!isApproved(config.allowlistFile, message.groupId)) {
    logger(`ignored: group ${message.groupId} is not on the allowlist`);
    return { forwarded: false, reason: "group_not_allowlisted" };
  }
  const isNew = markIfNew(config.dedupeFile, message.id);
  if (!isNew) {
    logger(`ignored: duplicate message id ${message.id}`);
    return { forwarded: false, reason: "duplicate" };
  }

  const extractedTicket = extractTicket(message.text);
  await postObservation(config, {
    messageId: message.id,
    groupId: message.groupId,
    groupName: message.groupName,
    sender: message.sender,
    timestamp: message.timestamp,
    text: message.text,
    extractedTicket,
  });
  logger(`forwarded observation ${message.id} (ticket guess: ${extractedTicket || "none"})`);
  return { forwarded: true, reason: "ok", extractedTicket };
}

module.exports = { handleIncomingMessage };
