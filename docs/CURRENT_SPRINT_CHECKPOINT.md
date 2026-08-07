# Current Sprint Checkpoint — Demo Scenarios + Lifecycle Stepper + Sales Script

Written at a natural stopping point, not a session-limit cutoff. Supersedes
the previous checkpoint (that sprint's work — the pending-message
projection fix, the QUO_API_KEY test-isolation fix — is preserved in
git history).

## Starting state

HEAD `ce8ee44` (end of the "workflow coverage gap + QUO_API_KEY fix"
sprint). Real DB (`data/dispatcher.db`): 6 jobs, 10 events, integrity
OK — unchanged throughout this sprint.

## Priority 1 — Demo scenarios A, B, F (DONE)

`scripts/seed_demo_scenarios.py` — three named scenarios, each driving
a job through real `operator_api.route_*`/`DispatcherService` calls
only (never hand-built rows), reusable both as a standalone script and
imported directly from tests:

- **Scenario A (happy path):** AGN job → reviewed → company K
  mock-sent → technician assigned → dispatch mock-sent → technician K
  accepted → status update recorded → company update mock-sent →
  closing recorded → company closing mock-sent → `complete_job()`.
  Ends `Closed` + `Settled` with a 10-event audit trail, and (verified
  by test) appears in **zero** Action Center sections once done.
- **Scenario B (human review):** zero eligible technicians for the
  job's area/service — proves `derive_pending_message_next_action()`
  reaches `HUMAN_REVIEW_NEEDED` without ever fabricating an assignment
  (a test asserts the technicians table stays empty).
- **Scenario F (technician decline):** a captured "can't" WhatsApp
  reply confirmed as a decline — proves `technician_acceptance` records
  `declined_at` with `accepted_at` staying `None` (no false acceptance),
  and the job's `next_action` moves to `HUMAN_REVIEW_NEEDED`.

One real bug found and fixed while building these: running multiple
scenarios back-to-back in one process could land inside
`luxor_ops_shared`'s 5-second pending-queue cache TTL, causing a
freshly-inserted message to be invisible to the very next call. Fixed
by calling `shared.clear_pending_caches()` right after each scenario's
message insert — the same pattern `route_confirm_dispatch` already uses
after a successful dispatch, just applied proactively here too.

**Tests:** 11 in `tests/test_demo_scenarios.py`. **Commit:** `70cb6b7`.

Scenarios C (duplicate/correction), D (technician restriction), and E
(incomplete closing) were **not built** — E is already covered
implicitly by existing `REVIEW_CLOSING` tests
(`tests/test_workflow_projection.py`), and C/D didn't reach the top of
the priority queue this round. See "exact next steps" below.

## Priority 2 — Lifecycle stepper (DONE)

`dispatcher.workflow_projection.derive_lifecycle_steps()` — a third
pure function alongside the two `next_action` functions, mapping the
same already-gathered facts into the required 11-step lifecycle
(Received → Reviewed → Company acknowledged → Technician assigned →
Sent to technician → Technician accepted → Work/update → Company
updated → Closing received → Closing sent → Complete), each step
`completed`/`current`/`blocked`/`pending`. At most one step is ever
`current` or `blocked` (enforced by a dedicated test); a dead-terminal
job (Cancelled/Rejected) shows no `current` step at all.

Wired into both required surfaces: `route_active`'s per-job `workflow`
dict gained a `steps` array (+ `delivery_mode`, always `"MANUAL COPY"`
in this build — the other two brief-specified values don't apply until
a real outbound connector or a distinct demo-auto-confirm path exists,
neither of which does), and `_new_job_dto` (New Jobs / incoming review)
gained the same. `lifecycleStepperHtml()` in the frontend only renders
what the backend already decided — no independent JS calculation.

**Verified live** at 1440×900 and 390×844 against a real Scenario F job:
steps 1–5 completed, step 6 "Technician accepted" shown blocked in red
with the decline reason, steps 7–11 pending — exact match to the
pure-function's own tests. 1920×1080 was not separately screenshotted
(the stepper is a compact flex-wrap block inside the same
already-verified max-width layout; low risk, just not captured).

**Tests:** 24 in `tests/test_workflow_projection.py`. **Commit:** `334f318`.

## Priority 4 — Sales demo script (DONE)

`docs/SALES_DEMO_SCRIPT.md` rewritten for the full current workflow —
14 steps (login → Command Center → Action Center → new job → AI
extraction → deterministic validation → company K → technician
recommendation → dispatch → technician K → technician update → company
update → closing → completion/KPI/audit), each with exact
click/appears/say/proves. "What NOT to claim" preserved and extended
(no outbound step is automatic; Quo remains structurally GET-only).

**Commit:** `93e8df4`.

## An unplanned but valuable outcome

The full suite ran **fully clean for the first time this session**:
1473 tests, 0 failures (previous rounds always had 1-2 socket-teardown
flakes even after the QUO_API_KEY fix). Plausibly the QUO_API_KEY fix
from the prior sprint removed enough wall-clock time/contention from
the suite that the separate `ConnectionAbortedError` teardown flake
simply didn't get a window to occur this run — not a claim that it's
fixed, just an honest observation. Real DB integrity/FK checks clean,
job/event counts unchanged (6 jobs, 10 events) before and after.

## What was deliberately not attempted, and why

Priorities 3, 5, and 6 (Action Center visual-overflow demo data,
commercial doc updates beyond the sales script, and the full
screenshot-artifact sweep) were not attempted this round:

- **P3 — Action Center overflow/5-item-cap visual demo.** The cap and
  `total` count are already tested at the API level
  (`test_action_center_sections_cap_at_five_items`, prior sprint); a
  dedicated *visual* screenshot proving "View all" specifically was not
  captured this round.
- **P5 — Remaining commercial docs** (`docs/COMMERCIAL_READINESS.md`,
  `docs/PRODUCTION_CHECKLIST.md`, `docs/KNOWN_LIMITATIONS.md`). Not
  updated this round — the sales script was prioritized as the more
  time-sensitive, externally-facing document.
- **P6 — Screenshot artifacts saved to `artifacts/final_workflow_demo/`.**
  As disclosed in every prior sprint this session: no tool in this
  environment can persist captured screenshot bytes to disk. Every
  screenshot claimed above was genuinely captured and inspected live in
  this conversation; none exist as files on disk.

## Exact next steps

1. **Scenarios C and D** — Duplicate/Correction and Technician
   Restriction are the two still-missing named scenarios. Both are
   small deltas on `scripts/seed_demo_scenarios.py`'s existing
   patterns: C reuses the same message twice with `save_pending_and_mark_processed`
   (already idempotent by `message_id`) plus a corrected second
   message; D seeds two technicians where the first is deliberately
   ineligible (e.g. wrong area) so `resolve_job_routing` rejects them
   with a visible reason.
2. **Remaining commercial docs (P5)** — `COMMERCIAL_READINESS.md`,
   `PRODUCTION_CHECKLIST.md`, `KNOWN_LIMITATIONS.md` should be
   refreshed to mention Action Center and the lifecycle stepper by
   name; the READY/PILOT READY/BLOCKED categorization from this
   sprint's brief is still accurate and can be transcribed directly.
3. **1920×1080 stepper verification** — quick, just not done yet.

## Safety confirmations (re-verified at checkpoint time)

- Real `data/dispatcher.db`: `PRAGMA integrity_check` = `ok`, zero FK
  violations, 6 jobs / 10 events — unchanged.
- `.env`: untouched, not inspected or modified this sprint.
- No Quo or WhatsApp message sent — no Quo-adjacent or WhatsApp-adjacent
  file was touched this sprint (the new scenarios use
  `whatsapp_bridge_adapter.add_observation()`, the same receive-only,
  already-audited entry point every prior sprint's K-tracking work used).
- The live WhatsApp bridge was never started or re-paired — every demo
  server this sprint used ran with `--no-bridge-adapter`.
- All new tests use `tmp_path`-backed throwaway databases — zero real
  network calls, zero real-DB writes (independently confirmed by the
  session-wide tripwire fixture in `conftest.py`).

## Demo

Isolated demo running at `http://127.0.0.1:8517` against a freshly
seeded throwaway database in the session scratchpad (never
`data/dispatcher.db`), seeded with both the standard Command Center
fixtures and all three demo scenarios. Login password is a per-process
override, never the real `LUXOR_DEMO_PASSWORD`.
