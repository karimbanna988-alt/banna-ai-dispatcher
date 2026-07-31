# WhatsApp Shadow Bridge — one-group receive-only pilot

This is the exact, step-by-step Windows procedure for pairing the WhatsApp
Shadow Bridge, approving exactly one technician group, and testing one
real incoming message end-to-end. Every account/QR-facing step requires
you to be physically present — nothing here pairs, sends, or approves
anything on your behalf.

No secrets, real group IDs, phone numbers, customer data, or tunnel URLs
are recorded in this document.

## Before you start

- The bridge is **disabled by default**. Nothing in normal CRM operation
  starts it.
- `whatsapp-web.js` (the library this bridge uses) is an **unofficial**
  WhatsApp client. Linking a real account through it carries a real risk
  of that number being flagged or banned by WhatsApp — this is a risk of
  the connection method itself, independent of how the bridge code
  behaves. Only pair a number you're prepared to have on that risk.
- This bridge can only ever **receive**. There is no send capability
  anywhere in its source (enforced by automated tests, see
  `whatsapp_shadow_bridge/test/no-send-static-check.test.js`).

## 1. Open PowerShell in the correct directory

```
cd "C:\Users\child\Desktop\ai dispatcher\whatsapp_shadow_bridge"
```

## 2. Run preflight

```
powershell -ExecutionPolicy Bypass -File scripts\preflight.ps1
```

Resolve every `[FAIL]` line before continuing. `[WARN]` lines are safe to
proceed past but worth reading.

## 3. Start the Python CRM and adapter

In a separate terminal, from the repo root:

```
cd "C:\Users\child\Desktop\ai dispatcher"
python operator_api.py
```

This also starts the loopback-only Python bridge adapter (port 8513) as
a background thread — no separate command needed for it.

## 4. Start pairing mode (you must be physically present)

Back in the `whatsapp_shadow_bridge` terminal:

```
powershell -ExecutionPolicy Bypass -File scripts\start_pairing.ps1
```

## 5. Scan the QR

A QR code prints directly in that terminal. On the technician's phone:
**WhatsApp → Settings → Linked Devices → Link a Device**, then scan it.
The QR expires quickly (well under a minute) — if it goes stale before
you scan it, stop the script (Ctrl+C) and rerun step 4 for a fresh one.

## 6. Confirm bridge health

In a third terminal:

```
cd "C:\Users\child\Desktop\ai dispatcher\whatsapp_shadow_bridge"
powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1
```

Both the Node admin server (8514) and Python adapter (8513) should show
`[ OK ]`, loopback-only.

## 7. List group metadata only

The Node admin server exposes discovered groups (name + ID only — no
message content, no participants, no history) at:

```
http://127.0.0.1:8514/groups
```

You can open that URL in a browser on the same machine, or:

```
Invoke-RestMethod http://127.0.0.1:8514/groups
```

## 8. Select exactly one real technician group

Review the list from step 7 and identify the one real group for this
pilot. Do not approve anything yet.

## 9. Add only that group to the allowlist

```
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8514/allowlist `
  -ContentType "application/json" `
  -Body (@{ id = "<the exact group id from step 7>"; name = "<a label you choose>" } | ConvertTo-Json)
```

Only the group ID you explicitly provide here gets approved — nothing
else. The bridge rejects anything that isn't shaped like a real WhatsApp
group ID.

## 10. Map the group to one technician

In the connected CRM (the browser tab already open to `operator_api.py`):
**Settings → Technicians** → find the real technician → set their
Dispatch group / Closing group fields as usual (existing, already-audited
flow — nothing new was added for this pilot).

## 11. Send one harmless new test update

From the technician's phone, in the approved group, send one short,
harmless message (e.g. "test update, ignore").

## 12. Confirm it appears once in Active

In the CRM's Active screen, the pending-observation card should appear
exactly once, showing: group name, sender, received time, a raw text
preview, and a Ticket/PDL/Ref guess (which may be blank if the test
message didn't contain one — that's expected).

## 13. Manually attach or ignore it

- **Attach**: select the correct real job first, then Attach. This only
  pre-fills the existing, already-reviewed technician-update paste flow
  — it does not change job status by itself. You still take the normal
  "Save verified update" step afterward for anything to actually record.
- **Ignore**: dismisses the observation. No job data changes either way.

## 14. Confirm no WhatsApp message was sent

Check the approved group on the technician's phone — nothing should have
been sent from the paired account. The bridge has no code path capable of
sending a WhatsApp message at all.

## 15. Safely stop the bridge

```
powershell -ExecutionPolicy Bypass -File scripts\stop.ps1
```

This stops the Node process only — the paired session and the CRM keep
working (the CRM never depended on the bridge being up).

## 16. Remove the linked session (only if you want to fully disconnect)

```
powershell -ExecutionPolicy Bypass -File scripts\remove_session.ps1
```

Requires typing `REMOVE` to confirm. This deletes the local session files
so a fresh QR scan is required next time — it does **not** remove the
device from the phone's own Linked Devices list; do that on the phone too
if you want a complete disconnect.

## Troubleshooting

- **QR expired before I could scan it**: Ctrl+C the pairing script, rerun
  step 4. Normal — not a failure.
- **`/groups` returns 503**: pairing hasn't completed yet, or the session
  disconnected. Rerun step 4.
- **Bridge is down / not responding**: the CRM keeps working normally —
  the Active screen's WhatsApp section just goes quiet. Rerun step 4 or 6
  once you're ready.
- **Wrong group got a test message**: don't approve it. Only IDs you
  explicitly POST to `/allowlist` in step 9 are ever processed.
