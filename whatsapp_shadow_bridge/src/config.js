/* Central config for the bridge. Every value has a safe default (bridge
 * disabled, loopback-only, no group approved yet) so simply running this
 * file with no .env at all still refuses to touch WhatsApp. */

"use strict";

const path = require("node:path");

const REPO_ROOT = path.resolve(__dirname, "..", "..");

function boolEnv(env, name, fallback) {
  const raw = env[name];
  if (raw === undefined || raw === "") return fallback;
  return raw.trim().toLowerCase() === "true";
}

function intEnv(env, name, fallback) {
  const raw = env[name];
  if (raw === undefined || raw === "") return fallback;
  const n = parseInt(raw, 10);
  return Number.isFinite(n) ? n : fallback;
}

function loadConfig(env = process.env) {
  return {
    // Disabled by default -- the one flag that gates whether client.js is
    // ever allowed to construct a real whatsapp-web.js Client.
    enabled: boolEnv(env, "WHATSAPP_SHADOW_ENABLED", false),

    // Session data (the linked-device credential) lives outside the repo
    // tree entirely, under a gitignored .runtime/ directory at the repo
    // root -- never inside whatsapp_shadow_bridge/ itself, so a stray
    // `git add .` in this directory can never pick it up either.
    sessionDir: env.WHATSAPP_SHADOW_SESSION_DIR
      || path.join(REPO_ROOT, ".runtime", "whatsapp-shadow-session"),

    // Local-only allowlist of approved technician group IDs. Starts
    // empty; nothing is processed until groups are explicitly added here.
    allowlistFile: env.WHATSAPP_SHADOW_ALLOWLIST_FILE
      || path.join(REPO_ROOT, ".runtime", "whatsapp-shadow-session", "allowlist.json"),

    dedupeFile: env.WHATSAPP_SHADOW_DEDUPE_FILE
      || path.join(REPO_ROOT, ".runtime", "whatsapp-shadow-session", "seen-message-ids.json"),

    // Durable holding area for a captured observation until the Python
    // adapter confirms it accepted and durably stored it -- see
    // deliveryQueue.js. Same directory/convention as dedupeFile above.
    retryQueueFile: env.WHATSAPP_SHADOW_RETRY_QUEUE_FILE
      || path.join(REPO_ROOT, ".runtime", "whatsapp-shadow-session", "pending-delivery.json"),

    retryDrainIntervalMs: intEnv(env, "WHATSAPP_SHADOW_RETRY_DRAIN_INTERVAL_MS", 30000),

    // The bridge's own tiny local admin/pairing server -- 127.0.0.1 only,
    // never bound to 0.0.0.0, never proxied through any tunnel.
    localAdminPort: intEnv(env, "WHATSAPP_SHADOW_LOCAL_PORT", 8514),

    // The narrow adapter on the Python side (operator_api.py's own
    // loopback-only listener, a different port from the tunneled one).
    adapterUrl: env.WHATSAPP_SHADOW_ADAPTER_URL || "http://127.0.0.1:8513",
    adapterSecret: env.WHATSAPP_SHADOW_ADAPTER_SECRET || "",

    maxMessageBytes: intEnv(env, "WHATSAPP_SHADOW_MAX_MESSAGE_BYTES", 8000),
  };
}

module.exports = { loadConfig, REPO_ROOT };
