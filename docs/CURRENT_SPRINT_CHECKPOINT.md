# Current Sprint Checkpoint — Full-Lifecycle Workflow Coverage + a Real Test-Isolation Bug Fix

Written at a natural stopping point, not a session-limit cutoff. Supersedes
the previous checkpoint (that sprint's work — the unified committed-job
projection, the first Action Center build — is preserved in git history).

## Starting state

HEAD `ea714ad` (end of the "unified workflow projection + Action
Center" sprint). Real DB (`data/dispatcher.db`): 6 jobs, 10 events,
integrity OK — unchanged throughout this sprint.

## Priority 1 — Fix the workflow coverage gap (DONE)

The prior sprint's report claimed `ACK_COMPANY_K` and
`SEND_JOB_TO_TECHNICIAN` were "structurally unreachable" for a
committed job. That claim was correct for the *committed-job* half of
the projection but incomplete: it never built the *pre-commit* half.

**What was built:** `dispatcher.workflow_projection.derive_pending_message_next_action(ctx)`
— a second pure function, same `WorkflowProjection` contract, same
`NEXT_ACTIONS` vocabulary, covering the pre-commit lifecycle (raw
message → reviewed → K owed → ready to dispatch), with its own explicit
precedence (needs-review first, then K-owed, then no-eligible-technician
as a human-review case, then ready-to-dispatch). `ctx` is built from
exactly the same fields `_new_job_dto` already computes (`needs_review`,
`k_required`, `k_sent`, `eligible_count`, `dispatched`) — no new query.

Action Center now normalizes items from **both** projections into one
schema via `_normalize_action_center_item()`:
`entity_type` (`JOB`/`INCOMING_MESSAGE`), `entity_id`, `ticket`,
`source`, `technician`, `stage`, `next_action`, `blocking_reason`,
`age`, `human_review_needed`, `destination`, `delivery_mode` (always
`"MANUAL COPY"` in this build — documented why the other two values
from the brief, `MOCK DEMO DELIVERY` / `EXTERNAL CONNECTOR REQUIRED`,
don't apply yet). Legacy `id`/`kind`/`waiting_since`/`age_minutes`
aliases are populated from the same values (not computed twice) so the
current frontend needed zero changes to its render function.

**Reachability proof, not just assertion:**
- Pure-function tests: `test_ack_company_k_is_genuinely_reachable`,
  `test_send_job_to_technician_is_genuinely_reachable`
  (`tests/test_workflow_projection.py`).
- Integration tests seeding **real** messages through **real** routes:
  `test_action_center_ack_company_k_reachable_through_real_workflow_state`
  (a real AGN message — AGN requires K per `policy.py`) and
  `test_action_center_send_job_to_technician_reachable_through_real_workflow_state`
  (a real Main Leads message — no K required) —
  `tests/test_operator_api.py`. Both pass.
- A third test proves the transition: `..._disappears_once_acknowledged`
  — the same message moves from `company_k_owed` to `ready_to_dispatch`
  the moment `route_acknowledge` is called.

**Commit:** `0f31d50`.

## Priority 2 — Verify Action Center section count (DONE)

The prior report said "9 sections" in prose but the code already
returned 10 keys — the prose was wrong, not the count. Realigned
section keys/titles to the brief's exact 10, in the exact required
order: `new_jobs_to_review`, `company_k_owed`, `assign_technician`,
`ready_to_dispatch` (renamed from `ready_for_dispatch`, now genuinely
`SEND_JOB_TO_TECHNICIAN`-driven instead of ad hoc filtering),
`waiting_technician_k`, `technician_updates_to_review`,
`company_updates_owed`, `closings_to_review`, `closings_ready_to_send`,
`human_review_required`. Every section now also carries its own
`title` string in the API response (previously title-only-in-frontend).

**Verified live** against the isolated demo: all sections render with
real data using the corrected labels; `company_k_owed` showed a real
AGN message correctly requiring acknowledgment.

## An unplanned but significant finding — real network calls in tests

While re-verifying the full suite, `test_post_with_valid_csrf_token_reaches_the_route`
went from "passes" to "hangs for 5-30s" to "fails reliably" across
consecutive runs in this session — the exact pattern every prior
sprint's report described as a "pre-existing socket flake." Traced to
the actual root cause instead of re-documenting it as a flake again:

**The test's only guarantee that `/api/sync` hits the "not configured"
400 path was a source comment, not a fixture.** `route_sync` reads
`QUO_API_KEY` from the process environment; when that key happens to be
set ambiently (this session's shell inherited a real key from a real
`.env`-backed environment), the test instead makes a **real,
authenticated network call to `api.quo.com`** — which this sandboxed
environment cannot reach, producing a 15-30 second connect timeout that
looks, from the test's own perspective, exactly like a flaky socket
teardown.

**Fix:** a new session-wide autouse fixture in `conftest.py` that
unconditionally clears `QUO_API_KEY` via `monkeypatch` before every
test — fail-closed by construction, the same pattern this project
already established for the database path (`dispatcher.db_safety`).
Any test that genuinely needs the key set can still call
`monkeypatch.setenv()` itself; that layers on top of, not blocked by,
this fixture. Verified: the previously-hanging test now passes
consistently in 1.7s across repeated runs, instead of intermittently in
5-30s.

This means the "pre-existing flake" line in every prior sprint's report
in this session was half-right: real, but for the wrong reason, and now
actually fixed rather than merely re-observed. The *other* flake class
(`ConnectionAbortedError` on `live_server` teardown under a long,
many-server full-suite run) is still present and still unrelated to
this or any other change this session — confirmed passing on isolated
retry, same as every prior report.

**Commit:** `36b8d27`.

## Full-suite verification

1449 tests: 1447 passed, 2 failed on the full run
(`test_post_without_csrf_token_rejected`,
`test_observation_rejected_without_correct_secret`) — both the
`ConnectionAbortedError` teardown flake described above, both confirmed
passing on isolated retry. Real DB integrity/FK checks clean, job/event
counts unchanged (6 jobs, 10 events).

## What was deliberately not attempted, and why

Priorities 3-6 from this sprint's brief were not started this round —
the unplanned test-isolation bug (above) was treated as higher priority
than starting new feature work, since it affects every future sprint's
ability to trust "the suite is green":

- **P3 — Six synthetic demo scenarios** (Happy Path, Human Review,
  Duplicate/Correction, Technician Exception, Incomplete Closing,
  Technician Decline). Not started. Happy Path remains the single
  highest-value next piece — see prior checkpoint's "exact next steps,"
  still accurate.
- **P4 — Lifecycle stepper UI.** Not started. Note: the data this needs
  already exists on Active's job DTO (`workflow.next_action`) — this is
  now purely a frontend rendering task, not a backend one.
- **P5 — Sales demo script rewrite.** Not started.
- **P6 — Full visual-proof sweep** across every screen. Partially done
  (Action Center screenshotted live against real corrected data); the
  full list from the brief (stepper, closings-ready, mobile Action
  Center, etc.) was not captured.

## Exact next steps

1. **Happy Path demo scenario (P3)** — build as a real seed script
   (same pattern as `scripts/seed_command_center_demo_db.py`), driving
   a job through every stage via real route/service calls, ending in a
   genuine `complete_job()` call. Use it to also visually verify
   `ready_to_dispatch` (the one section this sprint could not
   demonstrate live — the ad hoc manual pending-message insert used for
   a quick browser check didn't route through `get_live_dispatch_queue`
   correctly; the *automated* integration test for this section already
   passes using the correct `seed_pending()` helper, so this is a
   demo-script gap, not a projection bug).
2. **Lifecycle stepper (P4)** — read-only rendering of `workflow.next_action`
   already on Active's DTO; frontend-only work now.
3. **Sales doc + full visual sweep (P5/P6)** — lowest priority.
4. Consider auditing other tests for the same "assumes ambient
   environment is clean" anti-pattern the QUO_API_KEY fix closed —
   `WHATSAPP_BRIDGE_ADAPTER_SECRET`, `LUXOR_DEMO_PASSWORD`, etc. are
   candidates, not yet audited.

## Safety confirmations (re-verified at checkpoint time)

- Real `data/dispatcher.db`: `PRAGMA integrity_check` = `ok`, zero FK
  violations, 6 jobs / 10 events — unchanged.
- `.env`: untouched, not inspected or modified this sprint.
- No Quo message sent — and, per this sprint's own finding, this is now
  a *stronger* guarantee than before: the test suite itself can no
  longer accidentally attempt one, regardless of ambient environment.
- No WhatsApp message sent (bridge remains structurally receive-only,
  unchanged this sprint).
- The live WhatsApp bridge was never started or re-paired — the one
  demo server this sprint used ran with `--no-bridge-adapter`.
- All new tests use `api_env`/`tmp_path`-backed throwaway databases —
  zero real network calls now genuinely guaranteed, not just observed.

## Demo

Isolated demo running at `http://127.0.0.1:8517` against a freshly
seeded throwaway database in the session scratchpad (never
`data/dispatcher.db`). Login password is a per-process override, never
the real `LUXOR_DEMO_PASSWORD` — see the final report for the exact
value if you want to click through it yourself.
