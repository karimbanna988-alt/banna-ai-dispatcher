/* Deterministic ticket/PDL extraction from an incoming WhatsApp message's
 * text -- a pure function, no state, no network, no dependency on
 * whatsapp-web.js. Mirrors the same "PDL:"/"Ticket:" label conventions
 * the Python side's own message formats already use, but is a
 * deliberately separate, small implementation in this runtime -- never
 * shares code with the Python parser (this bridge has no dependency on
 * the Python codebase at all). Never invents a ticket: returns null when
 * nothing matches, rather than guessing. */

"use strict";

const TICKET_PATTERNS = [
  /\bPDL[:\s]+([A-Za-z0-9-]{3,20})\b/i,
  /\bTicket[:\s]+([A-Za-z0-9-]{3,20})\b/i,
  /\bRef(?:erence)?[:\s]+([A-Za-z0-9-]{3,20})\b/i,
];

function extractTicket(text) {
  if (!text || typeof text !== "string") return null;
  for (const pattern of TICKET_PATTERNS) {
    const match = text.match(pattern);
    if (match && match[1]) return match[1].toUpperCase();
  }
  return null;
}

module.exports = { extractTicket, TICKET_PATTERNS };
