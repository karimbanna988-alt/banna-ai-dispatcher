/* Incoming-message pipeline: allowlist check -> dedupe -> deterministic
 * ticket extraction -> durably queue -> deliver to the Python adapter ->
 * mark seen only once delivery is confirmed. Every dependency (allowlist
 * check, dedupe store, retry queue, adapter client) is injected, so this
 * is fully testable with fakes and never needs a real whatsapp-web.js
 * Client or a real network call. Never attaches to a job, never changes
 * any job's status, never sends anything -- it only ever forwards a
 * captured observation for a human to review later.
 *
 * Ordering is deliberate and load-bearing: a message is enqueued
 * durably BEFORE delivery is attempted, and only marked "seen" in the
 * dedupe store AFTER the adapter confirms it accepted and durably
 * stored it. A failed delivery (adapter down, rejected, network error)
 * leaves the message in the retry queue and NOT marked seen -- so it
 * gets retried instead of being silently lost. The dedupe file
 * historically doubled as the only durability signal, marked before
 * delivery was even attempted; a failed delivery under that scheme was
 * unrecoverable, since this bridge never imports chat history to catch
 * up later. */

"use strict";

const { extractTicket } = require("./ticketExtractor");

/**
 * @param {object} message - { id, groupId, groupName, sender, timestamp, text, isGroupMessage, fromMe, quotedMessageId }
 * @param {object} deps - { config, isApproved, isSeen, markSeen, enqueueRetry, dequeueRetry, postObservation, log }
 * @returns {Promise<{forwarded: boolean, reason: string}>}
 */
async function handleIncomingMessage(message, deps) {
  const { config, isApproved, isSeen, markSeen, enqueueRetry, dequeueRetry, postObservation, log } = deps;
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
  if (isSeen(config.dedupeFile, message.id)) {
    logger(`ignored: duplicate message id ${message.id}`);
    return { forwarded: false, reason: "duplicate" };
  }

  const extractedTicket = extractTicket(message.text);
  const observation = {
    messageId: message.id,
    groupId: message.groupId,
    groupName: message.groupName,
    sender: message.sender,
    timestamp: message.timestamp,
    text: message.text,
    extractedTicket,
    quotedMessageId: message.quotedMessageId || null,
  };

  // Durably captured BEFORE delivery is attempted -- a crash or an
  // adapter outage right after this line still leaves the message
  // recoverable (see the retry-drain loop in index.js), instead of
  // existing only in this function's local variables.
  if (enqueueRetry) enqueueRetry(config.retryQueueFile, observation);

  try {
    await postObservation(config, observation);
  } catch (err) {
    logger(`adapter delivery failed for ${message.id}, left queued for retry: ${err.message}`);
    return { forwarded: false, reason: "adapter_delivery_failed" };
  }

  markSeen(config.dedupeFile, message.id);
  if (dequeueRetry) dequeueRetry(config.retryQueueFile, message.id);
  logger(`forwarded observation ${message.id} (ticket guess: ${extractedTicket || "none"})`);
  return { forwarded: true, reason: "ok", extractedTicket };
}

module.exports = { handleIncomingMessage };
