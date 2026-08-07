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

**Finding (evidenced, not fixed this pass):** both routes scale
roughly linearly with job count, consistent with a per-job query loop
rather than a single batched query. `route_active`
(`operator_api.py:564-592`) calls `storage.get_shadow_episode_for_job`,
`_active_next_action_label`, `_company_update_owed_dto`,
`_technician_k_dto`, and `_workflow_projection_dto` once *per job* inside
its `for j in jobs:` loop -- each of those does at least one additional
SQL query, so the route issues on the order of 5N+1 queries for N active
jobs. `route_action_center` composes several similar per-item lookups
across its 10 sections. At the real database's current volume (6 jobs)
this is invisible (low single-digit ms); at 500 synthetic jobs it crosses
half a second. This is flagged per Part 16's instruction to report
measured/evidenced problems, but **not fixed this pass** -- batching
these into fewer queries (e.g. one `IN (...)` lookup per lookup type
instead of N separate ones) is a real, scoped, future performance task,
not attempted here to avoid rushed changes to read-model code under a
"do not add unnecessary work" directive when the real-world volume
(single digits of jobs) does not yet make this an active problem.
