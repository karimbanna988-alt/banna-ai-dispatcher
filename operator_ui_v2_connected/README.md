# LUXOR Operator UI V2 -- Connected (Zero-Subscription WhatsApp Bridge)

**Status: connected to the real database. No public tunnel is running
for this build -- see "Security" below before ever exposing it.**

This is the same visual system as [`operator_ui_v2/`](../operator_ui_v2)
(CSS copied unchanged), now wired to the real dispatcher database through
[`operator_api.py`](../operator_api.py) instead of fabricated in-memory
data. It implements the manual WhatsApp bridge: CRM prepares a message
and shows the exact destination group -> dispatcher copies it and opens
WhatsApp themselves -> dispatcher manually sends it -> dispatcher
returns and explicitly confirms. Nothing in this build ever sends a
WhatsApp or Quo message itself.

## What's connected in this build

- **Jobs** -- real pending Quo messages (`quo_pending_messages`), the
  same K/dispatch sequence `luxor_desk_pages/dispatch.py` already uses,
  through the same `DispatcherService` methods.
- **Active** -- real active jobs; "Paste technician's exact WhatsApp
  message" runs the existing deterministic commercial-update parser
  (`dispatcher.commercial`) and records everything to the existing
  Shadow Mode episode for that job.
- **Technicians** -- the real roster, including an admin destination
  editor that calls the existing audited
  `DispatcherService.update_technician_destinations()` -- the only
  write path for a technician's WhatsApp group names.

**Not connected in this build**: Closings and Reports remain fabricated
demo-only in `operator_ui_v2/` -- out of scope for this phase.

## Running it locally

```
python operator_api.py --port 8512
```

Serves both the JSON API (`/api/*`) and the static frontend from this
directory on one origin -- no separate static file server needed.

## Security

**This build has no login gate.** `luxor_desk.py` (the existing real
app) requires a password before showing any real data
(`luxor_desk_shared.require_login()`); this connected build does not
yet have an equivalent check, because none was requested in this phase.
It shows real customer names, phone numbers, and addresses to whoever
can reach it.

Because of this, **no public tunnel was created for this build** -- it
is reachable only at `http://localhost:8512` on this machine. Do not
tunnel or otherwise expose it publicly until an authentication gate
(matching `luxor_desk_shared.require_login()`'s pattern) is added.

## Zero-subscription guarantees

- No Meta Cloud API, no Zapier, no WhatsApp Business Solution Provider.
- No WhatsApp automation library of any kind (no whatsapp-web.js, no
  Baileys, no Selenium/Playwright/Puppeteer, no clipboard/window
  inspection, no simulated clicks or keystrokes).
- "Open WhatsApp" only ever navigates this browser tab/window to the
  `whatsapp://` URI scheme or `https://web.whatsapp.com/` -- an ordinary
  link, exactly like clicking a bookmark. It never claims the correct
  group was opened automatically.
- `operator_api.py` owns no SQL of its own and makes no outbound network
  call of any kind -- every write goes through an existing, already-
  audited `DispatcherService` method (see `tests/test_operator_api.py`'s
  structural guards, which grep-check both of these).
- No WhatsApp credential, session, or QR-code artifact is ever stored --
  there is no code path capable of producing one.
