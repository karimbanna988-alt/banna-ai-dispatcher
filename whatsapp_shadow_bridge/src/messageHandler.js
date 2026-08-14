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

/* A media message (photo, voice note, sticker, etc.) with no caption
 * arrives from whatsapp-web.js with an empty msg.body -- there is no
 * text to forward. Without this, such a message can never satisfy
 * adapterClient's "text is required" validation, so it fails locally on
 * every single retry attempt forever (the retry queue can never drain
 * it) and its arrival is never recorded anywhere. This never reads or
 * downloads the actual media -- it only labels the fact that a
 * non-text message arrived, using metadata whatsapp-web.js already
 * handed this bridge on the message object (type, hasMedia), so the
 * observation is still 100% honest: it never claims to know what the
 * media contains. */
function describeNonTextMessage(message) {
  if (message.hasMedia) return `[media message, no caption -- type: ${message.mediaType || "unknown"}]`;
  if (message.mediaType && message.mediaType !== "chat") return `[non-text message -- type: ${message.mediaType}]`;
  return "[empty message, no text content]";
}

/**
 * @param {object} message - { id, groupId, groupName, sender, timestamp, text, isGroupMessage, fromMe, quotedMessageId, mediaType, hasMedia }
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
    text: message.text || describeNonTextMessage(message),
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

/**
 * Audit-only capture of a self-sent (fromMe) message in an allowlisted
 * group -- e.g. the dispatcher manually typing a company update or a
 * closing directly in WhatsApp. Deliberately posts with a different
 * event_type ("outgoing_message") than handleIncomingMessage, which the
 * Python adapter routes to a completely separate audit table. This
 * function never enqueues to the incoming retry queue, never calls
 * postObservation, and never returns anything an actionable pipeline
 * could mistake for a real observation -- it only ever calls
 * postOutgoingObservation.
 *
 * @param {object} message - same shape as handleIncomingMessage, but fromMe must be true
 * @param {object} deps - { config, isApproved, isSeen, markSeen, postOutgoingObservation, log }
 */
async function handleOutgoingMessage(message, deps) {
  const { config, isApproved, isSeen, markSeen, postOutgoingObservation, log } = deps;
  const logger = log || (() => {});

  if (!message.fromMe) {
    // Defense in depth -- this function must never be reachable for an
    // incoming message. Caller (client.js) already filters on fromMe
    // before invoking this, but a wrong call here must be a safe no-op,
    // not a silent misclassification.
    logger("ignored: handleOutgoingMessage called with a non-outgoing message (defensive no-op)");
    return { audited: false, reason: "not_outgoing" };
  }
  if (!message.isGroupMessage) {
    return { audited: false, reason: "not_group_message" };
  }
  if (!isApproved(config.allowlistFile, message.groupId)) {
    return { audited: false, reason: "group_not_allowlisted" };
  }
  if (isSeen(config.dedupeFile, message.id)) {
    return { audited: false, reason: "duplicate" };
  }

  const observation = {
    messageId: message.id,
    groupId: message.groupId,
    groupName: message.groupName,
    timestamp: message.timestamp,
    text: message.text || describeNonTextMessage(message),
  };

  try {
    await postOutgoingObservation(config, observation);
  } catch (err) {
    logger(`outgoing audit delivery failed for ${message.id}: ${err.message}`);
    return { audited: false, reason: "adapter_delivery_failed" };
  }

  markSeen(config.dedupeFile, message.id);
  logger(`audited outgoing message ${message.id}`);
  return { audited: true, reason: "ok" };
}

module.exports = { handleIncomingMessage, handleOutgoingMessage, describeNonTextMessage };
