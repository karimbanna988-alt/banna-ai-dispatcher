# Current Sprint Checkpoint — Unified Workflow Projection + Action Center

Written at a natural stopping point, not a session-limit cutoff. Supersedes
the previous checkpoint (that sprint's work — fail-closed DB protection,
technician K acceptance tracking — is preserved in git history and
unmodified this sprint).

## Starting state

HEAD `efe4580` (end of the prior "fail-closed safety + K tracking"
sprint). Real DB (`data/dispatcher.db`): 6 jobs, 10 events, integrity
OK — unchanged throughout this sprint, re-verified after the full-suite
run.

Note: the Claude Code process was interrupted and restarted partway
through the *previous* sprint's wrap-up. No work was lost — everything
had already landed in commits `3b46c05`, `7d7cba1`, `8873827`, `efe4580`
before the interruption; only one already-orphaned background research
task (from an even earlier phase) failed to complete, which is
harmless and unrelated to any tracked work.

## Priority 1 — Unified workflow projection (DONE)

**What was built:** `src/dispatcher/workflow_projection.py` —
`derive_next_action(ctx)`, one pure function, no database access, that
returns the fixed 11-label `next_action` vocabulary the spec requires,
via an explicit, fully-documented precedence chain:

1. Human review blockers (Handle Manually status, a Custom technician
   update, a technician decline, an all-ambiguous K match, an
   unsupported closing template) — checked first, regardless of stage.
2. `REVIEW_NEW_JOB` — status == Needs Review.
3. `ASSIGN_TECHNICIAN` — Approved, no technician yet.
4. `WAIT_TECHNICIAN_K` — Approved, technician assigned, no status
   update yet (covers both "waiting on the K reply" and, once K is
   accepted, "waiting on the first real status update" — the fixed
   vocabulary has no separate label for the latter; documented in the
   module).
5. `REVIEW_TECHNICIAN_UPDATE` — technician reported "Completed" but the
   closing hasn't been recorded yet.
6. `UPDATE_COMPANY` — a technician update hasn't been answered to the
   company yet.
7./8. `REVIEW_CLOSING` / `SEND_CLOSING_TO_COMPANY` — Closed with
   missing fields, or ready-but-not-sent.
9. `COMPLETE` — closing sent, or genuinely terminal (Closed+Settled,
   Cancelled, Rejected).

Two vocabulary values (`ACK_COMPANY_K`, `SEND_JOB_TO_TECHNICIAN`) are
documented as **structurally unreachable** for an already-committed job
in this architecture: acknowledgment happens against a *pending
message* before the job exists, and dispatch (`confirm_whatsapp_dispatch`)
**is** the same action that creates the job row. Both stay in
`NEXT_ACTIONS` because the vocabulary is shared with the pending-message
review path (New Jobs), which this function's "committed jobs only"
contract doesn't cover — the Action Center's "new jobs to review" and
"company K owed" sections source from that separate path instead (see
below).

**Tests:** 24 in `tests/test_workflow_projection.py` — one (or more) per
branch, explicit precedence-ordering tests (e.g. Handle Manually
outranks everything else), and a test pinning that the two unreachable
values are never actually returned across a full sweep of status ×
technician × update-type × closing combinations.

**Commit:** `1fc412c`.

## Priority 2 — Action Center (DONE)

**What was built:**
- `operator_api.py::route_active` now also returns a `workflow` field
  per job (`next_action`/`blocking_reason`/`human_review_needed`/
  `terminal`) alongside the existing, unchanged, still-tested
  human-readable `next_action` label — additive, not a replacement of a
  working screen's existing contract.
- New `GET /api/action_center` — sweeps **every** committed job
  (`shared.fetch_jobs`, unfiltered) through the projection, groups by
  `next_action` into 7 of the 9 required sections; the remaining 2
  ("new jobs to review", "company K owed") plus a bonus "ready for
  technician dispatch" section source from the existing pending-message
  DTO (`_new_job_dto`, already computes `needs_review`/`k_required`/
  `k_sent`/`dispatched` — no new query). Every section capped at 5 items
  with an honest `total` count for the overflow.
- New "Action Center" screen in `operator_ui_v2_connected` (desktop
  sidebar, top-level; mobile Settings menu — the mobile bottom nav's 5
  slots were already full, so Action Center rides the same "less
  frequent, still one tap away" pattern as Performance/Technicians/
  Learning Review there). Each item has one "Open" action that
  navigates to its source screen and, for job items opening into
  Active, auto-selects the job. Command Center's Attention Now "View
  all" button now deep-links to Action Center instead of the generic
  Jobs list (the brief's explicit deep-link requirement).
- **Verified live** in the browser against a freshly seeded isolated
  demo: all 9 sections render with genuinely populated, correctly
  categorized data (including a real edge case the projection caught
  correctly — a Main Leads job with a Closed status landed in "Human
  review required" because Main Leads has no closing template, exactly
  matching `_closing_dto`'s existing `supported=False` logic); "Open"
  navigation confirmed working end-to-end.

**Tests:** 15 in `tests/test_operator_api.py` — section presence,
per-section correctness (assign-technician, waiting-on-K, company-
update-owed, human-review via Handle Manually), the 5-item cap with
correct `total`, a no-PII guard (no phone/address anywhere in the
response), and a structural guard proving the route never references
`quo_adapter`/`whatsapp_adapter`.

**Commit:** `016a06e`.

## Full-suite verification

1434 tests, 1432 passed. The 2 failures were both
`test_post_without_csrf_token_rejected` / `test_post_with_valid_csrf_token_reaches_the_route`
— a pre-existing, reproducible `recv_into` socket timeout in the
`live_server` fixture's teardown path, confirmed to pass on isolated
retry (same flake class documented in the prior two sprints' reports;
unrelated to any change this sprint). Real DB integrity/FK checks clean,
job/event counts unchanged (6 jobs, 10 events) before and after.

## What was deliberately not attempted, and why

Priorities 3-5 from this sprint's brief were not started:

- **P3 — Five synthetic end-to-end demo scenarios.** Each scenario
  (Happy Path, Human Review, Duplicate/Correction, Technician
  Exception, Incomplete Closing) is a real, multi-step fixture-building
  exercise in its own right, not a wiring gap — building all 5
  faithfully (through real service paths, not hand-built rows, per the
  brief's own requirement) is comparable in scope to this sprint's two
  priorities combined. Not started.
- **P4 — Lifecycle stepper UI.** A genuine new UI component (10-step
  visual, 3 states per step, 2 viewports to verify) for job detail.
  Not started.
- **P5 — Sales/commercial doc rewrite.** `docs/SALES_DEMO_SCRIPT.md`,
  `docs/COMMERCIAL_READINESS.md`, `docs/PRODUCTION_CHECKLIST.md` all
  need updates reflecting the Action Center's existence and the
  workflow projection now being the CRM's actual backbone. Not started
  (this checkpoint + `docs/END_TO_END_WORKFLOW_AUDIT.md`, both current,
  partially substitute in the interim).

## Exact next steps

1. **P3 first** (Happy Path scenario alone is the highest-value single
   piece — it exercises P1+P2's work end-to-end and becomes a real
   regression fixture other sprints can build on). Build it as a
   `scripts/`-style seed script the same way `seed_command_center_demo_db.py`
   already does — real route/service calls, never hand-built rows —
   ending with a job that's genuinely `COMPLETE` via the real
   `complete_job()` action, and confirm the Action Center shows it
   correctly disappearing from every section along the way.
2. **Then Human Review and Duplicate/Correction scenarios** if time
   allows — both are small deltas on the Happy Path seed script.
3. **Lifecycle stepper (P4)** — a read-only rendering of the same
   `workflow` projection field already on Active's job DTO (the data
   this needs already exists; this is purely a frontend rendering task
   now, not a backend one).
4. **Sales/doc updates (P5)** — lowest priority; narrative, not
   functional.
5. Consider extending `route_closings`' DTO with the same `workflow`
   field `route_active` now has, for full screen-by-screen consistency
   — noted as a nice-to-have, not blocking, since Action Center already
   gives closings-stage jobs a single correct source of truth
   independent of whether the Closings screen itself has been migrated.

## Safety confirmations (re-verified at checkpoint time)

- Real `data/dispatcher.db`: `PRAGMA integrity_check` = `ok`, zero FK
  violations, 6 jobs / 10 events — identical to every prior sprint's
  starting count this session.
- `.env`: untouched, not inspected or modified this sprint.
- No Quo message sent (client remains structurally GET-only, unchanged
  this sprint — no Quo-adjacent file was touched).
- No WhatsApp message sent (bridge remains structurally receive-only,
  unchanged this sprint — no WhatsApp-adjacent file was touched; the
  Action Center only *reads* existing pending-observation/job state).
- The live WhatsApp bridge was never started or re-paired — the one
  demo server this sprint used ran with `--no-bridge-adapter`.
- All new tests use `api_env`/`tmp_path`-backed throwaway databases —
  zero real network calls, zero real-DB writes (independently confirmed
  by the session-wide tripwire fixture in `conftest.py`, which ran
  clean across the full 1434-test session).

## Demo

Isolated demo running at `http://127.0.0.1:8517` against a freshly
seeded throwaway database in the session scratchpad (never
`data/dispatcher.db`). Login password is a per-process override, never
the real `LUXOR_DEMO_PASSWORD` — see the final report for the exact
value if you want to click through it yourself.
