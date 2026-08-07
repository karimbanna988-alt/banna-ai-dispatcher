# Workflow Speed Audit

Scope: Part 1 of the two-hour optimization brief (workflow speed baseline)
only. This document is descriptive/measurement only -- it does not
implement any UI redesign, effort-scoring model, fast-path flow,
Action Center rearrangement, keyboard shortcuts, or performance fix.
Those (brief Parts 2-11 and 15-17) were explicitly scoped out this pass
and are listed as not-done in `docs/CURRENT_SPRINT_CHECKPOINT.md`.

Two independent things are measured here and are **never mixed**:

1. **Operator interaction counts** -- clicks, screen transitions, typing,
   copy operations -- for the Scenario A happy path, counted directly
   from the current frontend source (`operator_ui_v2_connected/js/app.js`),
   not from a stopwatch or a claimed human-time estimate.
2. **Measured local API latency** -- `time.perf_counter()` around
   in-process calls to the route handlers in `operator_api.py`, against
   synthetic SQLite databases. This is real, measured execution time on
   this machine. It is **not** a claim about network or Quo/WhatsApp
   provider latency, which this method cannot see at all.

## Method note on the interaction counts

The counts below come from reading the actual click-handler wiring in
`app.js` (`wireJobDetailActions`/`handleJobAction`,
`wireActiveDetailActions`/`handleActiveAction`,
`wireClosingDetailActions`/`handleClosingAction`) rather than driving a
live browser session end to end, so every number below is cited to a
specific function and can be checked by reading that function. Where a
step is conditional (e.g. no deterministic company reply available), both
paths are noted.

## Scenario A operator path (10 stages)

| # | Stage | Clicks | Screen/panel transitions | Typing / paste required | Copy operations | Blocking decision | Main friction |
|---|-------|--------|---------------------------|--------------------------|------------------|--------------------|----------------|
| 1 | Incoming job review | 1 (select row in Jobs list) | 0 (detail pane opens in place) | 0 | 0 | none | "Job details" (customer/phone/address/original message) is collapsed by default behind a `<details>` disclosure (`jobDetailHtml`, app.js:604-616) -- an extra click if the operator needs it, but it keeps the primary panel short. |
| 2 | Company K (acknowledge) | 2: "Copy K" (`reveal_k`) then "Mark K sent manually" (`acknowledge`) | 0 | 0 | 1 (`copyText("K")`, app.js:679-682) | Operator must return from Quo/WhatsApp and confirm manually -- no auto-detection | Two clicks for a fixed, non-variable payload ("K") is the most mechanical step in the flow; it exists because sending is never automated (by design, per the absolute safety rules). |
| 3 | Technician determine | 0 operator clicks | 0 | 0 | 0 | none (system-computed) | Eligible technician is computed deterministically by `storage.assign_technician()` / area+skill matching, not chosen by the operator in this flow -- the operator only sees the result (`techLine`, app.js:583-585). If zero or multiple eligible technicians exist, this surfaces as `needs_review` (Scenario B/D territory) instead. |
| 4 | Technician dispatch | 4: "Copy technician job" (`prepare`) -> "Open WhatsApp" (`open_whatsapp`) -> "Confirm sent manually" (`confirm_prompt`, opens modal) -> "Yes, sent manually" (`confirmDispatch`) | 0 (modal overlay, not a screen change) | 0 | 1 (`copyText(j.luxor_message)`, called twice -- once on `prepare`, again on `open_whatsapp`, app.js:688-694) | Explicit "did you actually send it" confirmation modal before the job leaves the Jobs queue | The message is copied to the clipboard twice in a row (`prepare` then `open_whatsapp` both call `copyText`) -- redundant but harmless; the real friction is the 4-click chain for what is functionally one decision ("send this job to this technician"). |
| 5 | Wait for technician K | 0 | 0 | 0 | 0 | n/a (passive wait, system/technician side) | Not operator-controlled; shows up as "Awaiting technician" in Command Center/Action Center until stage 6. |
| 6 | Technician K (accept) | 2: select job in Active list (screen transition Jobs -> Active) + "confirm_k_accepted" | 1 (Jobs screen -> Active screen) | 0 | 0 | Operator is attesting to a real WhatsApp reply they read outside the app | Requires a screen change the operator must remember to make (job silently moves from the Jobs queue's mental model into Active). The decline path (`confirm_k_declined`, app.js:1420-1424) additionally requires a typed reason via a native `window.prompt()` -- functional but not styled like the rest of the app. |
| 7 | Technician update (status/price) | 2 minimum: "Interpret" (`interpret`) then "Save verified update" (`save`) | 0 | 1 paste (technician's raw WhatsApp text into `pasteText-{id}`) + 0-2 corrections (quote type/amount/customer response fields, pre-filled from the interpreted preview but editable) | 0 | If `human_review_required` or `quote_type === "UNKNOWN"`, a review banner blocks silent accept (app.js:1285-1287) | Paste-then-interpret-then-save is a real 3-step read/verify/commit cycle by design (never auto-trusts a raw parse) -- appropriate given no auto-send exists downstream, but it is the least "instant" step in the flow. |
| 8 | Company update (send) | 2: "Copy response" (`copy_reply`) + "Confirm company response sent manually" (`confirm_reply`) -- **only when a deterministic prepared reply exists**; otherwise the operator must type the reply manually via `statusUpdateManualText` before `prepare_company_update` (app.js:1393-1401), adding 1 typing step and 1 extra click | 0 | 0 (deterministic path) / 1 (manual path) | 1 (`copyText(preview.preparedText)`, app.js:1375-1377) | None beyond the copy+confirm pair | The manual-reply fallback is not visually distinguished from the deterministic path until the operator notices no `preparedText` appeared -- a legitimate future UX finding, not fixed this pass. |
| 9 | Closing (receive -> validate -> send) | 4 across 2 sub-steps: "Save closing details" (`record`) then, once amount+payment are present, "Copy closing / mark prepared" (`prepare`) + "I sent this closing manually" (`confirm_sent`) | 1 (Jobs/Active -> Closings screen) | 3 required (raw closing paste, final amount, payment method) + 1 optional (parts amount) | 1 (`copyText(c.prepared_text)`, app.js:1560-1563) | `prepare_company_closing` refuses with a specific "Missing: ..." reason if amount/payment aren't both present (see Scenario E) -- surfaced as a review banner, not a silent failure | Closing is the single highest-typing stage in the whole flow (3-4 fields typed by hand from a technician's raw message) -- the most defensible place to look for a faster capture UI in a future pass, since it's real data entry, not busywork. |
| 10 | Completion | 1 ("Complete job", `complete`) | 0 | 0 | 0 | Only reachable after `sent_confirmed` is true (closingDetailHtml's final branch, app.js:1527-1531) | None -- this is the cleanest step in the flow: one click, one clear precondition. |

**Totals for one full Scenario-A job, deterministic/happy path throughout:**
18 clicks, 2 screen transitions (Jobs -> Active -> Closings), 1 confirmation
modal, ~5 required typed/pasted fields (paste K acknowledgment is a copy,
not typing; the technician-message paste in stage 7, the raw-closing
paste + amount + payment method in stage 9, plus 0-2 optional corrections
in stage 7), and 4 clipboard-copy operations. This matches the 10-event
audit trail asserted by
`tests/test_demo_scenarios.py::test_scenario_a_happy_path_has_a_complete_audit_trail`.

No effort-weighting or before/after optimization is presented here --
that would require Part 2's effort model and Parts 3-11's actual
redesign, both explicitly out of scope this pass.

## Measured local API latency

All numbers are `time.perf_counter()` around direct, in-process Python
calls to the route handler functions (no HTTP layer, no browser, no
network) -- p50/p95/max over repeated calls against a throwaway SQLite
database. These are **local execution-time measurements only**; they
say nothing about Quo/WhatsApp network latency or real-world page-load
time (DOM render, JS parse, network round trip to the actual deployed
server are all excluded).

### Small dataset (18 jobs -- the standard demo/scenario fixture set)

N=200 in-process calls per route.

| Route | p50 | p95 | max |
|---|---|---|---|
| `route_command_center` | 7.39ms | 8.42ms | 10.63ms |
| `route_action_center` | 5.20ms | 6.16ms | 16.66ms |
| `route_active` | 3.65ms | 4.28ms | 5.02ms |
| `route_new_jobs` | 2.64ms | 3.10ms | 3.63ms |
| `route_closings` | 2.51ms | 3.09ms | 4.06ms |
| `route_technicians` | 1.92ms | 2.56ms | 4.12ms |

At this volume every route is comfortably sub-20ms locally -- not a
concern at current real-world job volumes (the real DB has 6 jobs today).

### Synthetic volume scaling (10 / 100 / 500 jobs)

Per the brief's explicit instruction, this uses clearly-synthetic data,
never production data, and never touches `data/dispatcher.db` -- each
volume was bulk-inserted directly into a throwaway scratchpad SQLite
file (`resolve_db_path`-style isolated path) with minimal-but-schema-valid
`jobs`/`events` rows (one technician, uniform "Approved" status, no real
customer data). This is **not** a correctness fixture and is not claimed
to exercise every code path Scenario A does -- it exists purely to see
how the two heaviest per-job-loop routes scale with row count. N=30 calls
per route per volume (fewer than the small-dataset run, because each call
is already tens to hundreds of milliseconds at the larger volumes).

| Jobs | `route_action_center` p50 | p95 | max | `route_active` p50 | p95 | max |
|---|---|---|---|---|---|---|
| 10 | 6.56ms | 7.34ms | 7.85ms | 10.31ms | 10.54ms | 10.57ms |
| 100 | 45.49ms | 50.32ms | 50.67ms | 88.84ms | 96.85ms | 100.67ms |
| 500 | 264.66ms | 287.74ms | 308.06ms | 526.33ms | 656.49ms | 668.98ms |

**Finding (evidenced):** both routes scale roughly linearly with job
count, consistent with a per-job query loop rather than a single batched
query. `route_active` (`operator_api.py:564-592`) called
`storage.get_shadow_episode_for_job`, `_active_next_action_label`,
`_company_update_owed_dto`, `_technician_k_dto`, and
`_workflow_projection_dto` once *per job* inside its `for j in jobs:`
loop -- each of those does at least one additional SQL query, so the
route issued on the order of 5N+1 queries for N active jobs.
`route_action_center` composes several similar per-item lookups across
its 10 sections. At the real database's current volume (6 jobs) this was
already invisible (low single-digit ms); at 500 synthetic jobs it
crossed half a second.

**Update -- fixed in the Operator Speed Implementation sprint (this
document's second half, below):** `_technician_k_dto` and
`_company_update_owed_dto` were being computed *twice* per job in
`route_active` (once directly, once again inside
`_workflow_projection_dto`'s internal call), and
`storage.list_whatsapp_pending_observations()` was re-queried from
scratch inside the per-job loop in both routes instead of once per
request. Both are now computed/fetched once and threaded through. This
is a redundant-work fix, not a batching rewrite -- the remaining
per-job queries (`get_shadow_episode_for_job`, `get_technician_acceptance`,
the workflow-projection's `closing` lookup) still run once per job and
were deliberately left alone (see below for why). Batching those into
`IN (...)` queries remains a real, scoped, **not yet attempted** future
task.

---

# Operator Speed Implementation Sprint

Continuation of the audit above, covering the follow-on sprint that
implemented (not just measured) a bounded set of the highest-value
optimizations. Starting point: the baseline captured above (18 clicks,
2 screen transitions, ~5 typed/pasted fields, 4 copies for one full
Scenario A job; route_active/route_action_center's measured N+1-shaped
scaling). This sprint deliberately did not attempt the full 18-part
brief that motivated it -- see `docs/CURRENT_SPRINT_CHECKPOINT.md` for
the exact scope decision and what was left out.

## Performance re-benchmark (same method, same synthetic volumes)

Same `time.perf_counter()`, in-process, no-HTTP-layer method as the
baseline above; same throwaway scratchpad SQLite databases at 10/100/500
synthetic jobs (never `data/dispatcher.db`).

| Jobs | `route_action_center` p50 (before &rarr; after) | % change | `route_active` p50 (before &rarr; after) | % change |
|---|---|---|---|---|
| 10 | 6.56ms &rarr; 5.74ms | -12.5% | 10.31ms &rarr; 6.49ms | -37.0% |
| 100 | 45.49ms &rarr; 37.92ms | -16.6% | 88.84ms &rarr; 47.50ms | -46.5% |
| 500 | 264.66ms &rarr; 227.60ms | -14.0% | 526.33ms &rarr; 273.89ms | -48.0% |

`route_active` improved materially (roughly halved at scale) because it
had the literal duplicate-computation bug described above. `route_action_center`
improved more modestly (~14-17%) because its only redundancy was the
per-job re-fetch of an (empty, in this synthetic dataset)
`whatsapp_pending_observations` table -- a real fix, but a smaller one.
`route_active`'s remaining per-job queries (shadow episode, technician
acceptance) are the honest reason it isn't faster still; batching those
is the scoped future task noted above. **No caching layer and no SQLite
denormalization were added**, per the brief's explicit instruction --
this is entirely "stop doing the same query twice," not new
infrastructure.

Job-detail and search routes were not separately benchmarked this
round: job detail (`route_job_workspace`) is a single-job lookup with no
per-job loop to begin with (not implicated in the N+1 finding), and no
global search route exists yet in this build (see below).

## Fast Path -- what changed

- Confirming dispatch (Jobs screen) now auto-switches to the Active
  screen with that same job already selected, instead of leaving the
  operator on Jobs to manually navigate and re-find it. This removes
  the exact friction the baseline audit flagged for that stage
  ("requires a screen change the operator must remember to make").
- A job whose technician reported "Completed" now shows a single
  dominant "Go to Closing" primary action in its Active detail panel,
  which jumps straight to the Closings screen with that same job
  already selected -- removing the equivalent friction flagged for the
  Closing stage.
- Nothing auto-advances into an external send, an acceptance, a
  decline, or a completion -- every step past an auto-navigation still
  requires its own explicit click, unchanged from the baseline.
- Action Center gained a 3-group presentation (Action now / Waiting on
  someone else / Needs review) with a deterministic priority bucket
  (URGENT/DUE/NORMAL/WAITING) per item, sorted oldest-and-most-urgent
  first within each group -- this reduces *scanning* effort across the
  whole queue, a dimension the single-job effort model below does not
  capture (see the caveat in that section).
- The four "copy a prepared message, then mark it sent" flows (company
  K, technician dispatch, company update, company closing) now use one
  consistent button pattern ("Copy &lt;thing&gt;" / "Mark sent
  manually") instead of four different confirm-sent phrasings.
- Safe keyboard shortcuts (J/K/Enter/Esc/C) were added for the
  list-based screens, desktop-only, none bound to a mutating action.

## Effort model -- before/after

Per the brief's instruction, this is **a comparative engineering
heuristic, not measured human time**, and is kept entirely separate
from the measured latency numbers above. Weights are exactly as
specified:

```
EFFORT = C + 3N + 4T + 2P + 2S + 3D
C = clicks/taps            N = navigation/context switches
T = typing interactions    P = copy operations
S = scroll/search ops      D = decision points
```

**Method:** each of Scenario A's 10 stages (from the baseline table
above) was re-scored stage by stage, assigning explicit C/N/T/P/S/D
counts from the same real click-handler wiring in `app.js` used to
build the original baseline table. A "decision point" (D) is scored
only where the operator must genuinely judge or choose between
options (classifying a technician-K candidate, verifying/correcting a
parsed technician update, confirming a closing is actually complete)
-- not for routine "confirm you did the thing" clicks, which are
already counted as C. S (scroll/search) is scored 0 throughout both
columns: this sprint did not touch scrolling behavior or build a
search feature (see "What wasn't done" below), so inflating S in the
BEFORE column just to make the AFTER column look better would be
exactly the "manipulating the metric" the brief prohibits.

| Stage | C before&rarr;after | N before&rarr;after | T before&rarr;after | P before&rarr;after | D before&rarr;after | What changed |
|---|---|---|---|---|---|---|
| 1. Incoming review | 1&rarr;1 | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | unchanged |
| 2. Company K | 2&rarr;2 | 0&rarr;0 | 0&rarr;0 | 1&rarr;1 | 0&rarr;0 | label only |
| 3. Technician determine | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | unchanged |
| 4. Technician dispatch | 4&rarr;4 | 0&rarr;0 | 0&rarr;0 | 1&rarr;1 | 0&rarr;0 | label only |
| 5. Wait for K | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | unchanged (passive) |
| 6. Technician K accept | 2&rarr;1 | 1&rarr;0 | 0&rarr;0 | 0&rarr;0 | 1&rarr;1 | dispatch-confirm auto-selects the job in Active -- the separate "find it in the list" click and screen change are gone |
| 7. Technician update | 2&rarr;2 | 0&rarr;0 | 2&rarr;2 | 0&rarr;0 | 1&rarr;1 | unchanged (Priority 6 "remove unnecessary typing" not attempted -- see below) |
| 8. Company update | 2&rarr;2 | 0&rarr;0 | 0&rarr;0 | 1&rarr;1 | 0&rarr;0 | label only |
| 9. Closing | 4&rarr;4 | 1&rarr;0 | 3&rarr;3 | 1&rarr;1 | 1&rarr;1 | "Go to Closing" replaces the old independent navigate+find-it-in-the-list step with one bundled click -- same click count, the screen change is no longer a separate operator-recalled step |
| 10. Completion | 1&rarr;1 | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | 0&rarr;0 | unchanged |
| **Total** | **18&rarr;17** | **2&rarr;0** | **5&rarr;5** | **4&rarr;4** | **3&rarr;3** | |

### A. Full Scenario A

- BEFORE: C=18, N=2, T=5, P=4, S=0, D=3 &rarr; EFFORT = 18 + 3(2) + 4(5) + 2(4) + 2(0) + 3(3) = 18+6+20+8+0+9 = **61**
- AFTER: C=17, N=0, T=5, P=4, S=0, D=3 &rarr; EFFORT = 17 + 3(0) + 4(5) + 2(4) + 2(0) + 3(3) = 17+0+20+8+0+9 = **54**
- **Reduction: 7 points, 11.5%.**

### B. Closing workflow alone (stage 9 in isolation)

- BEFORE: C=4, N=1, T=3, P=1, S=0, D=1 &rarr; EFFORT = 4 + 3(1) + 4(3) + 2(1) + 2(0) + 3(1) = 4+3+12+2+0+3 = **24**
- AFTER: C=4, N=0, T=3, P=1, S=0, D=1 &rarr; EFFORT = 4 + 0 + 12 + 2 + 0 + 3 = **21**
- **Reduction: 3 points, 12.5%.**

### Honest assessment against the 25% target

Both come in at roughly 11-13%, short of the brief's 25% aspirational
target. Reported as-is, not adjusted -- per the brief's own instruction
("if 25% cannot safely be reached, report the maximum defensible
reduction. Do not manipulate the metric"). Why it landed here:

- **N (navigation) is fully eliminated where it was touched** -- both
  of Scenario A's two screen transitions (stage 6, stage 9) now cost
  zero operator-recalled navigation. N is weighted 3x, so this is
  actually the single highest-leverage change available in this model;
  there were only two navigation points to remove, and both are gone.
- **T (typing, weighted 4x, the heaviest weight in the model) was
  deliberately left untouched.** Priority 6 of the brief ("remove
  unnecessary typing... pre-fill it") was evaluated and not
  implemented: every typed field remaining in Scenario A (the
  technician's raw message paste, the closing amount, the payment
  method) is genuinely new information from outside the system --
  pre-filling any of it would mean fabricating a price, a payment
  method, or a technician's words, which the brief's own absolute
  safety rules prohibit ("fabricate...prices...closing values"). This
  is the correct call, not a missed optimization, but it does cap how
  much this model can move: T alone contributes 20 of the 61 BEFORE
  points.
- **D (decision points) was correctly left unchanged.** The brief
  explicitly warns against "reject[ing] an optimization if it saves
  one click but increases ambiguity" -- none of this sprint's changes
  removed a judgment call the operator used to make, and none should.
- **C (clicks) only dropped by 1** in the full-scenario count, because
  most of the navigation savings showed up as N, not C -- the clicks
  that used to accompany manual navigation (selecting the job in a
  list after arriving) were already counted as part of the *next*
  stage's C in the original baseline, so removing the navigation alone
  doesn't remove a click unless a click is also genuinely eliminated
  (as it was in stage 6).

### What the effort model does not capture

Scenario A's effort model is scoped to **one job moving through the
whole workflow alone**. It does not capture the benefit of Action
Center's new 3-group priority presentation, which is a *multi-job
triage* improvement (finding which of N waiting jobs needs attention
next), not a single-job workflow-step reduction. That benefit is real
-- the fixed key-mismatch bug alone means "Ready to send to technician"
items are now visible at all, where before they silently never
rendered -- but this specific model has no dimension for "queue-scanning
effort across many jobs simultaneously," so it isn't reflected in the
61&rarr;54 number above. A future S (search/scroll)-weighted model
extension, once a real search feature exists, would be the natural
place to capture it.

## What wasn't done this sprint (disclosed, not silently skipped)

- **Priority 6 (remove unnecessary typing):** evaluated per field: none
  of Scenario A's remaining typed fields are safely pre-fillable
  without fabricating data (see above). No change made.
- **Priority 8 (global search)** and its `/` keyboard shortcut: not
  built. A real search feature (backend route + latency measurement +
  frontend UI) is a standalone-sized piece of work; deferred rather
  than shipped shallow. The `/` shortcut is intentionally not bound to
  anything in this build rather than bound to a feature that doesn't
  exist.
- **Priority 11/12 (full responsive/desktop-density pass)** at all 5
  specified viewports: not re-verified this sprint beyond spot-checking
  the screens this sprint actually changed (Action Center, Active
  detail). No layout/CSS density changes were made to Command Center,
  Jobs, or Closings beyond what's described above.
- **Priority 10's remaining batching work** (shadow episode and
  technician-acceptance lookups still run once per job): the redundant
  *duplicate* computation was fixed; converting the remaining per-job
  queries into batched `IN (...)` queries was not attempted, per the
  brief's own "do not prematurely optimize SQLite" guidance combined
  with the real database's current low volume (6 jobs).

---

# Operator Speed Round 2 (Search, Next Actionable Job, Performance Round 2)

Continuation covering the follow-on sprint. Starting point: the round-1
numbers above (Scenario A effort 61&rarr;54, Closing 24&rarr;21, both
short of the 25% target with the reasons given). This round's own
Priority 1 was to re-audit for more *safe* Scenario A friction before
building anything else.

## Priority 1 re-audit: no further safe reduction found in Scenario A itself

Re-scoring each of Scenario A's 10 stages against the current
implementation reproduced the exact same C/N/T/P/S/D counts as round 1
(18&rarr;17 clicks, 2&rarr;0 navigation, 5 typing, 4 copies, 3
decisions, unchanged). The conclusion from round 1 held under
re-examination: every remaining typed field is genuinely new
information (a technician's message, a closing amount, a payment
method) that cannot be safely pre-filled without fabricating data, and
every remaining decision point is a real judgment call the brief
explicitly protects. **This is reported honestly, not padded** -- the
single-job Scenario A model has a real, evidenced floor given this
product's safety constraints, and this round did not lower it further.

What this round's work (search, Next Actionable Job) actually targets
is a *different* dimension the Scenario A model was never built to
measure: moving between jobs in a queue, not moving through one job's
own stages. Round 1's own writeup flagged this exact gap ("a future
S-weighted model extension, once a real search feature exists, would
be the natural place to capture it") -- this round follows through on
it with two new, separately-scored scenarios below, rather than folding
invented numbers into the original Scenario A/Closing scores.

## Scenario A / Closing effort: BEFORE (original) / PRE-ROUND-2 / FINAL

| | C | N | T | P | S | D | EFFORT | vs original (61/24) | vs pre-round-2 (54/21) |
|---|---|---|---|---|---|---|---|---|---|
| Scenario A -- original (HEAD `ad69d01`) | 18 | 2 | 5 | 4 | 0 | 3 | **61** | -- | -- |
| Scenario A -- pre-round-2 (HEAD `3350b38`) | 17 | 0 | 5 | 4 | 0 | 3 | **54** | -11.5% | -- |
| Scenario A -- final (this round) | 17 | 0 | 5 | 4 | 0 | 3 | **54** | -11.5% | **0%** |
| Closing -- original | 4 | 1 | 3 | 1 | 0 | 1 | **24** | -- | -- |
| Closing -- pre-round-2 | 4 | 0 | 3 | 1 | 0 | 1 | **21** | -12.5% | -- |
| Closing -- final (this round) | 4 | 0 | 3 | 1 | 0 | 1 | **21** | -12.5% | **0%** |

**Unchanged this round, honestly.** No new Scenario-A or Closing
friction was found or removed. Manufacturing a change here (e.g. by
reclassifying an existing action's weight, or crediting search for a
step Scenario A doesn't actually take) would be exactly the "manipulate
the metric" the brief prohibits.

## New supplementary scenarios (this round's actual improvements)

These are new, separately-tracked scenarios -- not blended into
Scenario A/Closing above -- using the same weights
(`EFFORT = C + 3N + 4T + 2P + 2S + 3D`).

### Scenario G: find a specific job when its stage is not already known

Realistic case: the operator has a ticket number (e.g. from a phone
call) and doesn't know whether that job is New, Active, or in Closings.

**BEFORE (no search):** guess a screen, scan the list (a real S
operation), guess wrong once, navigate again, scan again, then click.
C=3 (nav to guess 1, nav to guess 2, click job), N=2 (two screen
changes), S=2 (two visual scans), T=0.
EFFORT = 3 + 3(2) + 4(0) + 2(0) + 2(2) + 3(0) = 3+6+0+0+4+0 = **13**

**AFTER (with search):** open search (desktop: already focused via
`/`), type the ticket, click the one result -- which auto-navigates to
wherever the job actually lives (`_job_target_screen`, this round's own
fix ensures this is always correct). C=2 (open/focus + click result),
T=1 (typing the ticket, counted once regardless of character count,
consistent with how every other typed field in this model is scored),
N=0 (the navigation is a side effect of the click, not a separate
operator-recalled action, same convention used for Fast Path's
auto-navigation), S=0.
EFFORT = 2 + 3(0) + 4(1) + 2(0) + 2(0) + 3(0) = 2+0+4+0+0+0 = **6**

**Reduction: 7 points, 53.8%.** Note T (typing, the heaviest weight)
went from 0 to 1 -- search trades a scan-and-guess for a short type,
and still comes out well ahead because eliminating the wrong-screen
guess (N=2, S=2) outweighs adding one typed field.

### Scenario H: move to the next actionable job after finishing one

Realistic case: the operator just marked something sent (or a job
became waiting-only) and needs to know what to do next.

**BEFORE (no Next Actionable Job control):** navigate to Action Center,
visually scan the groups for the top item, click it. C=2 (nav + click),
N=1, S=1 (scanning even a well-organized, pre-sorted list still costs a
visual scan), T=0.
EFFORT = 2 + 3(1) + 4(0) + 2(0) + 2(1) + 3(0) = 2+3+0+0+2+0 = **7**

**AFTER (with Next Actionable Job):** one click, from wherever the
operator currently is (Action Center header button, or the "waiting on
technician" prompt on a job with nothing left to do). C=1, N=0 (the
navigation to the target item's own screen is the click's direct
effect, not a separate step), S=0.
EFFORT = 1 + 0 + 0 + 0 + 0 + 0 = **1**

**Reduction: 6 points, 85.7%.**

## Performance Round 2 (separate from effort modeling, per Part 2's own rule)

Real, measured, in-process route latency -- see the Performance section
above for round 1's numbers and method. Round 2 fixed the actual
algorithmic issue profiling found (an O(n^2) computation, not just
duplicate work) -- see the git commit for the full profiling evidence.

| Route | Round-1 p50 @ 500 jobs | Round-2 p50 @ 500 jobs | vs round 1 | vs original |
|---|---|---|---|---|
| `route_action_center` | 227.60ms | 175.68ms | -22.8% | -33.6% |
| `route_active` | 273.89ms | 212.39ms | -22.5% | -59.6% |
| `route_search` (new) | -- | 14.76-17.81ms | -- | -- |
| `route_job_workspace` | -- | 2.49-2.52ms (flat across 10/100/500) | -- | -- |

`route_search` and `route_job_workspace` were not part of round 1 (search
didn't exist yet; job detail was never implicated in the N+1 finding).
Both stay fast at volume because neither does per-job work across the
whole dataset: search caps its expensive per-result computation at 20
results regardless of how many jobs matched, and job detail only ever
looks at the one requested job.
