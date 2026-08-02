/* HTTP client for the narrow, localhost-only Python adapter
 * (operator_api.py's own loopback listener, a different port from the
 * tunneled one). This file only ever POSTs an "observation" -- a
 * captured incoming message -- for the Python side to hold in a local
 * pending-review queue. It has no function capable of sending a
 * WhatsApp message, replying, or writing to the main dispatcher
 * database; only dispatcher.application.DispatcherService (Python) can
 * ever do that, and only after a human confirms the observation in the
 * CRM. */

"use strict";

function validateObservation(observation, maxBytes) {
  const required = ["messageId", "groupId", "groupName", "text", "timestamp"];
  for (const field of required) {
    if (!observation[field]) throw new Error(`observation.${field} is required`);
  }
  const size = Buffer.byteLength(JSON.stringify(observation), "utf8");
  if (size > maxBytes) throw new Error(`observation exceeds max size (${size} > ${maxBytes} bytes)`);
}

async function postObservation(config, observation, fetchImpl = fetch) {
  validateObservation(observation, config.maxMessageBytes);
  const res = await fetchImpl(`${config.adapterUrl}/observation`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Bridge-Secret": config.adapterSecret,
    },
    body: JSON.stringify({
      event_type: "incoming_message",
      group_id: observation.groupId,
      group_name: observation.groupName,
      sender: observation.sender || null,
      timestamp: observation.timestamp,
      message_id: observation.messageId,
      text: observation.text,
      extracted_ticket: observation.extractedTicket || null,
      quoted_message_id: observation.quotedMessageId || null,
    }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`adapter rejected observation: ${res.status} ${body}`.trim());
  }
  return res.json();
}

async function checkHealth(config, fetchImpl = fetch) {
  try {
    const res = await fetchImpl(`${config.adapterUrl}/health`, { method: "GET" });
    return res.ok;
  } catch (err) {
    return false;
  }
}

module.exports = { postObservation, checkHealth, validateObservation };
