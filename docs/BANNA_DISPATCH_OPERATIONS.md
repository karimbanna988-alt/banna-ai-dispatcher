# Banna Dispatch — Operations

**PRODUCT:** Banna Dispatch · **CURRENT TENANT:** Luxor Locksmith

Start, stop, recover, back up. Every command below was actually run
against this machine — nothing here is invented.

Presentation names live in `src/dispatcher/branding.py`
(`BANNA_PRODUCT_NAME`, `BANNA_TENANT_NAME`). No `LUXOR_*` environment
variable, fixture, script or business record was renamed.

## Start

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_banna_dispatch.ps1
```

Starts the console/API on **8512** (which hosts the loopback adapter on
**8513** inside the same process) and the WhatsApp bridge on **8514**.
Anything already running is left alone, so it is safe to run twice.

It prints real status and the current LAN URL, discovered at run time.
Verified output on this machine:

```
[ok]    Console already running on 8512 (adapter 8513 runs inside it)
[ok]    WhatsApp bridge already running on 8514
  8512 console   OK
  8513 adapter   OK
  8514 whatsapp  OK (existing session reused)
  Phone (same Wi-Fi):  http://192.168.1.105:8512/
```

**Pairing is never forced.** The bridge reuses the existing linked-device
session. If it cannot confirm one within 45 seconds the script prints
`OWNER ACTION REQUIRED - WHATSAPP PAIRING` **once** and stops. It never
loops attempts, and no QR is ever written to a log file — a QR, if
genuinely needed, appears only in the bridge's own terminal window.

The individual scripts still work and were not replaced:
`scripts\start_crm.ps1`, `scripts\stop_crm.ps1`, `scripts\crm_health_check.ps1`.

## Stop

```bash
powershell -ExecutionPolicy Bypass -File scripts\stop_banna_dispatch.ps1
```

Stops only the processes actually holding 8512/8514, and only if they
are the expected `python`/`node` executable — an unrelated Python or
Node workload is reported, not killed.

- The database is never touched. SQLite is durable across this kind of
  stop; nothing deletes, moves or truncates `data\dispatcher.db`.
- The WhatsApp session in `.runtime\whatsapp-shadow-session\` is **not**
  touched. Stopping is not logging out — the next start reuses it.

## Recovery

| Symptom | Component | Owner action | Expected result |
|---|---|---|---|
| Console won't load; browser can't connect | 8512 | Run the start script | Console answers on 8512 |
| Health bar shows **ADP** red | 8513 | Restart 8512 — the adapter is a thread inside it | ADP green |
| Health bar shows **WA** red, or "no message in the last hour" | 8514 | Check the bridge window; run the start script if it exited | WA green once a real message arrives |
| Bridge window shows a QR code | 8514 | Scan from WhatsApp → Linked Devices. Do not delete the session folder | `paired: true` |
| Queue count climbing and not returning to 0 | 8513/8514 | Adapter is rejecting deliveries — check the bridge window for the reason | Queue returns to 0 |
| Signed out unexpectedly | 8512 | Sign in again | Normal |

**Sessions are in memory by design: restarting 8512 signs everybody
out.** That is expected, not a fault.

### Restarting 8512 does not lose captured messages

The bridge queues every observation durably *before* delivery is
attempted and redelivers on a 30-second loop, so messages that arrive
while the console is down are delivered afterwards rather than lost.
This was exercised for real during this sprint — see below.

## Backup and restore

```bash
python scripts/backup_database.py
```

Uses SQLite's own `Connection.backup()` (not a file copy), so the
snapshot is consistent even mid-transaction. It never overwrites the
live database and prints no customer row content. Verified run:

```
path: C:\Users\child\Desktop\ai-dispatcher-db-backups\dispatcher.db.pre-manual-<UTC>.bak
integrity_check: ok
foreign_key_violations: 0
schema_up_to_date: True
```

Backups are written **outside the repository**, to
`C:\Users\child\Desktop\ai-dispatcher-db-backups\`.

Restore and verification use the existing scripts — read their `--help`
before use, and take a fresh backup first:

```bash
python scripts/verify_backup.py --help
python scripts/restore_database.py --help
```

## Health, and what each light actually means

The console header shows **CRM / ADP / WA** plus last message,
observations today, queue depth and shadow mode. Every value comes from
a real local source (`/api/live_health`) — none of it is decorative.

- **CRM** — the route ran and reached the database. The response is the proof.
- **ADP** — the adapter **thread's** actual liveness (`is_alive()`).
- **WA** — derived from *observation recency*, not a socket check.
  `operator_api.py` deliberately makes no outbound HTTP calls (enforced by
  `test_operator_api_makes_no_outbound_network_calls`), so it never polls
  8514. This is weaker than a socket check and is labelled as such — but
  it cannot lie in the dangerous direction: it can never claim
  "receiving" without a real message having arrived.
- **Queue** — the bridge's own durable queue file. Unreadable shows
  **unknown**, never "healthy".
- **Shadow** — `PASSIVE` unless `.runtime/active_analysis.lock` exists.
  Live capture being alive is **not** analysis and never implies a model
  is running.

## Live Operations

The **Live operations** button opens the operator event stream:
`RECEIVED → PARSED → CORRELATED → verdict → NEXT`, with business
evidence under each event and a `needs you` flag on anything requiring a
human.

It is a projection of already-persisted observations through the
existing interpretation pipeline. **No model call is involved**, so it
keeps updating while Claude is completely idle. Customer detail is
masked before it reaches the projection.

## Apprentice

The **Apprentice** button shows read-only playbook suggestions built
from repeated, exact-ticket-correlated intake→dispatch pairings.

There is no approve control, no approval endpoint, and no code path in
this application that can turn a suggestion into a routing rule. Every
payload carries `"active": false`.

Evidence bar: at least 4 distinct tickets across at least 2 distinct
days. Below that, nothing is proposed at all. Evidence concentrated into
very few days is capped as **TEMPORARY COVERAGE** and can never reach
HIGH confidence — a stand-in covering a weekend must never become a
permanent assignment.

## A real incident from this sprint

**25 captured messages were stuck and never persisted.** Every retry
failed with `observation.text is required`: they were caption-less media
messages captured *before* caption handling existed, so their text was
empty and no number of retries could ever fix it. They replayed forever.

Fixed by applying the same placeholder the capture path already uses,
during retry (`whatsapp_shadow_bridge/src/retryDrain.js`). All 25 were
recovered and persisted — **without restarting the bridge**, because the
retry drain is independent of WhatsApp, so the linked-device session was
never at risk.

This is why the health bar shows queue depth: a silently growing queue
is silent data loss.

## Private remote access (Tailscale Serve)

Set up 2026-08-15 so the owner can work from the iPhone over cellular.

```
iPhone --private tailnet--> HTTPS --> laptop --> 127.0.0.1:8512
```

**Private HTTPS URL:**

```
https://laptop-g7s8949k.taild695d8.ts.net/
```

Reachable **only** from devices signed into the owner's Tailscale
account. Verified at setup: Serve reports "tailnet only", **Funnel is
OFF**, the TLS chain validates against the system trust store, the app
login is still enforced (`/api/live_health` returns 401 unauthenticated),
and `8513` / `8514` are **not** reachable on the tailnet address — they
stay loopback-only. No router port was opened and no public tunnel exists.

A useful side effect: Serve sets `X-Forwarded-Proto: https`, which the
app already honours, so the session cookie now carries `Secure`.

The laptop's tailnet address is `100.120.56.2`. `http://100.120.56.2:8512/`
also works from tailnet devices (WireGuard-encrypted, still private) and
is the fallback if the HTTPS name ever misbehaves.

### After a reboot

The Tailscale service starts automatically and the Serve config is
persisted, so the HTTPS route is **expected** to return on its own —
confirm rather than assume, with `tailscale serve status`.

**The application does NOT auto-start.** After every reboot:

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_banna_dispatch.ps1
```

Then give the WhatsApp bridge a minute or two. The wrapper's readiness
check gives up at 45 seconds and prints "OWNER ACTION REQUIRED —
WHATSAPP PAIRING", but the bridge frequently needs longer to boot
Chromium and reconnect. **Wait and re-check before concluding a QR is
needed** — on 2026-08-15 it reconnected on its own with the existing
session and no QR was required.

### If the private HTTPS route stops

```bash
tailscale serve --bg http://127.0.0.1:8512
```

`tailscale serve status` must report **tailnet only**. If it ever
mentions Funnel or public access, turn it off immediately:

```bash
tailscale serve --https=443 off
```

### Never do this

`tailscale cert <name>` writes a real certificate **and private key**
into the current directory. It was run once during setup for
diagnosis and both files were deleted immediately. Serve manages its own
certificates internally — you do not need that command, and its output
must never be committed.
