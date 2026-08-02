/* Local-only, file-backed dedup set keyed by WhatsApp message ID. Keeps
 * the bridge from forwarding the same incoming message twice (e.g. on a
 * reconnect/replay) -- no database, just a bounded JSON array on disk
 * under the same gitignored session directory. */

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const MAX_ENTRIES = 5000;

function ensureDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function load(dedupeFile) {
  try {
    const raw = fs.readFileSync(dedupeFile, "utf8");
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed.seen) ? parsed.seen : [];
  } catch (err) {
    if (err.code === "ENOENT") return [];
    return [];
  }
}

function save(dedupeFile, seen) {
  ensureDir(dedupeFile);
  fs.writeFileSync(dedupeFile, JSON.stringify({ seen }, null, 2), "utf8");
}

/** Returns true if this is the first time messageId has been seen (and
 * records it); false if it's a duplicate. */
function markIfNew(dedupeFile, messageId) {
  if (!messageId) throw new Error("messageId is required");
  const seen = load(dedupeFile);
  if (seen.includes(messageId)) return false;
  seen.push(messageId);
  while (seen.length > MAX_ENTRIES) seen.shift();
  save(dedupeFile, seen);
  return true;
}

/** Peek-only membership check -- never mutates the dedupe file. Used to
 * skip a message we've already durably delivered, without marking
 * anything as seen ourselves (see markSeen below for why that's a
 * separate step). */
function isSeen(dedupeFile, messageId) {
  return load(dedupeFile).includes(messageId);
}

/** Mutate-only: records messageId as seen. Deliberately separate from
 * the duplicate check (isSeen) -- messageHandler.js only calls this
 * AFTER the Python adapter has confirmed durable acceptance, so a
 * message that fails delivery is never marked seen and can be retried
 * (see deliveryQueue.js). Calling this before delivery is confirmed is
 * exactly the bug this split fixes: a message could be marked "seen"
 * and then never actually reach the CRM, with no way to recover it
 * short of importing chat history (which this bridge never does). */
function markSeen(dedupeFile, messageId) {
  if (!messageId) throw new Error("messageId is required");
  const seen = load(dedupeFile);
  if (seen.includes(messageId)) return false;
  seen.push(messageId);
  while (seen.length > MAX_ENTRIES) seen.shift();
  save(dedupeFile, seen);
  return true;
}

module.exports = { markIfNew, isSeen, markSeen, load, save };
