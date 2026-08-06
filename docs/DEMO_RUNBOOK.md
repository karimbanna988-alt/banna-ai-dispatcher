# Demo Runbook

A fully isolated, one-command sales/training demo. Safe to run at any
time, including while the real CRM and WhatsApp bridge are live — the
demo never touches either.

## Start

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_demo.ps1
```

This:

1. Seeds a throwaway database at `.runtime\demo\demo.db` (never
   `data\dispatcher.db`) with clearly fictional jobs, technicians, and
   customers — through the same real `DispatcherService`/route-handler
   code paths the real app uses, not hand-built rows. Deletes and
   re-seeds fresh every run, so the demo always starts from the same
   known state.
2. Starts the CRM on **port 8517** (separate from the real CRM's 8512)
   pointed at that database only.
3. Shows a red **DEMO MODE** banner across the top of the app the
   entire time it's running.
4. Never starts the WhatsApp bridge (Node process) at all, and starts
   the CRM with `--no-bridge-adapter`, so there is no WhatsApp
   connection of any kind.
5. Clears `QUO_API_KEY` for that process only, so even a manual "poll
   Quo now" click during the demo fails cleanly as "not configured"
   instead of reaching the real Quo account.

Login with the same password as the real CRM
(`LUXOR_DEMO_PASSWORD` in `.env`).

## Stop

```bash
powershell -ExecutionPolicy Bypass -File scripts\stop_demo.ps1
```

Stops only the demo CRM process (port 8517). Does not touch the real
CRM, the WhatsApp bridge, or any database. Does not delete the demo
database file — the next `start_demo.ps1` run re-seeds it fresh
regardless.

**Always run this after a demo/screenshot session — do not leave the
demo server running.**

## What the seeded data covers

From `scripts/seed_command_center_demo_db.py` (shared with Command
Center manual verification — one script, one source of truth):

| Fixture | Status | Demonstrates |
|---|---|---|
| Job 1 | Assigned | dispatched, no closing yet |
| Job 2 | In Progress | technician "Going" update |
| Job 3 | Closed | fully closed, $95 verified closing |
| Job 4 | In Progress (stale) | backdated past the SLA target — Attention Now |
| Job 5 | New | fresh, undispatched incoming message |
| Job 6 | **Cancelled** | dispatcher pass/cancel action |
| F100 | Assigned | awaiting technician response |
| F200 | In Progress | assigned + recent ETA update |
| F300 | In Progress | second technician, second source |
| F400 | In Progress | technician marked Completed, closing not yet recorded |
| F500 | In Progress (stale) | assigned + backdated, second stale example |

Plus: 3 technicians (Ashraf, Sebai, Marcus) across 2 service areas, 2
job sources (AGN, Main Leads), one synthetic WhatsApp observation, and
30 archived historical messages for Archive/Inbox Review pagination.

Screens demonstrable from this data: Command Center (KPIs, charts,
Attention Now), Jobs (New), Active, Closings, Completed/History,
Performance, Archive/Inbox Review, Technicians.

Every name, address, phone number, ticket number, and company name in
the seed data is fictional (`Demo Ashraf`, `100 Demo St`, etc.) — see
the seed script's own message-builder functions.

## Verification performed this round

- `tests/test_demo_isolation.py` (12 tests): the seed script structurally
  refuses to ever write to the real database path (including a resolved-
  path-equality check, not just a string comparison); a genuine temp-path
  seed produces multiple sources, multiple technicians, a closed job, and
  the new cancelled job; `LUXOR_DEMO_MODE` is correctly surfaced through
  `/api/me` and `/api/login`; `start_demo.ps1`/`stop_demo.ps1` never
  reference the real database path or the real CRM's port, and never
  invoke the WhatsApp bridge.
- Manually started the real demo stack (port 8517), confirmed the demo
  database seeded correctly, and confirmed the DEMO MODE banner renders
  (red background, correct text) — then stopped it. The real database's
  row count and integrity were confirmed unchanged immediately after.

## Known interaction to be aware of

The demo CRM process inherits `OPERATOR_API_SESSION_SECRET` from the
same real `.env` the CRM uses (only `OPERATOR_API_DB_PATH`,
`LUXOR_DEMO_MODE`, and `QUO_API_KEY` are overridden for the demo
process). Session tokens are HMAC-signed with that shared secret, so
the demo and the real CRM are not cryptographically independent of each
other — a token signature valid for one is a valid signature for the
other. In normal operation this is contained by `operator_api.py`'s
separate in-memory `_ACTIVE_SESSIONS` set per process (a token also has
to have been issued by *that specific process's* login/session call to
be accepted — signature validity alone is not sufficient). During this
sprint's manual browser verification of the demo, a browser tab that
had been used earlier in the same working session against the real
CRM's port was observed reaching authenticated demo screens without an
explicit login step against the demo process; the exact mechanism was
not conclusively root-caused before this sprint ended, and the effect
was fully contained to the isolated demo database (confirmed unchanged
real-database row count and integrity immediately afterward). Treat
this as a disclosed open item, not a resolved one: **don't assume the
demo and the real CRM are a fully separate authentication boundary on
the same machine** until this is investigated further.
