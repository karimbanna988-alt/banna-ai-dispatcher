# WhatsApp Shadow Bridge — Experimental, Disabled by Default

**Status: built and tested with a mocked client. Not paired. No real
WhatsApp session has ever been opened by this code.**

## Account-risk warning — read before ever pairing

This bridge uses a **linked-device WhatsApp Web session** (via
`whatsapp-web.js`), not the official Meta Cloud API. This is an
**unsupported approach**:

- WhatsApp does not officially support or endorse third-party linked-
  device automation. Using it carries a real risk of the account being
  **rate-limited, flagged, or logged out** without warning.
- A WhatsApp Web update can break `whatsapp-web.js` at any time,
  requiring maintenance with no advance notice from WhatsApp.
- The linked-device session can silently expire and need re-pairing.
- This is explicitly the user's chosen tradeoff because Meta Cloud API
  and paid alternatives (Zapier, a WhatsApp Business Solution Provider)
  are not available to this business. It is not being minimized or
  hidden here — it is real, and it applies to the real business's real
  WhatsApp number the moment pairing happens.

**Do not pair this against the business's real number without
accepting that risk explicitly, ideally testing with a spare/secondary
number first if one is available.**

## What this build is (Phase 4D)

- Local-only. Nothing here is reachable from the public Cloudflare
  tunnel that serves the connected CRM.
- **Disabled by default** (`WHATSAPP_SHADOW_ENABLED=false`). The Python
  CRM works completely normally whether this bridge is running,
  stopped, disabled, or has never been installed.
- **Incoming/read-only.** No function anywhere in this directory can
  send a WhatsApp message, reply, do a bulk send, modify a group, modify
  a contact, manage members, or read statuses/stories. See
  `test/no-send-static-check.test.js` — it greps this entire directory
  for send-capable calls and fails the build if one is ever added.
- **No historical backfill.** Only messages received while connected are
  ever processed.
- **Group-allowlisted only.** Every other chat (personal DMs, unrelated
  groups, family conversations, statuses, archived chats) is ignored
  before it is ever inspected further, let alone forwarded to Python.
- **No stealth/evasion code of any kind.** No fingerprint spoofing, no
  detection bypass, no attempt to make this look like manual phone use.

## What it is not (this phase)

- Not connected to WhatsApp yet — no QR has been scanned.
- Not able to send anything, ever, in this version.
- Not automatically mapped to technicians — every group→technician
  mapping is a deliberate, human-confirmed step (see below).
- Not a replacement for the existing manual Copy → Open WhatsApp →
  send → Confirm sent manually workflow, which is unchanged.

## Architecture

```
WhatsApp (linked device)
   |
   v
whatsapp-web.js Client (src/client.js -- the ONLY file that imports it)
   |  allowlist check -> dedupe -> deterministic ticket extraction
   v
src/messageHandler.js
   |  POST /observation (shared secret, loopback only)
   v
operator_api.py's own 127.0.0.1-only adapter listener (a different
port from the tunneled one) -- see docs in operator_api.py
   |  held in an in-memory pending-review queue, never auto-attached
   v
Connected CRM (Active screen) -- dispatcher explicitly attaches,
selects another job, or ignores. Only then does the existing,
already-audited DispatcherService record anything.
```

## Running it

```
cd whatsapp_shadow_bridge
npm install        # fetches whatsapp-web.js + puppeteer (~300MB, downloads Chromium)
cp .env.example .env
# edit .env: set WHATSAPP_SHADOW_ADAPTER_SECRET to a random value,
# and set the same value as WHATSAPP_BRIDGE_ADAPTER_SECRET in the
# main project's .env
node src/index.js               # health server only, bridge disabled
WHATSAPP_SHADOW_ENABLED=true node src/index.js --pair   # explicit pairing only
```

`npm install` has **not** been run as part of this build — it was
deliberately left for the user to run when actually ready to pair,
since it downloads a full Chromium binary. All automated tests in
`test/` run without it (they use a hand-written fake client, never the
real `whatsapp-web.js` package).

## Session storage

The linked-device session lives at
`<repo root>/.runtime/whatsapp-shadow-session/` — gitignored, never
committed, never uploaded, never printed. Deleting that directory forces
a fresh pairing next time.

## Local admin server (127.0.0.1 only)

- `GET /health` — `{enabled, paired, status}`
- `GET /groups` — discovered group names + IDs (only once paired)
- `GET /allowlist` / `POST /allowlist` / `DELETE /allowlist/:id` —
  manage which groups this bridge processes at all

Group **names** are for local review only — see
`docs/whatsapp_groups_readiness.md` for why raw WhatsApp internal group
IDs are never surfaced in the normal (tunnel-facing) dispatcher
interface. Once a group is allowlisted here, map it to a real
technician using the **existing, already-audited** Technicians
destination editor in the connected CRM (Settings → Technicians) —
this bridge never writes a technician's dispatch/closing group itself.
