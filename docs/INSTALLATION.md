# Installation

Written for a technically competent person setting this up for the
first time — not necessarily the eventual dispatcher, but not
requiring deep software engineering background either.

## Supported versions

- **Python**: 3.12+ (developed and tested against 3.12.10). Windows.
- **Node.js**: 20+ (required only for the WhatsApp bridge —
  `whatsapp_shadow_bridge/package.json`'s `engines.node`). Skip if you
  are not enabling WhatsApp capture.
- **OS**: Windows (all scripts in `scripts/` are PowerShell). The core
  Python application itself is not Windows-specific, but this repo's
  tooling has not been adapted for macOS/Linux.

## 1. Get the code

Clone or copy this repository to a permanent location — not a temp
folder, since `data/dispatcher.db` and backups live relative to it.

## 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

Installs `streamlit`, `pyyaml`, `pytest`, `requests`, `python-dotenv`.

## 3. Install the WhatsApp bridge dependencies (optional)

Only if you're enabling WhatsApp capture:

```bash
cd whatsapp_shadow_bridge
npm install
cd ..
```

## 4. Configure secrets

```bash
copy .env.example .env
```

Then edit `.env` by hand and set at minimum:

| Variable | Required for | Notes |
|---|---|---|
| `LUXOR_DEMO_PASSWORD` | CRM login | Pick a real password. This is the single shared dispatcher password — see `docs/KNOWN_LIMITATIONS.md`. |
| `OPERATOR_API_SESSION_SECRET` | Session signing | Auto-generated and appended to `.env` on first run of `operator_api.py` if missing — you can also set it yourself. **Never share this value or commit it.** |
| `WHATSAPP_BRIDGE_ADAPTER_SECRET` | WhatsApp bridge delivery | Only needed if enabling WhatsApp. Must match `WHATSAPP_SHADOW_ADAPTER_SECRET` in `whatsapp_shadow_bridge/.env` exactly — `tools/system_readiness.py` verifies this without ever printing either value. |
| `QUO_API_KEY` | Quo polling | Only needed if enabling Quo. From your Quo workspace's Settings → API (requires Owner/Admin). |

Never commit `.env` — it's gitignored (`.gitignore` line 7). Never
include a real secret value in a screenshot, log, or support message.

## 5. Create the database

The database is created automatically the first time the application
connects to it — no separate "create database" step. To verify it was
created correctly:

```bash
python scripts/manage_schema.py check
```

Should report the schema as up to date.

## 6. Start the CRM

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_crm.ps1
```

Opens on `http://127.0.0.1:8512`. **Do not** use
`tools\windows_launcher\Start Dispatcher.bat` — it currently starts a
retired interface, not this one (a known, disclosed gap; see
`docs/COMMERCIAL_READINESS.md` category 10). Use `start_crm.ps1`
directly until that's fixed.

## 7. Verify it's healthy

```bash
python tools/system_readiness.py
```

Read-only, safe to run any time. See `docs/OPERATIONS_RUNBOOK.md` for
what each check means.

## 8. (Optional) Pair and start the WhatsApp bridge

Requires physical access to scan a QR code once:

```bash
powershell -ExecutionPolicy Bypass -File whatsapp_shadow_bridge\scripts\start_pairing.ps1
```

After pairing once, subsequent starts don't need the QR code again:

```bash
powershell -ExecutionPolicy Bypass -File scripts\start_boss_demo.ps1
```

(Despite the name, this starts the real CRM + real paired WhatsApp
bridge together — see that script's own docstring.)

## 9. Shut down

```bash
powershell -ExecutionPolicy Bypass -File scripts\stop_crm.ps1
```

For the WhatsApp bridge: `whatsapp_shadow_bridge\scripts\stop.ps1`.

## 10. Back up before you rely on this

```bash
python scripts/backup_database.py --db data\dispatcher.db
```

See `docs/BACKUP_RESTORE_RUNBOOK.md` for the full procedure, including
verification and restore.

## A note on screenshots and logs

Never include `.env` contents, a session cookie value, a `QUO_API_KEY`,
or any `WHATSAPP_*_SECRET` value in a screenshot, bug report, or support
request. `tools/system_readiness.py`'s output is always safe to share —
it never prints a secret value, only presence and a one-way fingerprint.
