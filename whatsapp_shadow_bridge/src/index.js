/* Entry point. Always starts the local (127.0.0.1-only) health/admin
 * server, regardless of enabled state, so /health is always answerable.
 * Never touches whatsapp-web.js unless WHATSAPP_SHADOW_ENABLED=true AND
 * --pair is explicitly passed on the command line -- there is no path
 * from "just run this file" to "a real WhatsApp session opens". */

"use strict";

const { loadConfig } = require("./config");
const { createHealthServer } = require("./healthServer");

function main(argv = process.argv.slice(2)) {
  const config = loadConfig();
  const state = { paired: false, getGroups: null };

  const server = createHealthServer(config, state);
  console.log(`WhatsApp Shadow Bridge local admin server listening on http://127.0.0.1:${config.localAdminPort} (loopback only)`);
  console.log(`Bridge enabled: ${config.enabled}`);

  if (!config.enabled) {
    console.log("WHATSAPP_SHADOW_ENABLED=false -- bridge is disabled. The connected CRM works normally without it.");
    console.log("No WhatsApp session will be opened. Pass --pair with WHATSAPP_SHADOW_ENABLED=true to pair explicitly.");
    return { server, client: null };
  }

  if (!argv.includes("--pair")) {
    console.log("Bridge is enabled but --pair was not passed -- staying idle. Run with --pair to begin pairing.");
    return { server, client: null };
  }

  const { runPairing } = require("./pairing");
  const { initializeBridge } = require("./client");
  const client = runPairing(config, initializeBridge, console.log);
  if (client) {
    state.getGroups = async () => {
      const chats = await client.getChats();
      return chats.filter((c) => c.isGroup).map((c) => ({ id: c.id._serialized, name: c.name }));
    };
  }
  return { server, client };
}

if (require.main === module) {
  main();
}

module.exports = { main };
