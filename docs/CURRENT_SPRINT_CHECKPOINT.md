# Current Sprint Checkpoint — Operator Speed Implementation Sprint

Written at the natural end of the "OPERATOR SPEED IMPLEMENTATION SPRINT
— FAST PATH + ACTION CENTER + CLOSINGS + PERFORMANCE + RESPONSIVE UX"
brief. Supersedes the previous checkpoint (that sprint's work — the
security audit, Scenarios C/D/E, the workflow-speed baseline audit — is
preserved in git history, HEAD `ad69d01`).

## Starting state

HEAD `ad69d01`. Real DB (`data/dispatcher.db`): 6 jobs, 10 events,
integrity OK — unchanged throughout this sprint (re-verified below).

## Scope decision, disclosed up front

The governing brief was again a 14-part, multi-day-scale spec (a full
guided Fast Path, Action Center rearrangement + deterministic priority,
an aggressive Closing redesign, typing removal, global search, safe
keyboard shortcuts, an N+1 performance fix, full responsive
verification at 5 viewports, a security follow-up pass, and an effort
model with a 25% reduction target) layered onto an already very long
session. This sprint implemented — not just documented — a bounded,
real subset:

1. Fixed the measured N+1-shaped performance issue in
   `route_active`/`route_action_center`.
2. Action Center 3-group presentation + deterministic priority buckets.
3. Fast Path auto-navigation at the two screen-transition friction
   points the baseline audit identified (dispatch confirm -> Active;
   technician-completed -> Closings).
4. Consistent Copy/Mark-sent button wording across all four
   prepared-outbound flows, plus safe keyboard shortcuts (J/K/Enter/Esc/C).
5. The effort model, computed with the brief's exact weights and
   reported honestly (short of the 25% target, with the specific reason
   why).

Explicitly **not** attempted, each disclosed at the point it applies in
`docs/WORKFLOW_SPEED_AUDIT.md`'s "What wasn't done this sprint" section:
typing removal (Priority 6 — evaluated field-by-field, none safely
pre-fillable without fabricating data), global search (Priority 8 — a
standalone-sized feature), a dedicated Closing fast-capture UI beyond
the navigation fix (Priority 5's deeper redesign), full 5-viewport
responsive re-verification (only the screens actually changed were spot
checked), and the remaining per-job query batching in
`route_active`/`route_action_center` (the duplicate-computation bug was
fixed; batching the still-once-per-job shadow-episode/technician-
acceptance lookups was not attempted).

## Priority 1 — Performance fix (DONE)

`route_active` was computing `_technician_k_dto` and
`_company_update_owed_dto` **twice** per job (once directly, once again
inside `_workflow_projection_dto`'s internal call), and both
`route_active`/`route_action_center` re-queried
`whatsapp_pending_observations` from scratch inside their per-job
loops. Both DTOs now accept precomputed values via a sentinel-based
optional parameter (`_UNSET`, distinguishing "not supplied" from a
legitimate `None` result); the observations list is fetched once per
request and threaded through.

**Measured (synthetic 10/100/500-job databases, never `data/dispatcher.db`):**

| Jobs | `route_action_center` p50 before&rarr;after | `route_active` p50 before&rarr;after |
|---|---|---|
| 10 | 6.56ms &rarr; 5.74ms (-12.5%) | 10.31ms &rarr; 6.49ms (-37.0%) |
| 100 | 45.49ms &rarr; 37.92ms (-16.6%) | 88.84ms &rarr; 47.50ms (-46.5%) |
| 500 | 264.66ms &rarr; 227.60ms (-14.0%) | 526.33ms &rarr; 273.89ms (-48.0%) |

No caching layer, no SQLite denormalization added — purely removing
duplicate work. **Tests:** 2 new (a pending-observations wiring
regression, a 50-synthetic-job latency-bound regression).
**Commit:** `04a15d5`.

## Priority 2/3 — Action Center grouping + priority, Fast Path navigation (DONE)

- New deterministic `priority` field per Action Center item
  (URGENT/DUE/NORMAL/WAITING) — integer-bucket logic from
  `human_review_needed`, item age, and a technician-K staleness
  escalation (30+ min waiting escalates WAITING -> DUE). No AI ranking,
  no exposed decimal score.
- New `groups` response field (Action now / Waiting on someone else /
  Needs review) as a presentation layer over the existing 10 sections —
  the 10 sections are unchanged in the API and every existing test
  still passes against them unmodified.
- Fixed a real, previously-undiscovered bug while rewriting the
  frontend renderer for this: the JS section-key list used
  `"ready_for_dispatch"` while the backend's actual key was
  `"ready_to_dispatch"` — that whole section (jobs ready to send to a
  technician) silently rendered as permanently empty. Structurally
  eliminated (stage labels are now driven directly from `next_action`
  codes, not a second duplicated key list).
- Fast Path: confirming dispatch now auto-navigates to Active with the
  same job pre-selected; a job whose technician reported "Completed"
  shows a dominant "Go to Closing" action that auto-navigates to
  Closings with that job pre-selected. No screen ever auto-advances
  into an external send, acceptance, decline, or completion.

**Tests:** 5 new (priority-bucket correctness, group repartitioning
with no loss/duplication, deterministic within-group ordering).
Live-verified in-browser at desktop and mobile (screenshots taken,
described under "Visual acceptance" below).
**Commit:** `6e67b75`.

## Real bug found and fixed while testing the above: CSRF token lost on reload

`app.js`'s `boot()` calls `Api.checkSession()` (`GET /api/me`), never
`login()`, whenever the session cookie is already valid — the ordinary
case on any page reload. `checkSession()` never populated the
frontend's in-memory CSRF token (only `login()` did), so every write
after a reload 403'd with "Missing or invalid CSRF token" until an
explicit logout/login. Found by live-testing the Fast Path changes in
the browser — every write attempt failed after a routine reload.

**Fix:** `route_me` now also returns `csrf_token_for(session_token)` —
the same pure function `route_login` already used, re-derived from a
session token the request already proved possession of, so no new
secret or capability is created. `checkSession()` stores it.
**Tests:** 2 new, end-to-end (`GET /api/me` alone, never `/api/login`,
produces a token that a subsequent write accepts; the token matches
what login would have issued). **Documented:**
`docs/SECURITY_AUDIT.md` finding #5 (updated in place, since it
directly concerns the CSRF/session mechanism that finding covers).
**Commit:** `6e67b75` (bundled with Priority 2/3 — found and fixed in
the same pass, not deferred, since a broken write path after any
reload directly undermines this sprint's entire goal).

## Priority 4/7 — Copy/Mark-sent consistency + safe keyboard shortcuts (DONE)

All four prepared-outbound flows (company K, technician dispatch,
company update, company closing) now use the same pattern: "Copy
&lt;thing&gt;" + "Mark sent manually" (K keeps its own already-consistent
"Mark K sent manually"). Four different confirm-sent phrasings
("Confirm sent manually", "I sent this reply manually", "Confirm
company response sent manually", "I sent this closing manually")
collapsed to one.

Safe keyboard shortcuts, desktop-only (mobile needs none of this):
`J`/`K` move selection in whichever list screen is open (Jobs/Active/
Closings); `Enter` opens the mobile sheet for the current selection;
`Esc` closes any open sheet/modal; `C` clicks the current detail's one
prepared-copy button, but only when exactly one exists (never a guess).
None bound to a mutating action — mark sent, assign, accept/decline
technician, closing confirmation, and complete all stay click-only.
`/` (focus search) intentionally not bound — no search feature exists
in this build to focus.

Live-verified in-browser: J/K move and clamp at list boundaries, typing
in a textarea doesn't trigger any shortcut, Esc closes an open sheet, C
clicks the single unambiguous copy target.
**Commit:** `e2426f1`.

## Priority 14 — Effort model, before/after (DONE, honestly short of target)

`EFFORT = C + 3N + 4T + 2P + 2S + 3D`, the brief's exact weights,
applied stage-by-stage to Scenario A's 10 stages.

- **Full Scenario A:** 61 &rarr; 54 (**-11.5%**).
- **Closing workflow alone:** 24 &rarr; 21 (**-12.5%**).

Both fall short of the brief's 25% aspirational target — reported as
measured, not adjusted, per the brief's own "do not manipulate the
metric" instruction. N (navigation, 3x weight) was fully eliminated
where touched (both of Scenario A's two screen transitions now cost
zero operator-recalled navigation) — the highest-leverage change
available, since there were only two navigation points to remove and
both are gone. T (typing, 4x — the heaviest weight in the model) was
deliberately left untouched: every remaining typed field is genuinely
new information from outside the system (a technician's message, a
closing amount, a payment method), and pre-filling any of it would mean
fabricating data, which the brief's own absolute safety rules prohibit.
D (decision points) was correctly left unchanged — none of this
sprint's changes removed a real judgment call. Full stage-by-stage
table and reasoning in `docs/WORKFLOW_SPEED_AUDIT.md`.

**Caveat documented in the same file:** this model is scoped to one job
moving through the workflow alone, so it does not capture Action
Center's multi-job triage improvement (including the section-key bug
fix) — that benefit is real but has no dimension in this particular
model.

**Commit:** `76d382b`.

## Visual acceptance (live-verified, this checkpoint)

A completely fresh demo (`scripts/seed_command_center_demo_db.py` +
`scripts/seed_demo_scenarios.py`, all six scenarios A-F) was seeded into
a new scratchpad database, and a fresh `operator_api.py` process started
against it (port 8519, `--no-bridge-adapter`, throwaway demo password
and session secret) — the mid-development demo instance used while
building this sprint's features was not reused for final acceptance,
to verify the actual shipped state rather than a state polluted by
manual testing.

Verified at desktop (1440×900):
- Command Center loads.
- Action Center: 3 groups (Action now (8) / Waiting on someone else (6)
  / Needs review (4)), correct priority chips and ordering (the
  4-hour-old unassigned job sorts first in Action Now as URGENT; stale
  8/6/5/3-hour waits sort before fresh waits in the Waiting group).
- F400 (technician reported Completed) shows the "Go to Closing"
  primary action; lifecycle stepper renders correctly.

Verified at mobile (375×812):
- Action Center renders as single-column cards, priority chips visible,
  bottom nav present and not overlapping content, no horizontal scroll.
- F400's detail opens in the bottom sheet with "Go to Closing" as the
  dominant primary action, reachable without scrolling.
- Clicking "Go to Closing" on mobile correctly navigates to Closings
  with F400 pre-selected and its capture form open.

Not re-verified this pass: 1366×768/1920×1080 desktop, 430×932 mobile,
and Command Center/Jobs/Closings at mobile width (no layout changes
were made to those screens this sprint, so this is a coverage gap, not
a known issue — disclosed rather than silently skipped).

## Safety confirmations (re-verified at checkpoint time)

- Real `data/dispatcher.db`: `PRAGMA integrity_check` = `ok`, zero FK
  violations, 6 jobs / 10 events — unchanged from the start of this
  sprint and every prior sprint.
- `.env`: untouched, not inspected or modified this sprint (still
  `.gitignore`d, confirmed).
- No Quo message sent, no Quo POST/PUT/PATCH/DELETE call made —
  `quo_client.py` re-confirmed to contain no method capable of a
  non-GET request.
- No WhatsApp message sent, no send-capable `whatsapp-web.js` method
  called; the bridge's receive-only guarantee was not touched.
- The live WhatsApp bridge was never started or re-paired — every demo
  server this sprint used ran with `--no-bridge-adapter`.
- The WhatsApp pilot allowlist and real technician mappings were not
  touched.
- `OPERATOR_API_SESSION_SECRET`'s known duplicate-line issue was not
  resolved (still explicitly out of scope, unchanged from every prior
  sprint).
- The `0.0.0.0` server bind address was not changed (still awaiting
  owner confirmation, per Security Audit finding #9, unchanged from the
  prior sprint).
- No secret value appears in any diff this sprint (checked via
  `git diff` grep across every commit's touched files).
- Full suite: **1492 passed, 0 failed**
  (`python -m pytest -q tests`, ~320s). One flaky failure was observed
  in an earlier run of the same suite
  (`test_post_without_csrf_token_rejected`, passes deterministically in
  isolation) — traced to `operator_api.py`'s module-level
  `_LOGIN_ATTEMPTS`/`_ACTIVE_SESSIONS` dicts not being reset between
  tests, a pre-existing test-isolation gap (same category as the
  "socket-teardown flake" noted in earlier checkpoints), not a
  regression from this sprint's changes — confirmed by running the
  full suite again cleanly and by running the affected test file alone
  twice with zero failures either time.

## Remaining blockers before a paid pilot

Unchanged from the prior checkpoint: owner decision on the `0.0.0.0`
bind address; the duplicate `OPERATOR_API_SESSION_SECRET` line in
`.env`; backup file permissions unreviewed. New, minor, operational-only
item from this sprint: if a pilot's session was already open before
this upgrade, one logout/login clears the (now-fixed) reload CSRF gap
cleanly for that session — not a blocker, just a one-time note for
whoever deploys this update to a live pilot.

## Remaining blockers before automatic outbound production

Unchanged: Quo remains structurally GET-only, WhatsApp sending remains
structurally absent, no auto-send/accept/complete path exists anywhere
— every state transition that reaches an external party or a terminal
status still requires an explicit human click, including every new Fast
Path transition added this sprint (which only ever auto-navigates
screens/pre-selects a job, never auto-confirms an external action).

## Exact next steps

1. **Remaining query batching** for `route_active`/`route_action_center`
   (shadow episode, technician acceptance lookups) — a real, scoped,
   future performance task now that the duplicate-computation bug is
   fixed; only worth doing once real job volume approaches the point
   where it's user-visible (current real volume: 6 jobs, low single-digit ms).
2. **Global search (Priority 8)** — backend route, frontend UI, latency
   measurement, then wire the `/` shortcut that was deliberately left
   unbound this sprint.
3. **Closing fast-capture UI (Priority 5's deeper redesign)** — the
   Closing stage remains the single highest-typing stage in the
   workflow (T=3 required fields); the navigation friction around it is
   now fixed, but the data-entry itself was not redesigned this sprint.
4. **Full responsive re-verification** at the 2 remaining desktop and 1
   remaining mobile viewport, and at the screens this sprint didn't
   touch.
5. **Test-isolation fix** for `operator_api.py`'s module-level
   `_LOGIN_ATTEMPTS`/`_ACTIVE_SESSIONS` dicts (an autouse fixture that
   resets them between tests would remove the observed flake).

## Demo

Fresh isolated demo left running at `http://127.0.0.1:8519`
(`--no-bridge-adapter`, throwaway demo password
`luxor-demo-final`, throwaway session secret, database at
`<scratchpad>/demo_final_acceptance.db` — never `data/dispatcher.db`),
seeded with the standard Command Center fixtures plus all six demo
scenarios (A-F). This is a clean instance seeded specifically for this
checkpoint's visual-acceptance pass, separate from the instance used
during development.
