/* Durable, file-backed retry queue for observations that were captured
 * from a real incoming WhatsApp message but could not be delivered to
 * the Python adapter yet (adapter down, wrong secret, transient network
 * error, etc.). Same on-disk convention as dedupeStore.js/allowlist.js
 * -- a plain JSON file under the gitignored session directory, no
 * database, no new dependency.
 *
 * Exists because whatsapp-web.js's "message" event fires exactly once,
 * live, and this bridge is explicitly forbidden from importing chat
 * history to "catch up" later -- so a failed delivery has to be kept
 * somewhere durable on the bridge's own disk, or it is gone for good.
 */

"use strict";

const fs = require("node:fs");
const path = require("node:path");

function ensureDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function load(queueFile) {
  try {
    const raw = fs.readFileSync(queueFile, "utf8");
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed.queued) ? parsed.queued : [];
  } catch (err) {
    return [];
  }
}

function save(queueFile, queued) {
  ensureDir(queueFile);
  fs.writeFileSync(queueFile, JSON.stringify({ queued }, null, 2), "utf8");
}

/** Durably records an observation as "not yet confirmed delivered".
 * Safe to call more than once for the same message id -- replaces the
 * existing entry rather than duplicating it. */
function enqueue(queueFile, observation) {
  if (!observation || !observation.messageId) throw new Error("observation.messageId is required");
  const queued = load(queueFile);
  const next = queued.filter((o) => o.messageId !== observation.messageId);
  next.push(observation);
  save(queueFile, next);
}

/** Removes an observation once its delivery is confirmed accepted. */
function dequeue(queueFile, messageId) {
  const queued = load(queueFile);
  const next = queued.filter((o) => o.messageId !== messageId);
  if (next.length !== queued.length) save(queueFile, next);
}

module.exports = { load, save, enqueue, dequeue };
