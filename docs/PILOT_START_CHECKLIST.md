# Pilot Start Checklist

Short and operational — for the person actually starting the first real
pilot shift, not a policy document. If something here fails, stop and
use the existing manual dispatch process rather than pushing through.

## BEFORE START

- [ ] **Backup verified.** Run a fresh backup and confirm it independently:
  ```
  python scripts/backup_database.py --db data/dispatcher.db
  python scripts/verify_backup.py <the .bak path just printed>
  ```
  Both must report `integrity_check: ok` and `0` foreign-key violations.
- [ ] **Readiness command run and reviewed line by line:**
  ```
  python tools/system_readiness.py
  ```
  Every `FAIL` must be resolved. Every `ACTION REQUIRED`/`WARNING`
  tagged `(CONFIGURATION)` must be a conscious, acknowledged decision —
  not silently ignored. Anything tagged `(EXTERNAL INTEGRATION)` is
  expected and not a blocker (see `docs/CURRENT_PRODUCT_STATUS.md`).
- [ ] **DB integrity confirmed** — covered by the readiness command
  above (`SQLite integrity_check` / `SQLite foreign_key_check`, both
  must show `PASS`).
- [ ] **Correct startup command used:** `scripts\start_crm.ps1` (starts
  `operator_api.py`). Do **not** use `tools/windows_launcher/` — it
  still points at the retired `luxor_desk.py` interface (a known,
  documented gap; the readiness tool flags this every run).
- [ ] **Mapped technician/area selected.** Confirm the specific
  technician(s) working this shift each have both a `dispatch_group`
  and `closing_group` configured (Technicians screen, or the readiness
  tool's "Technician mappings" line). Do not dispatch to a technician
  missing either — the operator won't have a destination to copy the
  message to.

## FIRST REAL JOB

Walk through one real incoming job end to end, verifying each step
before moving to the next:

- [ ] **Verify parser** — the extracted fields on the Review screen
  match what the actual incoming message said.
- [ ] **Verify ticket** — the ticket/ref number shown matches the
  source (Quo/company) system.
- [ ] **Verify service/city** — service type and city are correct, not
  a parser misread.
- [ ] **Verify company K** — if the source requires acknowledgment,
  confirm the K step appears and the copied text is correct before
  marking it sent.
- [ ] **Verify technician recommendation** — the suggested/eligible
  technician(s) are actually right for this job's area and skill —
  don't accept a recommendation on faith the first time.
- [ ] **Verify destination** — the WhatsApp group/destination shown for
  dispatch is the correct one for that technician.
- [ ] **Manually send outbound messages** — copy and actually send the
  prepared text yourself in Quo/WhatsApp. Nothing in this product sends
  on its own; if a message doesn't need to leave this device, something
  is wrong.
- [ ] **Verify technician K matching** — when the technician replies,
  confirm the system correctly attributes that reply to this job before
  clicking "Confirm technician accepted."
- [ ] **Verify update** — a technician status update (Going/ETA/etc.)
  is recorded and the resulting company reply reads correctly before
  sending it.
- [ ] **Verify closing** — the final amount and payment method entered
  match exactly what the technician actually reported — never a guess
  or a rounded figure.
- [ ] **Verify audit** — open the job's history/audit trail and confirm
  every step you just did is recorded there.

## STOP / FALLBACK CONDITIONS

If any of the following happen, **stop using this system for that job**,
fall back to the existing manual dispatch workflow, and record what
happened (ticket, what you saw, what you expected):

- Incorrect technician suggested or assigned.
- Incorrect destination (wrong WhatsApp group) shown.
- A duplicate job appears for a ticket you know was only sent once.
- Wrong source attributed to a job.
- A technician-K match you're not confident about — if in doubt, treat
  it as unmatched and confirm manually with the technician directly.
- A closing amount that doesn't match what the technician actually told
  you.
- Any unexpected write/network behavior — anything that looks like the
  system sent something on its own, wrote to a database file you didn't
  expect, or made a network call you didn't initiate. This should never
  happen (Quo is structurally read-only, WhatsApp sending doesn't
  exist in this build) — if it appears to, stop immediately and report
  it before continuing.

When in doubt, the manual process is always the safe fallback. Nothing
in this product requires you to trust an automated suggestion you
haven't personally verified.
