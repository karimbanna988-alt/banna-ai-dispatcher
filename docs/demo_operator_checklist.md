# Demo operator checklist

A short, practical checklist for running the CRM demo and (when you're
ready and present) the WhatsApp one-group pilot. No secrets, phone
numbers, group IDs, customer data, or tunnel URLs are recorded here —
see `docs/whatsapp_one_group_pilot.md` for the full step-by-step pairing
procedure this checklist summarizes.

## Exact first command

```
cd "C:\Users\child\Desktop\ai dispatcher"
powershell -ExecutionPolicy Bypass -File scripts\start_crm.ps1
```

## 1. Start the CRM

```
powershell -ExecutionPolicy Bypass -File scripts\start_crm.ps1
```
Leave that window open. Then open `http://127.0.0.1:8512` in a browser.

To confirm it's actually up from a second window:
```
powershell -ExecutionPolicy Bypass -File scripts\crm_health_check.ps1
```

## 2. Login and mobile demo sequence

1. Log in with the usual password.
2. **Jobs**: shows only NEW jobs from the last 60 minutes, capped at 5
   cards, newest first. Older unresolved jobs are behind the "Older
   unresolved jobs" control (with a search box) — not deleted, not
   auto-shown.
3. **Active**: shows in-progress jobs regardless of age.
4. **Closings** and **Completed**: load normally.
5. **Settings → Technicians**: real technicians, dispatch/closing group
   fields, a "Not configured" flag for anyone missing one.
6. **Settings → Learning Review**: real Shadow-mode episodes only.
7. To demo mobile: resize the browser window to about 390×844 (or open
   on an actual phone) — the bottom navigation (Jobs / Active / Closings
   / Completed / More) stays visible, no sideways scrolling, cards show
   age/status/one action button.

## 3. WhatsApp preflight (safe, read-only, no pairing)

```
cd whatsapp_shadow_bridge
powershell -ExecutionPolicy Bypass -File scripts\preflight.ps1
```
Resolve anything marked `[FAIL]` before pairing. `[WARN]` is fine to
proceed past.

## 4. Pairing (only while you're physically present)

```
powershell -ExecutionPolicy Bypass -File scripts\start_pairing.ps1
```
Scan the QR that prints in that terminal with the technician's phone
(WhatsApp → Settings → Linked Devices → Link a Device). Full detail:
`docs/whatsapp_one_group_pilot.md`.

## 5. One-group selection

After pairing succeeds, list discovered groups (name + ID only) at
`http://127.0.0.1:8514/groups`, then approve exactly one:
```
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8514/allowlist `
  -ContentType "application/json" `
  -Body (@{ id = "<group id>"; name = "<your label>" } | ConvertTo-Json)
```
Malformed group IDs are rejected automatically.

## 6. One incoming test message

Send one short, harmless message in the approved group from the
technician's phone. It should appear exactly once as a pending card in
the CRM's Active screen (group, sender, time, raw preview, ticket
guess).

## 7. Manual Attach / Ignore

- **Attach**: pick the correct real job first, then Attach. This only
  pre-fills the existing, already-reviewed technician-update flow — no
  job status changes until you take the normal "Save verified update"
  step.
- **Ignore**: dismisses it. No job data changes either way.

## 8. Safe shutdown

```
powershell -ExecutionPolicy Bypass -File whatsapp_shadow_bridge\scripts\stop.ps1
powershell -ExecutionPolicy Bypass -File scripts\stop_crm.ps1
```
Stopping either one never touches the real database or the paired
WhatsApp session.

## What normal "unavailable" states look like

- **CRM running, bridge not started/paired**: the Active screen's
  WhatsApp section is simply absent — no error banner, nothing to
  dismiss. This is normal, not a bug.
- **`crm_health_check.ps1` shows `[DOWN]`**: the CRM process isn't
  running — run `start_crm.ps1`.
- **`whatsapp_shadow_bridge\scripts\health_check.ps1` shows both
  ports `[DOWN]`**: the bridge isn't running — expected unless you've
  started pairing/normal mode.
- **`/groups` returns 503**: pairing hasn't completed yet, or the
  session disconnected — this is a normal "not ready" response, not a
  crash.
