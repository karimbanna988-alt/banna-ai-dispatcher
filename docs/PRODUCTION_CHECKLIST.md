# Production Checklist

Run through this before calling a deployment "live" for a paying pilot
customer. Each item names the exact command or document to check
against — nothing here is a vague aspiration.

## Secrets

- [ ] `LUXOR_DEMO_PASSWORD` is set to a real, non-default password (not
      left as a placeholder from `.env.example`).
- [ ] `OPERATOR_API_SESSION_SECRET` is set (auto-generated on first run
      if missing — confirm it exists, don't regenerate an established one).
- [ ] If WhatsApp is enabled: `WHATSAPP_BRIDGE_ADAPTER_SECRET` (repo
      root `.env`) and `WHATSAPP_SHADOW_ADAPTER_SECRET`
      (`whatsapp_shadow_bridge/.env`) match — verify with
      `python tools/system_readiness.py` (the "Bridge secret
      fingerprint match" check), never by comparing the raw values by
      eye.
- [ ] If Quo is enabled: `QUO_API_KEY` is the customer's real key, not
      a placeholder or a different customer's key.
- [ ] No secret value appears in any committed file, screenshot, log,
      or support ticket. `git status --ignored -- .env` should show
      `.env` as ignored, never as tracked.

## Backups tested

- [ ] `python scripts/backup_database.py --db data\dispatcher.db`
      completes with `integrity_check: ok` and `foreign_key_violations: 0`.
- [ ] `python scripts/verify_backup.py <the backup path just printed>`
      reports `VERIFY: PASS`.

## Restore tested

- [ ] `python scripts/restore_database.py --source <a real backup>
      --target <a scratch path>` succeeds and reports
      `RESTORE VERIFY: PASS`. (This restores to a **temporary** path —
      it structurally cannot overwrite the live database. Never attempt
      to make it do so; a real restore-over-production is a deliberate
      manual procedure, not a scripted one — see
      `docs/BACKUP_RESTORE_RUNBOOK.md`.)

## Admin password changed

- [ ] `LUXOR_DEMO_PASSWORD` is not the same value used during
      development/demo, if those environments are exposed differently
      (e.g. a demo instance someone else might have access to).

## Synthetic data excluded

- [ ] `data\dispatcher.db` contains zero rows with `demo`/`fake`/`test`
      markers from `scripts/seed_command_center_demo_db.py` (that
      script structurally refuses to target the live database — see
      `tests/test_demo_isolation.py` — but confirm no one worked around
      that by hand).
- [ ] The demo (`scripts/start_demo.ps1`) was never pointed at the real
      database — it uses `.runtime\demo\demo.db` exclusively by
      construction.

## Quo remains GET-only

- [ ] `src/dispatcher/quo_client.py` contains no `.post(`, `.put(`,
      `.delete(`, or `.patch(` call — enforced by a structural test in
      `tests/test_quo_client.py`. Re-run that test after any change to
      this file, ever.

## WhatsApp remains receive-only

- [ ] No send capability exists anywhere in `whatsapp_shadow_bridge/` —
      enforced by `whatsapp_shadow_bridge/test/no-send-static-check.test.js`.
      Re-run after any change to that codebase.

## Allowlist reviewed

- [ ] If WhatsApp is enabled, the group allowlist
      (`whatsapp_shadow_bridge`'s local admin page, `/allowlist`)
      contains only the groups this customer explicitly wants captured
      — `python tools/system_readiness.py` reports the count (never the
      names) as a sanity check; review the actual names locally.

## Ports and firewall checked

- [ ] Port 8512 (CRM) is reachable only as intended — locally, or
      through an explicitly configured tunnel (e.g. `cloudflared`), not
      accidentally bound to `0.0.0.0` and exposed to the open internet
      without a tunnel's own access control.
- [ ] Ports 8513 (Python bridge adapter) and 8514 (Node WhatsApp
      bridge admin) are loopback-only by design — confirm they are
      never proxied through any tunnel (see the code comments in
      `whatsapp_bridge_adapter.py` and `whatsapp_shadow_bridge/src/healthServer.js`).

## Database integrity checked

- [ ] `python tools/system_readiness.py` reports PASS for both SQLite
      integrity_check and foreign_key_check.
- [ ] `python scripts/manage_schema.py check` reports the schema as up
      to date.

## Pilot group approved

- [ ] The customer has explicitly approved which WhatsApp group(s),
      Quo contacts, and job sources are in scope for the pilot — no
      group/source is captured or processed without this sign-off.

## Rollback plan

- [ ] A recent, verified backup exists (see above) before any schema
      migration or major change is applied to this customer's database.
- [ ] `scripts/manage_schema.py migrate` always backs up automatically
      first — confirm this is understood, not disabled.
- [ ] Know how to stop the CRM (`scripts\stop_crm.ps1`) and, if
      WhatsApp is enabled, the bridge, without losing any captured but
      unprocessed WhatsApp observation (durable in SQLite as of this
      sprint — confirmed by `tools/system_readiness.py`'s durability
      check).

## Support contact

- [ ] The customer knows who to contact and how, if
      `tools/system_readiness.py` reports a problem they can't resolve
      themselves using `docs/OPERATIONS_RUNBOOK.md`.

## Known limitations acknowledged

- [ ] The customer has read `docs/KNOWN_LIMITATIONS.md` and explicitly
      accepted, in writing, the unofficial-WhatsApp-dependency risk and
      the local-deployment model — both are real, ongoing, and not
      something a future code change resolves on its own.

## Operator Speed Implementation Sprint — relevant to this checklist

- [ ] If this pilot's session was live before this sprint, have the
      operator do one full logout/login after upgrading — a real bug
      (fixed this sprint, `docs/SECURITY_AUDIT.md` finding #5) meant a
      session that was already open before the fix would not itself
      pick up the corrected CSRF-token-on-reload behavior mid-session;
      a fresh login clears this cleanly, same as any other code
      upgrade to an already-running server.
- [ ] `route_active`/`route_action_center` now run measurably faster at
      volume (see `docs/WORKFLOW_SPEED_AUDIT.md`); no new infrastructure
      (cache, queue, etc.) was added, so no new operational dependency
      needs to be understood or monitored for this pilot.
