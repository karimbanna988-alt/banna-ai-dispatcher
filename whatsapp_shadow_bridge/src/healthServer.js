/* The bridge's own tiny local admin server -- 127.0.0.1 ONLY, never
 * 0.0.0.0, never proxied through Cloudflare or any tunnel. Exposes
 * health/status, the discovered-group list (for allowlist review), and
 * allowlist add/remove. Never exposes a send capability. Never serves
 * the QR code or session data to a browser -- QR display is terminal
 * text only (see pairing.js). */

"use strict";

const http = require("node:http");
const { loadAllowlist, addGroup, removeGroup } = require("./allowlist");

function createHealthServer(config, state) {
  const server = http.createServer((req, res) => {
    const send = (status, body) => {
      const data = JSON.stringify(body);
      res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      res.end(data);
    };

    if (req.method === "GET" && req.url === "/health") {
      return send(200, {
        enabled: config.enabled,
        paired: !!state.paired,
        status: state.paired ? "connected" : (config.enabled ? "disabled_or_connecting" : "disabled"),
      });
    }

    if (req.method === "GET" && req.url === "/groups") {
      if (!state.paired || !state.getGroups) {
        return send(503, { error: "bridge_not_ready", message: "The WhatsApp session is not ready yet." });
      }
      return state.getGroups()
        .then((groups) => send(200, { groups }))
        .catch((err) => {
          // Never echo the raw internal error back over HTTP -- it comes
          // from whatsapp-web.js/Puppeteer internals and could be an
          // unhelpful fragment or, in principle, something derived from
          // chat state. Log locally (this terminal only) for the
          // operator, return a sanitized code to the caller.
          console.error("WhatsApp Shadow Bridge: group discovery failed:", err && err.message);
          send(500, { error: "group_discovery_failed", message: "Could not list groups right now. Check the bridge terminal for details." });
        });
    }

    if (req.method === "GET" && req.url === "/allowlist") {
      return send(200, loadAllowlist(config.allowlistFile));
    }

    if (req.method === "POST" && req.url === "/allowlist") {
      let body = "";
      req.on("data", (chunk) => { body += chunk; if (body.length > 10000) req.destroy(); });
      req.on("end", () => {
        try {
          const parsed = JSON.parse(body || "{}");
          if (!parsed.id) return send(400, { error: "id is required" });
          const updated = addGroup(config.allowlistFile, { id: parsed.id, name: parsed.name });
          send(200, updated);
        } catch (err) {
          send(400, { error: err.message || "Invalid request body" });
        }
      });
      return;
    }

    if (req.method === "DELETE" && req.url.startsWith("/allowlist/")) {
      const id = decodeURIComponent(req.url.slice("/allowlist/".length));
      const updated = removeGroup(config.allowlistFile, id);
      return send(200, updated);
    }

    send(404, { error: "Not found" });
  });

  server.listen(config.localAdminPort, "127.0.0.1");
  return server;
}

module.exports = { createHealthServer };
