/* Entry point. Always starts the local (127.0.0.1-only) health/admin
 * server, regardless of enabled state, so /health is always answerable.
 * Never touches whatsapp-web.js unless WHATSAPP_SHADOW_ENABLED=true AND
 * --pair is explicitly passed on the command line -- there is no path
 * from "just run this file" to "a real WhatsApp session opens". */

"use strict";

const { loadConfig } = require("./config");
const { createHealthServer } = require("./healthServer");

/**
 * @param {string[]} argv
 * @param {object} deps - injectable for tests: { config, runPairing, initializeBridge, createHealthServer, startRetryDrainLoop }.
 *   Defaults to the real implementations/real env-derived config; tests
 *   pass a fake config and fakes for the rest so no real whatsapp-web.js
 *   Client, real environment variable, or network call is ever needed.
 */
function main(argv = process.argv.slice(2), deps = {}) {
  const config = deps.config || loadConfig();
  const state = { paired: false, getGroups: null };

  const makeHealthServer = deps.createHealthServer || createHealthServer;
  const server = makeHealthServer(config, state);
  console.log(`WhatsApp Shadow Bridge local admin server listening on http://127.0.0.1:${config.localAdminPort} (loopback only)`);
  console.log(`Bridge enabled: ${config.enabled}`);

  if (!config.enabled) {
    console.log("WHATSAPP_SHADOW_ENABLED=false -- bridge is disabled. The connected CRM works normally without it.");
    console.log("No WhatsApp session will be opened. Pass --pair with WHATSAPP_SHADOW_ENABLED=true to pair explicitly.");
    return { server, client: null, state };
  }

  // Redelivers anything left in the durable retry queue from a prior
  // run (adapter was down, etc.) -- independent of WhatsApp pairing
  // state, since draining only needs the Python adapter to be
  // reachable, not a live WhatsApp session.
  const startRetryDrainLoop = deps.startRetryDrainLoop || require("./retryDrain").startRetryDrainLoop;
  startRetryDrainLoop(config, { log: console.log });

  if (!argv.includes("--pair")) {
    console.log("Bridge is enabled but --pair was not passed -- staying idle. Run with --pair to begin pairing.");
    return { server, client: null, state };
  }

  const { runPairing } = deps.runPairing ? { runPairing: deps.runPairing } : require("./pairing");
  const { initializeBridge } = deps.initializeBridge ? { initializeBridge: deps.initializeBridge } : require("./client");
  // state.paired and state.getGroups are set ONLY inside the real "ready"
  // callback -- never eagerly after client.initialize() returns, since
  // that call is synchronous and the WhatsApp Web session is not yet
  // usable at that point (this was the bug: /groups could be called,
  // and would throw a confusing internal error, before the session was
  // actually ready).
  const client = runPairing(config, initializeBridge, console.log, {
    onReady: () => {
      state.paired = true;
      state.getGroups = async () => {
        const chats = await client.getChats();
        return chats.filter((c) => c.isGroup).map((c) => ({ id: c.id._serialized, name: c.name }));
      };
    },
    onDisconnected: () => {
      state.paired = false;
      state.getGroups = null;
    },
    onAuthFailure: () => {
      state.paired = false;
      state.getGroups = null;
    },
  });
  return { server, client, state };
}

if (require.main === module) {
  main();
}

module.exports = { main };
