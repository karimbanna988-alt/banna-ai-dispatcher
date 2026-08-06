# Current Sprint Checkpoint — Fail-Closed Safety + Technician K Tracking

Written at a natural stopping point, not a session-limit cutoff. Supersedes
the previous checkpoint (that sprint's work is preserved in git history;
its incident and reasoning are summarized below where still relevant).

## Starting state

HEAD `1e5c3e9` (end of the prior "technician update / company reply
workflow" sprint). Real DB (`data/dispatcher.db`): 6 jobs, 10 events,
integrity OK — unchanged throughout this sprint, re-verified after
every write-adjacent operation and after both full-suite runs.

## Priority 1 — Fail-closed production database protection (DONE)

The prior sprint disclosed that two demo-seeding calls briefly resolved
to the real database's default path because `OPERATOR_API_DB_PATH` was
unset in the calling shell — net effect was zero (verified), but the
*mechanism* that made that possible (silent fallback) was still live.
This priority closed the mechanism itself, not just the one incident.

**What changed:**
- New `src/dispatcher/db_safety.py` — one shared `resolve_db_path(mode,
  path)` function. `"live"` mode (the sanctioned `scripts/start_crm.ps1`
  path) keeps the existing default-to-real-DB behavior unchanged.
  `"demo"` and `"test"` mode now **require** an explicit path and
  **reject** one that canonically resolves (via `Path.resolve()`, not
  string comparison — proven against relative paths, redundant `.`
  segments, and a real filesystem symlink) to the production DB path.
- `operator_api.py` and `whatsapp_bridge_adapter.py` both route their
  `_db_path()` through this one function. Mode is auto-detected:
  `"pytest" in sys.modules` → `"test"` (so a test that forgets to set
  the env var now fails loudly instead of silently touching the real
  DB — this is the actual fix for the fixture-level version of the
  incident), `LUXOR_DEMO_MODE=true` → `"demo"`, else `"live"`.
- `scripts/seed_command_center_demo_db.py` now force-sets both
  `LUXOR_DEMO_MODE` and `OPERATOR_API_DB_PATH` in its own process
  environment before making any `operator_api` calls — a caller
  forgetting to set the env var in their shell (exactly what happened
  last sprint) can no longer matter, because the script no longer
  trusts the caller's environment for this.
- `conftest.py` gained a session-wide autouse fixture that asserts the
  real database's mtime/size are identical before vs. after the entire
  test run — independent, whole-suite tripwire on top of the per-module
  guard. Ran across a live 1401-test session with zero trips.
- 24 new tests (`tests/test_db_safety.py` plus additions to
  `tests/test_operator_api.py`), including the exact five negative
  cases from the brief (missing demo path, missing test path, relative
  path resolving to prod, absolute prod path, symlink to prod) plus the
  positive cases (valid temp path succeeds, live startup unaffected).

**Commit:** `3b46c05`.

## Priority 2 — Technician K acceptance tracking (DONE)

The audit (prior sprint) found no schema concept anywhere of "the
technician accepted this job" — `dispatch_tracking` tracks prepared/
copied/WhatsApp-opened/dispatched-manually, nothing for the reply. A
bare "K" was only ever flagged for human review, never recorded as a
job fact.

**What was built:**
- `storage.technician_acceptance` — new, additive, job-keyed table
  (deliberately excluded from the blanket schema-upgrade gate, same
  precedent as Shadow Mode's tables, so it never forces "Database
  upgrade required" on unrelated existing functionality). Idempotent
  `mark_technician_accepted`/`mark_technician_declined`, both COALESCE
  "set once" like every other milestone in this codebase, both also
  writing a full audit-trail event (unlike `dispatch_tracking`'s silent
  milestones — acceptance/decline is a fact worth showing in history).
  Never touches `jobs.status` — orthogonal, same pattern as
  `reconciliation_state`.
- `src/dispatcher/technician_ack.py` — pure, deterministic matching. No
  guessing: a candidate WhatsApp observation is classified EXACT (group
  + ticket both match), STRONG (group matches, technician has exactly
  one job waiting), AMBIGUOUS (group matches, multiple jobs waiting,
  no ticket to disambiguate), or UNMATCHED (anything else) — from
  evidence already in the schema (dispatch-group name, extracted
  ticket, an exact acknowledgement/decline vocabulary reusing the same
  words `whatsapp_bridge_adapter.py` already treats specially).
- `DispatcherService.confirm_technician_accepted`/`confirm_technician_declined`,
  two new routes, and a `technician_k` field on `/api/active`'s per-job
  DTO (computed fresh, never persisted speculatively).
- A "Technician acceptance — waiting on K" panel on the Active job
  detail: shows each candidate's category and full reasoning, with
  explicit Confirm-accepted / Confirm-declined buttons. Disappears once
  either action is confirmed, or once a real technician status update
  supersedes the question.
- 20 new tests across `tests/test_technician_ack.py` (pure matching),
  `tests/test_application_service.py` (service-layer idempotency/audit),
  `tests/test_operator_api.py` (route/DTO integration, including an
  AMBIGUOUS case with two jobs genuinely waiting on the same technician).
- **Verified end-to-end in a live browser** against the isolated demo:
  seeded a bare "K" WhatsApp observation, confirmed the DTO correctly
  classified it AMBIGUOUS (two demo jobs were waiting on the same
  technician), confirmed accepting it via the real UI action persisted
  and the panel updated to show "Technician confirmed accepted."

**Commits:** `7d7cba1`, `8873827`.

## Full-suite verification (both priorities)

Ran twice (once per priority, from a clean background process): 1378
tests then 1401 tests, both green except one pre-existing, reproducible
flake (`test_post_with_valid_csrf_token_reaches_the_route` — a socket
`recv_into` timeout during teardown, confirmed to pass on isolated
retry both times it appeared; unrelated to any change this sprint or
last — it also appeared in the prior sprint's report). Real DB
integrity/FK checks clean and job/event counts unchanged after both runs.

## What was deliberately not attempted, and why

Priorities 3-7 from this sprint's brief were not started:

- **P3 — Unified next-action projection.** A real per-job workflow
  projection already exists and now covers technician acceptance too
  (`_active_next_action_label` plus the `technician_k`/
  `company_update_owed` DTOs added across the last two sprints). What
  the brief additionally wants is *every* CRM screen (New Jobs,
  Closings, not just Active) consuming one single projection function
  instead of each screen computing its own next-action signal. That's
  a real refactor touching several route handlers and their read
  models, not a wiring gap — doing it hastily risks silently changing
  behavior on screens that already work and are tested. Scoped, not
  started.
- **P4 — Action Center.** Per the audit, Command Center already serves
  this structural role; building a literal second screen without first
  finishing P3's unified projection would mean the new screen either
  duplicates each source screen's logic a third time, or is built on
  the same per-screen computation P3 is meant to eliminate. Sequencing
  matters here — P3 before P4.
- **P5 — Five synthetic demo scenarios**, **P6 — lifecycle stepper
  UI**, **P7 — sales/commercial doc rewrite.** Each is a real, multi-hour
  deliverable in its own right. Not started.

## Exact next steps

1. **P3 first, then P4.** Design one projection function (module-level,
   pure, takes a job + its related state, returns the fixed label set
   from the brief) that `route_new_jobs`, `route_active`, and
   `route_closings` all call instead of each computing next-action
   independently. Migrate one screen at a time, re-running that
   screen's existing tests after each migration before moving to the
   next, so a regression is caught immediately and attributable.
2. **Then Action Center (P4)**, once every screen the brief lists (new
   jobs, company K owed, ready-for-dispatch, waiting-for-K, updates to
   review, company updates owed, closings to review/send, human
   review) has one real, projection-backed data source to read from.
3. **Demo scenarios (P5)** — Happy Path first (it exercises P1-P4's
   work end-to-end and makes a good regression fixture for future
   sprints), then Human Review and Duplicate/Correction if time allows.
4. **Lifecycle stepper (P6)** and **sales/doc updates (P7)** — lowest
   priority; cosmetic/narrative, not functional gaps.

## Safety confirmations (re-verified at checkpoint time)

- Real `data/dispatcher.db`: `PRAGMA integrity_check` = `ok`, zero FK
  violations, 6 jobs / 10 events — identical to both this sprint's and
  the prior sprint's starting count.
- `.env`: no diff; still exactly 2 `OPERATOR_API_SESSION_SECRET` lines;
  no secret rotation, printing, or cleanup performed (none was needed
  or attempted, per the brief's explicit instruction).
- No Quo message sent (client remains structurally GET-only, unchanged).
- No WhatsApp message sent (bridge remains structurally receive-only,
  unchanged; this sprint's new `technician_ack` module only *reads*
  observations already captured by the existing receive-only path and
  never sends anything).
- The live WhatsApp bridge was never started or re-paired — every demo
  server this sprint used ran with `--no-bridge-adapter`.
- All new tests use `tmp_path`/`api_env`-backed throwaway databases —
  zero real network calls, zero real-DB writes (independently confirmed
  by the new session-wide tripwire fixture in `conftest.py`).

## Demo

Isolated demo running at `http://127.0.0.1:8517` against a freshly
seeded throwaway database in the session scratchpad (never
`data/dispatcher.db`), seeded via the now-fail-closed seed script.
Login password is a per-process override, never the real
`LUXOR_DEMO_PASSWORD` — not written to any file, only passed as an
environment variable to the running process; see the final report if
you want the exact value to click through it yourself.
