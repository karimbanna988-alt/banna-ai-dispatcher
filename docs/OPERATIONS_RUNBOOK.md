# Operations Runbook

Day-to-day operator procedures: how to check system health, what each
result means, and what to do about it. Written for a dispatcher/owner
running this locally on Windows, not a software engineer.

## Daily / pre-shift / pre-demo health check

```bash
python tools/system_readiness.py
```

Read-only, end to end. It never starts, stops, or restarts anything,
never modifies any database, and never prints a secret's actual value
(only whether one is present, and a short one-way fingerprint used to
confirm two secrets match without displaying either one). Safe to run
at any time, including while the CRM and WhatsApp bridge are live.

### What it checks

| # | Check | What a problem means |
|---|---|---|
| 1 | CRM (`operator_api.py`) on port 8512 | If down: dispatchers can't use the CRM. Start with `scripts\start_crm.ps1`. |
| 2 | Python bridge adapter on port 8513 | Started automatically by the CRM; down only if the CRM itself is down. |
| 3 | Node WhatsApp Shadow Bridge on port 8514, paired state | If not paired: WhatsApp messages are not being captured. Re-pairing requires physical presence to scan a QR code — see `whatsapp_shadow_bridge/scripts/start_pairing.ps1`. This tool never re-pairs or restarts the bridge itself. |
| 4 | WhatsApp allowlist size | A count only — never which groups. Use the bridge's own local admin page to review actual group names. |
| 5 | Real database `PRAGMA integrity_check` | A FAIL here means possible database corruption — stop and restore from the most recent backup (see below) rather than continuing to use the CRM. |
| 6 | Real database `PRAGMA foreign_key_check` | A FAIL here means orphaned rows — treat like #5. |
| 7 | Backup destination writable | If FAIL: backups will not succeed. Check disk space / permissions on the backup folder. |
| 8 | Required secrets present (`LUXOR_DEMO_PASSWORD`, `OPERATOR_API_SESSION_SECRET`, `WHATSAPP_BRIDGE_ADAPTER_SECRET`; `QUO_API_KEY` optional) | ACTION REQUIRED means login or bridge delivery will not work until that variable is set in `.env`. |
| 9 | Python-side vs. Node-side bridge secret match | FAIL means the two `.env` files (repo root and `whatsapp_shadow_bridge/.env`) have different values for what must be the same shared secret — the bridge will get HTTP 401 trying to deliver every WhatsApp observation. Fix by copying the value from one `.env` to the other (a human task — this tool will not do it for you). |
| 10 | WhatsApp observations durability + duplicate IDs | ACTION REQUIRED / FAIL here means the schema migration that added durable WhatsApp storage hasn't been applied — run `python scripts/manage_schema.py migrate`. |
| 11 | Startup scripts point at the current CRM | Flags the known gap where `tools\windows_launcher\` still starts the retired `luxor_desk.py` Streamlit app instead of `operator_api.py`. Use `scripts\start_crm.ps1` directly until that launcher is repointed. |

### Reading the summary

- **PASS** — nothing to do.
- **WARNING** — usually "not running right now," which may be expected
  (e.g. you haven't started the CRM yet). Not counted as a failure.
- **FAIL** / **ACTION REQUIRED** — something needs a human decision or
  action before you'd want to demo or rely on this system. The script
  exits with code `1` if any of these are present, `0` otherwise — safe
  to wire into a scheduled task that just wants a pass/fail signal.

## Backups

See `docs/BACKUP_RESTORE_RUNBOOK.md` for the full procedure. Quick
reference:

```bash
python scripts/backup_database.py --db data/dispatcher.db
python scripts/verify_backup.py "<path printed above>"
```

Backups are written to `..\ai-dispatcher-db-backups\` (a sibling
folder to the repo, so it's never accidentally committed) and the most
recent 30 are kept automatically.

## Starting / stopping the CRM

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_crm.ps1
powershell -ExecutionPolicy Bypass -File scripts\stop_crm.ps1
powershell -ExecutionPolicy Bypass -File scripts\crm_health_check.ps1
```

## Starting the full boss-demo stack (CRM + WhatsApp bridge, real data)

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_boss_demo.ps1
```

Requires an already-paired WhatsApp session; this script will not pair
one on its own. For a fully synthetic, isolated demo instead (no real
data, no live WhatsApp/Quo connection), see `docs/DEMO_RUNBOOK.md`.

## Configuration validation

```bash
python scripts/validate_configuration.py
```

Read-only technician/routing/policy configuration checker — separate
from `system_readiness.py`, which checks infrastructure health, not
business configuration. Run both before onboarding a new customer.

## If something looks wrong

1. Run `python tools/system_readiness.py` first — it's the fastest way
   to narrow down which subsystem is the problem.
2. If it points at the database, stop before making any further
   changes and take a fresh backup of the current (possibly damaged)
   state before attempting any recovery, so nothing is lost either way.
3. If it points at a secret mismatch or missing configuration, fix the
   relevant `.env` file by hand — no script in this repo writes to
   `.env` on your behalf except `operator_api.py`'s own one-time
   session-secret bootstrap on first run (see "Known operational risk"
   below).

## Known operational risk: `.env` auto-provisioning on import

`operator_api.py` auto-generates and appends a new
`OPERATOR_API_SESSION_SECRET` to the real `.env` file the moment it is
imported and does not find that variable already set in the process
environment — regardless of whether the import happened from a normal
CRM start or anything else (a script, a REPL, a test run in an
unusually configured environment). This is intentional for first-run
setup, but it means **any tool that imports `operator_api` must ensure
`OPERATOR_API_SESSION_SECRET` is already set** before doing so, or it
will silently gain a second, later-and-therefore-winning
`OPERATOR_API_SESSION_SECRET=` line in the real `.env`. If you ever see
two such lines, delete the one you don't want to keep by hand — `.env`
is plain text, gitignored, and never touched by this repo's test suite
(a regression test in `tests/test_system_readiness.py` specifically
guards against a repeat of this).
