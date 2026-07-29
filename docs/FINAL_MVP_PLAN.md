# LUXOR LOCKSMITH -- Final Supervised-MVP Completion Plan

Written against commit `dda1da9` (real database-backed `luxor_desk.py`).
This is a planning document, not a status claim -- nothing here should
be read as "production-ready" or "approved."

## Critical path

```
REAL QUO MESSAGE
  -> CORRECT SOURCE IDENTIFICATION
  -> ACKNOWLEDGMENT PREPARED
  -> LUXOR MESSAGE FORMATTED
  -> TECHNICIAN ROUTING RESOLVED
  -> WHATSAPP HANDOFF PREPARED
  -> STATUS UPDATES TRACKED
  -> CLOSING PREPARED
  -> COMPLETE AUDIT HISTORY
```

Every step stays human-supervised. Quo outbound and WhatsApp sending
stay disabled until explicitly approved and enabled by the user in the
real `.env` -- nothing in this plan changes that.

---

## M1 -- Real inbound ingestion

**Status: Operational.**

- `src/dispatcher/quo_client.py` -- GET-only client, structurally
  incapable of `.post()`/`.put()`/`.delete()`/`.patch()` (enforced by a
  test that greps the module source).
- `src/dispatcher/quo_ingest.py` -- polls the four known Quo contacts
  directly (AGN, Lucky, Main Leads, Lucky Closings & Replies) via their
  configured contact IDs, not the slower/less precise conversations
  endpoint. Incoming-only filter excludes outgoing Closings replies from
  the operational queue while still saving them for audit.
- `scripts/background_poller.py` -- manual, human-started process only
  (`python scripts/background_poller.py --loop`). Not a Windows service,
  not a scheduled task, installs nothing. `QUO_POLL_MIN_INTERVAL_SECONDS`
  protects the real API from being polled harder than intended.
- Poll health surfaced in the Dispatch page header (Live/Delayed/
  Disconnected + last successful sync time), reading `quo_poll_state`.

**Acceptance criteria:** met -- `tests/test_quo_client.py`,
`tests/test_quo_ingest.py` pass; GET-only guard passes; poller runs
manually and records `last_checkpoint`/`last_error` in `quo_poll_state`.

## M2 -- Real dispatch queue

**Status: Operational.**

- `luxor_desk_read_model.py` (new, this milestone) is the single shared
  implementation of the live-queue boundary, canonical `(source,
  ticket)` deduplication, and the job-transformation gate -- imported by
  both `luxor_console_pages/dispatch_queue.py` and
  `luxor_desk_pages/dispatch.py`, so there is exactly one tested
  implementation, not two forks.
- `luxor_desk_pages/dispatch.py` shows **every** live job as its own
  selectable queue row, grouped by company when the "All" filter is
  active -- no job is hidden behind a collapsed "Also in..." summary.
- Historical backlog (received before the durable
  `console_activation_state` boundary) is excluded from every live
  counter, never flagged urgent, and stays visible only in a collapsed
  "Previous Backlog" section for audit.

**Acceptance criteria:** met -- `tests/test_luxor_desk_real.py` (26
tests) and `tests/test_speed_console.py` (30 tests) cover boundary
exclusion, dedup, gate correctness, and full-queue visibility.

## M3 -- Source acknowledgment workflow

**Status: Operational.**

- AGN/Lucky: `policy.acknowledgment_required` drives a "K ready --
  sending disabled" caption plus a plain (non-primary) "Mark K sent
  manually" control that only writes to the local `dispatch_tracking`
  table -- never a real send while outbound is disabled.
- Main Leads: "No acknowledgment required", exactly as
  `dispatcher/policy.py` already specifies -- no per-page reimplementation
  of company rules.
- Closings & Replies stream: explicitly excluded from the new-job
  acknowledgment branch (see M7 for its current limitation).
- Approval/live outbound mode is implemented and tested
  (`tests/test_outbound.py`) but `_send_live()` unconditionally raises
  `NotImplementedError` -- structurally incapable of a real send in this
  build regardless of mode.

**Acceptance criteria:** met for the disabled-by-default path (the only
path enabled in the real `.env`).

## M4 -- Luxor message preparation

**Status: Operational.**

- Reuses the existing, unmodified formatter (`dispatcher/pipeline.py` /
  `dispatcher/formatter.py`) via `luxor_ops_shared.cached_full_preview`.
- The Luxor message renders immediately (inside a collapsed-by-default
  `st.expander`, native copy icon via `st.code`) -- never a blank
  template, never gated behind a separate "Generate" step.
- New this milestone: when `result.decision.missing_information` is
  non-empty, the workspace shows a `HUMAN_REVIEW_NEEDED` warning banner
  above the message instead of silently presenting it as ready. This is
  implemented at the application layer (a warning shown alongside the
  existing `(not provided)` formatter output), **not** by changing
  `dispatcher/formatter.py` itself -- that file's `NOT_PROVIDED` constant
  is shared by `app.py`, `luxor_ops.py`, and `luxor_console.py`, and
  every existing formatter test asserts on it; changing it globally
  would be a breaking change to already-approved, already-tested shared
  behavior well outside this milestone's scope.

**Acceptance criteria:** met, with the above documented design choice.

## M5 -- Technician routing and WhatsApp destination

**Status: Operational.**

- `storage.list_eligible_technicians_for()` (existing, unchanged) drives
  the Routing section -- always the full, name-sorted list of eligible
  technicians, never an auto-selected single one.
- Existing routing behavior preserved unchanged and covered by existing
  tests: Greenville service-based split, Savannah multi-candidate
  (Luxor, Tarek, Omar Shata), Atlantic City -> NJ, address-over-market-
  alias precedence, Atlanta structurally unreachable (not in
  `rules/service_areas.yaml`).
- WhatsApp destination shown from each eligible technician's
  `dispatch_group`; when blank, "Open WhatsApp" and "Mark Dispatched"
  are **hidden**, not just disabled, matching the standing requirement
  to never let the system claim a handoff happened through a
  destination that doesn't exist.

**Acceptance criteria:** met.

## M6 -- Active-job updates

**Status: Partially operational -- documented gap, not silently skipped.**

- `luxor_desk_pages/active_jobs.py` reads every real committed active
  job (`jobs.status` in the active set) and shows source, ticket, area,
  service, technician, and time since the last event -- all real,
  nothing fabricated.
- **Not yet built**: an interactive "record technician status -> prepare
  the correct company update" action inside the new `luxor_desk`
  workspace. That capability already exists and is already tested
  (`src/dispatcher/updates.py`'s `map_status_to_reply()`/
  `answer_from_latest_status()`, wired into `app.py`'s Status tab), but
  porting it into `luxor_desk`'s per-job workspace needs the dispatcher-
  typed status text to be entered against a specific existing job by ID
  -- `storage.save_audit_entry()` requires a full `ExtractedJob`, and
  `active_jobs.py` currently only has the flattened `latest_fields_json`
  dict, not a re-hydrated `ExtractedJob`. Building that reconstruction
  correctly (rather than a rushed, under-verified shortcut) is scoped as
  the next unblocked increment, not attempted in this pass.
- **Already working today**: a genuine incoming STATUS/UPDATE_REQUEST
  Quo message that correlates to an existing job (via ticket number)
  already flows through `luxor_desk_pages/dispatch.py`'s normal queue
  and workspace correctly, with a real freshly-extracted `ExtractedJob`
  -- the gap is specifically the dispatcher-typed-status path, which
  today still requires the original `app.py`.

**Acceptance criteria:** partially met -- read visibility is real and
correct; the interactive update-preparation action is a known, disclosed
gap.

## M7 -- Closings and reconciliation

**Status: Partially operational -- documented gap, not silently skipped.**

- `luxor_desk_pages/closings.py` shows only real unsettled closed jobs,
  amount/payment method only when `storage.get_latest_closing_details()`
  actually has a structured record, and the literal empty state "No
  closings awaiting action" when there are none -- never a fabricated
  placeholder.
- **Not yet built**: entering a new closing (amount/payment method/parts)
  from inside `luxor_desk`. That capability already exists, already
  tested, in `app.py`'s Closing tab (`dispatcher/closing.py`'s
  `ClosingInput`/`render_closing()`), which requires the same
  in-session `ExtractedJob` `app.py` already keeps from when a job was
  first processed. Porting this into `luxor_desk` has the same
  reconstruction dependency noted in M6 and is scoped together with it.
- The Dispatch workspace's Closings & Replies branch now explicitly
  tells the dispatcher to use the existing Closings & Payments tool for
  entry until this workspace grows that action, rather than staying
  silent about the gap.

**Acceptance criteria:** partially met -- read visibility and the
never-fabricate-an-amount guarantee are real and tested; closing entry
remains in the original, already-approved `app.py` for now.

## M8 -- Supervised real-job test

**Status: Ready to attempt, not yet performed.** See the exact procedure
in the final report / `docs/EXTERNAL_BLOCKERS.md`. Requires the user's
own login (this assistant does not enter the app's password into a live
browser session under any circumstance) and, for a full end-to-end test
including a real WhatsApp handoff, at least one technician's
`dispatch_group` populated with a real destination string (currently
blank for all 15 -- see External Blockers).

## M9 -- Production-readiness hardening

**Status: Not started; explicitly out of scope for supervised MVP.**
Would include: real secrets management beyond a local `.env`, structured
logging/alerting, a real deployment target (this session's native-
process + Cloudflare Quick Tunnel pattern is a review mechanism, not a
production host), backup automation beyond the manual
`ai-dispatcher-db-backups/` copies made before risky operations, and a
decision on whether/how to eventually enable `QUO_OUTBOUND_MODE=live`.

## M10 -- Optional AI assistance layer

**Status: Documented, disabled, not built.** See "AI assistance
boundary" below. Not required for MVP; the deterministic pipeline
(parser + classifier + decision engine + formatter) is the entire
system today and needs no LLM to function.

---

## "Not required for MVP"

Explicitly out of scope until the supervised MVP itself is validated
with real jobs:

- Any AI/LLM integration (see M10).
- Automatic Quo/WhatsApp sending (`QUO_OUTBOUND_MODE=live`).
- A native iOS app or push notifications.
- Analytics, charts, or decorative dashboards.
- A second database, parser, classifier, decision engine, or
  persistence layer of any kind.
- Windows service / scheduled task installation for the poller.
- Multi-tenant / multi-company support beyond AGN, Lucky, Main Leads,
  Lucky Closings & Replies.

## Real database inventory (read at time of writing, real DB)

| Table | Rows |
|---|---|
| `jobs` | 5 |
| `events` | 6 |
| `technicians` | 15 (all real, 0 with a configured WhatsApp `dispatch_group`) |
| `quo_pending_messages` | 126 |
| `quo_processed_messages` | 216 |
| `dispatch_tracking` | 88 (additive-only: first-seen/prepared/acknowledged/copied/WhatsApp-opened/dispatched timestamps) |
| `quo_outbound_log` | 0 (no outbound attempt has ever been recorded) |
| `console_activation_state` | 1 (live-queue boundary, seeded once) |

No fabricated row has ever been written to this database by any
candidate built this session -- every write path used here is the
original `storage.save_audit_entry()`/`mark_quo_pending_message_actioned()`/
`dispatch_tracking` path, unchanged.
