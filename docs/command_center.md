# Command Center

Phase 6B. Read this alongside `docs/kpi_definitions.md` (the existing
Performance-screen KPI engine, which Command Center reuses wherever the
question is the same) and `docs/kpi_data_readiness.md`.

## Audit: what "Older unresolved jobs (N)" actually meant

Traced end to end before any code changed (see the git history for the
full trace). Three separate, non-overlapping data stores exist:

| Store | Table | What it holds | Real count at audit time |
|---|---|---|---|
| A. Quo inbound queue | `quo_pending_messages` | Every raw inbound message not yet actioned | 858 pending |
| B. Real committed jobs | `jobs` | Jobs a dispatcher actually approved into existence | 6 |
| C. WhatsApp bridge queue | in-memory (`whatsapp_bridge_adapter.py`) | Manually-relayed technician replies awaiting attach/ignore | not persisted |

"Older unresolved jobs", the Jobs-screen count badge, and the "live
jobs" figure were all computed from **Store A** (raw messages), not
Store B (real jobs) -- via `luxor_desk_read_model.get_live_dispatch_queue()`
-> `luxor_ops_shared.fetch_pending_messages(limit=100)`. That `limit=100`
is silent: of the 858 real pending rows, only the newest 100 were ever
visible to this screen, with no indication anywhere in the UI that a cap
existed. Of that capped 100, most non-`NEW` traffic (status updates,
closings, corrections) was silently dropped by `shows_job_transformation()`
because it couldn't correlate to one of only 6 real jobs.

Separately, `kpi_read_model.py` (deterministic, tested, used only by the
Performance screen) reads exclusively from the real `jobs` table and was
completely disconnected from the Jobs screen's pipeline -- Jobs and
Performance could show unrelated "how many jobs" numbers from the same
database at the same instant.

**Fix applied** (Part A/G): the Jobs screen's "Older unresolved jobs"
section is relabeled "Archive / Needs Review" and its copy now states
plainly that these are raw incoming messages pending triage, not
confirmed jobs -- with an honest note when the 100-row cap means the
real backlog (`total_pending_messages`, a new, real, uncapped
`COUNT(*)` exposed by `route_new_jobs`) is larger than what's shown.
The "Recent unresolved jobs" fallback section is relabeled "Needs
review". The Sync button's message now reads "Last Quo sync: 5:42 PM ·
3 new job messages" instead of "Synced -- 3 new message(s)." No backend
classification logic changed -- this was a labeling and honesty fix,
not a pipeline rewrite (per the phase's explicit "do not rewrite
backend systems merely for visual consistency" instruction).

## Command Center definitions

The real `jobs.status` enum is `New / Needs Review / Approved / Handle
Manually / Rejected / In Progress / Closed / Cancelled` -- there is no
`NEW`/`ASSIGNED`/`EN_ROUTE`/`COMPLETED` enum anywhere in this codebase.
Every Command Center concept below maps onto these real values, reusing
an existing kpi_read_model function wherever one already answers the
same question:

| Concept | Definition | Function |
|---|---|---|
| RECENT NEW | `status='New'`, received within the window (60 min for the KPI card) | `new_jobs_recent()` (new) |
| WAITING | `status IN ('New','Needs Review')` older than `dispatch_target_minutes` (30 min default) | `jobs_waiting_over_target()` (existing) |
| ACTIVE (with technician) | `status IN ('Approved','In Progress')` AND `assigned_technician_id IS NOT NULL` | `active_with_technician_count()` (new) -- deliberately distinct from the Performance screen's `active_jobs_count()`, which also includes not-yet-dispatched New jobs and doesn't require a technician; these answer different questions and must not converge |
| EN ROUTE | Not trackable -- no `update_type` value in the real schema reliably distinguishes "en route" from any other technician update. Not shown as its own KPI card; jobs that might be "en route" show simply as "In Progress" | `en_route_count()`, returns `data_quality=needs_business_definition`, same posture as the existing `no_answer_no_show_counts` -- never wired into the dashboard |
| IN PROGRESS | `status='In Progress'` | `in_progress_count()` |
| CLOSING DUE | `status='In Progress'` OR (`status='Closed'` AND `reconciliation_state != 'Settled'`) -- the same population `luxor_desk_read_model.get_closing_candidates()` already surfaces on the real Closings screen (`operator_api.route_closings`). Not identical to IN PROGRESS: a Closed-but-unsettled job counts here but not there | `closings_due_count()` |
| COMPLETED | `status='Closed'`, today | `completed_jobs()` (existing) |
| ARCHIVE / NEEDS REVIEW | Store A backlog (raw pending messages) | Jobs screen's "Archive / Needs Review" disclosure |

**Stage badges** on the Active Jobs board (Assigned / Waiting for
technician / Closing needed) come from `_active_next_action_label()`,
the exact same deterministic label already shown on the Active screen
-- not a new classification. "En route" is not shown as a distinct
badge for the same reason it's not a KPI: no reliable signal exists in
real data. Jobs that might colloquially be "en route" show as
"Assigned" today.

## KPIs (Part C)

All six top cards plus "Verified closed amount today" come from
`kpi_read_model.get_command_center()`:

1. **New jobs -- last 60 min**: `new_jobs_recent()`.
2. **Waiting for assignment**: `jobs_waiting_over_target()`.
3. **Active with technician**: `active_with_technician_count()`.
4. **In progress**: `in_progress_count()` -- `status='In Progress'` only. Not labeled "en route": no real signal distinguishes that from any other In Progress job (`en_route_count()` documents this and is intentionally never wired in here).
5. **Closings due**: `closings_due_count()` -- the real Closings-workspace population (In Progress, plus Closed-but-unsettled). Deliberately independent from "In progress" above, not a duplicate of it.
6. **Completed today**: `completed_jobs()`, scoped to today (`America/New_York`, via the existing `resolve_period("shift", ...)` -- shift start defaults to local midnight, i.e. "today so far").
7. **Verified closed amount today**: `verified_closed_amount()` (new) -- sums only `events.closing_amount` values that parse as a plain number (same `$`/comma-stripping rule as the existing `average_closed_amount` KPI). If any closed-today job lacks a reliable numeric amount, the card shows "Closing data incomplete" instead of a partial/misleading total. Zero closings today is a real, distinct zero (`$0.00`), never conflated with "incomplete".

"Every KPI links to the filtered relevant jobs" (Part C): each KPI card
navigates to the existing screen that shows that population (Jobs,
Active, or Closings) -- there is no query-param-level filtered deep
link anywhere else in this app either, so this matches the app's
existing navigation model rather than inventing a new one.

## Attention now (Part D)

`kpi_read_model.attention_now()` (existing, unchanged, still powers
Performance), enriched with `age_minutes` by a new, purely additive
`_attention_now_with_age()` wrapper local to Command Center -- this
does not change `attention_now()`'s own return shape, so the Performance
screen and its tests are unaffected. `operator_api.route_command_center`
merges in real WhatsApp observations (`whatsapp_bridge_adapter.list_pending()`,
the same in-memory queue the Active screen already reads) up to a total
of 5 items. No new attention-detection logic was written; this is
composition only.

## Charts (Part F)

Native SVG/CSS only, no chart library:

1. **Job source mix -- today**: `new_jobs_by_source()` (existing), rendered as an SVG donut.
2. **Job pipeline -- today**: `job_pipeline_counts()` -- mutually-exclusive snapshot across New/Waiting/Approved-Assigned/In Progress (all current-state, matching the snapshot semantics of the KPIs they're built from) plus Closed/Cancelled (scoped to today). Uses `in_progress_count()`, not `closings_due_count()`, for the In Progress bucket -- the latter's broader Closed-but-unsettled jobs would otherwise double-count against the Closed bucket. A footnote explains that "en route" jobs show simply as In Progress.
3. **Technician activity -- today**: `technicians_summary()` (existing), filtered to technicians with real activity this period, sorted alphabetically (not by volume -- never a leaderboard).
4. **Verified closings -- today**: `verified_closed_amount()` -- same function as the KPI card. Shows an honest "insufficient data" state instead of a chart when any closing lacks a reliable amount, per the phase's explicit instruction not to show an empty/misleading chart.

## Safety (Part I)

- Every metric is a GET-only, read-only SQL query; `route_command_center`
  never writes anything.
- `kpi_read_model.py` stays SQL-only (jobs/events/shadow_episodes) --
  the one thing it can't include by its own design (a real WhatsApp
  observation) is merged in at the `operator_api.py` route layer instead,
  matching how `route_active` already does the same merge.
- No duplicate KPI math exists in JavaScript -- `app.js`'s Command
  Center functions only format fields already present on the API
  response.
- Verified via live browser check against a throwaway demo server
  (separate port, temporary on-disk SQLite DB, test-only password,
  `--no-bridge-adapter` to avoid colliding with the live pilot's real
  adapter) that the real `data/dispatcher.db` job count (6) and pending
  count (858) were unchanged before and after this phase, and that the
  live WhatsApp bridge/adapter (ports 8513/8514) stayed up and untouched
  throughout.
