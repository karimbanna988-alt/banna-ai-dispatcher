# Current Sprint Checkpoint — Operator Speed Round 2

Written at the natural end of the "FINAL PILOT-CLOSURE SPRINT — OPERATOR
SPEED ROUND 2 + SEARCH + DENSITY + FINAL SECURITY/PILOT CHECK" brief.
Supersedes the previous checkpoint (round 1's work — Fast Path, Action
Center grouping/priority, the first performance fix, the reload-CSRF
fix — is preserved in git history, HEAD `3350b38`).

## Starting state

HEAD `3350b38`. Real DB (`data/dispatcher.db`): 6 jobs, 10 events,
integrity OK — unchanged throughout this round (re-verified below).

## Scope decision, disclosed up front

Another 14-part, multi-day-scale brief. This round implemented — not
just documented — a bounded, real subset: a friction re-audit (found
nothing further safe to remove from Scenario A itself), global search,
Next Actionable Job, an Action Center performance fix that closed a
genuine O(n²) algorithmic issue, an extended pilot-readiness tool, a
backup-security review, a test-order-flake fix, and an honest effort
re-measurement. Two real, user-facing bugs were found and fixed
*during* this round's own live-testing (not planned work, but fixed
immediately since a broken write/navigation path directly undermines
the round's purpose) — see below.

Explicitly **not** attempted, each disclosed at the point it applies:
typing removal beyond what round 1 already ruled out (Priority 2 — see
below), a dedicated Closing-stage UI redesign beyond what already
existed (Priority 6's deeper ask), full desktop-density verification at
1366×768/1920×1080 and mobile at 430×932 (Priority 7/8 — only the
screens this round actually changed were spot-checked), and the
remaining per-job query batching flagged in round 1 (shadow episode,
technician acceptance — still once per job, not yet batched).

## Priority 1 — Re-audit for remaining Scenario A friction (DONE, honest null result)

Re-scored all 10 stages against the current implementation; reproduced
the exact same C/N/T/P/S/D counts as the end of round 1. No further
safe reduction exists in Scenario A's own single-job flow — every
remaining typed field is genuinely new information (technician's
message, closing amount, payment method), and every remaining decision
point is a real judgment call the brief protects. This conclusion
directed the rest of the round toward a *different* dimension (moving
between jobs in a queue) rather than forcing a number that doesn't
exist. Full writeup in `docs/WORKFLOW_SPEED_AUDIT.md`.

## Priority 2 — Typing audit (DONE, honest null result, folded into Priority 1)

Evaluated per field: ticket/technician/city/service are already
displayed, never typed, in the current UI (nothing to prefill there).
The remaining typed fields (technician's raw message, closing amount,
payment method) cannot be safely prefilled without fabricating data.
No change made — correctly, per the brief's own prohibition on
fabricating prices/closing values.

## Priority 3 — Global search (DONE)

`GET /api/search`: matches ticket/source/technician/city/service across
committed jobs (any status) and live pending messages — never
phone/address/customer name, matching Action Center's existing no-PII
rule. Capped at 20 results; the expensive per-result workflow
projection is only computed for the capped subset, so search stays fast
regardless of dataset size (measured p50 at 500 synthetic jobs: 14.76ms
matching-all-of-them, 7.93ms for a single specific match). Frontend: a
persistent header search box on desktop, a search-icon overlay on
mobile, `/` focuses it, arrow keys + Enter select a result, Escape
closes it. Selecting a result navigates directly into that job's Fast
Path context.

**Tests:** 11 new. **Commit:** `98ee267`.

## Priority 4/5 — Fast Path continuity audit + Next Actionable Job (DONE)

Audited every successful action for continuity; the two screen
transitions round 1 already fixed (dispatch confirm, Go to Closing)
were re-confirmed still correct. Added `route_next_actionable_job`: the
single highest-priority ACTION NOW/NEEDS REVIEW item, using the exact
same ordering Action Center itself displays. Frontend: a "Next
actionable job" button in the Action Center header, and a "Waiting on
technician" + "Next actionable job" prompt on any Active job whose own
next step is passively waiting on someone else. Never mutates anything
-- purely a read that tells the frontend where to navigate.

**Tests:** 5 new. **Commits:** `98ee267` (backend), `eb58b57` (frontend
+ shared navigation helper).

## Two real bugs found and fixed while live-testing search (not planned work)

**Bug 1 — `_job_target_screen` sent "Handle Manually" jobs to a screen
they never appear on.** A job whose technician assignment was rejected
for every eligible candidate (see Scenario D) gets status "Handle
Manually" — which is in NEITHER Active's `ACTIVE_JOB_WORKSPACE_STATUSES`
nor Closings'/History's membership rules. The screen-resolution helper
(added in round 1's Open-button bugfix) defaulted anything not
explicitly Closed/Cancelled/Rejected to "active", so search results,
Action Center's "Open", and Next Actionable Job all silently sent the
operator to the Active screen for such a job — where it never appears,
so `selectActive`'s own fallback silently substitutes whatever job
happens to be first in the list, with no error surfaced. Reproduced
live via search against a Scenario-D-shaped job (kept "selecting"
SCEN-F instead of the searched-for SCEN-D). Fixed: unmatched statuses
now route to `"actioncenter"` (the one screen such a job is genuinely
listed on, via `human_review_required`) instead of a screen that
silently doesn't show it.

**Bug 2 — `setScreen()` always fires its own unawaited `refreshAll()`,
racing any caller that does its own explicit load-then-select right
after it.** This is why round 1's `setTimeout(0)`→`await` fix for the
Action Center "Open" button wasn't actually sufficient: awaiting the
*second* load never controlled the *first*, unawaited one `setScreen`
itself always kicks off. Both loads fetch the same endpoint
concurrently; whichever resolves last wins, and if it's the unawaited
one, it silently overwrites a correct selection with its own
default-first-item fallback. `setScreen` gained an optional
`{skipRefresh: true}`, now used by every "navigate then select a
specific item" call site (`navigateToItem`, `confirm_dispatch`'s
auto-nav, `go_to_closing`). Verified with 15 repeated trials across 5
different jobs post-fix (all correct) versus a real, reproducible
mismatch before it.

Both found and fixed in the same pass rather than deferred, since a
broken navigation/selection path directly undermines this round's
entire purpose. **Tests:** 3 new regression tests. **Commit:** `8d0acd2`.

## Priority 9 — Action Center performance round 2 (DONE)

Profiled `route_action_center` at 500 synthetic jobs (all sharing one
technician — the worst case) with `cProfile` rather than guessing.
Found `_technician_k_dto`'s "who else is this technician already
juggling" computation was 80% of the route's total time — an O(n) scan
per job, making the whole route effectively O(n²) (5.1M `dict.get()`
calls in a 10-rep profile). Fixed with one O(n) grouping pass
(`_group_waiting_jobs_by_technician`) computed once per request.
Also fixed `technician_acceptance_schema_present` being queried twice
per job (~36% of the remaining cost) via an optional `schema_present`
param on `storage.get_technician_acceptance`.

Cumulative profiled cost at 500 jobs: 4.78s → 2.41s for 10 reps (~50%
reduction) on top of round 1's fix. Measured p50 at 500 jobs:
`route_action_center` 227.60ms → 175.68ms this round (-33.6% vs the
original pre-round-1 baseline); `route_active` 273.89ms → 212.39ms
(-59.6% vs original). No caching or SQLite denormalization added.

**Tests:** 2 new. **Commit:** `ae861aa`.

## Priority 11/12 — Backup security review + pilot-readiness tool extension (DONE)

`tools/system_readiness.py` already existed and covered much of what
was asked (DB integrity/FK, backup destination writable, required
secrets never printed, duplicate-secret comparison) — extended rather
than duplicated. Added: technician dispatch/closing-group mapping
completeness, service-area mapping presence, Quo GET-only and WhatsApp
no-send structural checks, the 0.0.0.0 bind-address warning, and a
demo/test DB isolation guard. Every `Check` now carries a category
(CODE / CONFIGURATION / EXTERNAL INTEGRATION) so a non-PASS result is
never mistaken for a software defect, per the brief's explicit
instruction. Run read-only against the real database: surfaced a real,
previously-undocumented finding — **12 of 15 active technicians are
missing a dispatch_group and/or closing_group** — informational only,
not modified (real configuration data).

Backup review: `scripts/backup_database.py`/`restore_database.py`/
`verify_backup.py` and `docs/BACKUP_RESTORE_RUNBOOK.md` reviewed.
Mechanism is sound (online-backup API, restore structurally refuses to
target the live database, retention scoped by exact source filename, no
secrets ever included). One CONFIGURATION gap documented (backup
directory doesn't harden its own Windows ACLs) — not a code defect.

**Tests:** 38 new (`tests/test_system_readiness.py`). **Commit:** `51d45c3`.

## Priority 10 — Test-order flake fix (DONE)

`operator_api.py`'s `_LOGIN_ATTEMPTS`/`_ACTIVE_SESSIONS` are
deliberately in-memory module-level state, but that let one test's
login-rate-limit exhaustion leak into a nearby test within the pytest
process — the exact flake round 1's checkpoint documented. New autouse
`conftest.py` fixture resets both before and after every test. **Commit:** `51d45c3`.

## Priority 13 — Effort re-measurement (DONE, honestly unchanged)

| | Scenario A | Closing |
|---|---|---|
| Original (HEAD `ad69d01`) | 61 | 24 |
| Pre-round-2 (HEAD `3350b38`) | 54 (-11.5%) | 21 (-12.5%) |
| Final (this round) | **54 (-11.5%, 0% vs pre-round-2)** | **21 (-12.5%, 0% vs pre-round-2)** |

Unchanged, honestly — no further Scenario A/Closing friction was found
or removed (see Priority 1). Two new supplementary scenarios quantify
what this round actually improved (a different dimension entirely):
"find a job when its stage is unknown" (search) 13 → 6 (-53.8%); "move
to the next actionable job" (Next Actionable Job) 7 → 1 (-85.7%). Full
tables and reasoning in `docs/WORKFLOW_SPEED_AUDIT.md`.

## Visual acceptance (live-verified, this checkpoint)

A completely fresh demo was seeded (`scripts/seed_command_center_demo_db.py`
+ `scripts/seed_demo_scenarios.py`, all six scenarios A-F) into a new
scratchpad database, with a fresh `operator_api.py` process (port 8520,
`--no-bridge-adapter`, throwaway demo password/session secret) — the
mid-development instance was not reused for final acceptance.

**Desktop (1440×900):** Command Center; Action Center (3 groups, correct
priority ordering, "Next actionable job" button); search (found SCEN-D
by ticket, landed on Action Center — the correct screen for a
Handle-Manually job, confirming Bug 1's fix); normal Fast Path (F400 →
"Go to Closing"); Human Review (SCEN-D visible with its real blocking
reason); Closing (SCEN-E's incomplete-closing state, "Missing: amount,
payment method"); Completed (SCEN-A closed, D600 cancelled). Race-fix
stress test: 15/15 correct across repeated search-then-click trials on
5 different jobs.

**Mobile (375×812):** Action Center as single-column cards with the
"Next actionable job" button, priority chips, no horizontal scroll;
mobile search overlay (search-icon toggle, full-width results); Fast
Path bottom sheet (F400 → "Go to Closing" reachable without scrolling)
confirmed correctly navigating to Closings when tapped (the properly
scoped/visible button — an initial test-script false negative from an
unscoped `document.querySelector` matching the desktop pane's
`display:none` duplicate copy was investigated and ruled out as a real
bug; a real finger tap can only ever reach the visible sheet element).

Not re-verified this pass: 1366×768/1920×1080 desktop, 430×932 mobile,
Command Center/Jobs/Closings layout at mobile width (no layout changes
were made to those beyond what's described above).

## Safety confirmations (re-verified at checkpoint time)

- Real `data/dispatcher.db`: `PRAGMA integrity_check` = `ok`, zero FK
  violations, 6 jobs / 10 events — unchanged from the start of this
  round and every prior round.
- `.env`: untouched, not inspected or modified this round.
- No Quo message sent, no Quo POST/PUT/PATCH/DELETE call made —
  re-confirmed structurally via `tools/system_readiness.py`'s new
  `check_quo_read_only`, which now runs as a repeatable regression
  check, not just a one-time manual grep.
- No WhatsApp message sent, no send-capable method called —
  re-confirmed structurally via the new `check_whatsapp_no_send`.
- The live WhatsApp bridge was never started or re-paired — every demo
  server this round used `--no-bridge-adapter`.
- The WhatsApp pilot allowlist and real technician mappings were not
  touched (the technician-mapping *finding* is informational only).
- `OPERATOR_API_SESSION_SECRET`'s duplicate-line issue and the
  `0.0.0.0` bind address were not changed (both still awaiting owner
  decision, unchanged from every prior round).
- No secret value appears in any diff this round.
- Full suite: **1522 passed, 0 failed** (`python -m pytest -q tests`,
  ~330s, run twice cleanly this round with zero flakes observed post-fix).

## Remaining blockers before a paid pilot

Unchanged from the prior checkpoint (owner decision on `0.0.0.0`,
duplicate session-secret line, backup directory ACL hardening on a
shared machine) plus one new, real, actionable item this round's
pilot-readiness tool surfaced: **12 of 15 active technicians are
missing a dispatch_group and/or closing_group** — confirm every
technician who will receive real dispatches during a pilot has both
configured before relying on them (`python tools/system_readiness.py`
to re-check).

## Remaining blockers before automatic outbound production

Unchanged: Quo remains structurally GET-only (now with a repeatable
regression check, not just a documented audit finding), WhatsApp
sending remains structurally absent (same), no auto-send/accept/
complete path exists anywhere — search and Next Actionable Job are both
pure reads, neither can mutate a job.

## Exact next steps

1. **Owner action:** review the 12-of-15 technician mapping gap and the
   `0.0.0.0` bind-address decision.
2. **Remaining query batching** for `route_active`/`route_action_center`
   (shadow episode, technician acceptance lookups still run once per
   job, just no longer duplicated or quadratic) — real, scoped, future
   task once real job volume makes it worth doing.
3. **Closing stage's deeper redesign** (Priority 6's full ask beyond
   navigation) — the Closing stage remains the single highest-typing
   stage in the workflow; this round's Fast Path navigation to it is
   done, but the data-entry form itself was not redesigned.
4. **Full responsive re-verification** at the remaining 2 desktop and 1
   mobile viewport not covered this round.
5. **The orphaned/unwired `activeDetailPane` copy on mobile** — noticed
   while investigating the mobile Fast Path test artifact above:
   `renderActiveDetail()` writes into `activeDetailPane.innerHTML` even
   on mobile (where it's `display:none` and invisible), and its own
   `wireActiveDetailActions` call wires the *wrong* root due to
   `isMobile()` resolving to the bottom sheet instead. Zero real user
   impact (the pane is genuinely invisible on mobile) — wasted render
   work only, not a functional bug. Not fixed this round; noted for a
   future pass.

## Demo

Fresh isolated demo left running at `http://127.0.0.1:8520`
(`--no-bridge-adapter`, throwaway demo password `luxor-round2-final`,
throwaway session secret, database at
`<scratchpad>/demo_round2_final.db` — never `data/dispatcher.db`),
seeded with the standard Command Center fixtures plus all six demo
scenarios (A-F).
