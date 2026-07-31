# KPI data readiness

Factual inventory of what the real schema and real data actually support,
produced by direct inspection of `data/dispatcher.db` and `src/dispatcher/storage.py`
on 2026-08-01. No values are guessed; where a field doesn't exist, it's
listed as missing, not assumed.

## Real data volume today (structure/counts only, no content)

| Table | Rows |
|---|---|
| `jobs` | 6 |
| `events` | 8 |
| `technicians` | 15 |
| `shadow_episodes` | 0 |
| `quo_pending_messages` | 510 |

Job status breakdown: `Approved` (1), `Handle Manually` (5). **Zero** jobs
are `Closed`, `In Progress`, `Cancelled`, or `Rejected`. Zero events have a
`closing_amount`. Zero events have a non-null `update_type` (no STATUS
updates like Going/Arrived/Completed have ever been recorded). Zero Shadow
episodes exist at all — the Learning Review page has never had a reviewed
example.

**This is the single most important fact in this document**: the KPI
engine below is built to be correct at any volume, but almost every KPI
that depends on completed jobs, technician timing, closings, or AI review
will report "Insufficient data" against the real database *today*, simply
because the real operational history is this thin. That is the honest,
correct behavior — not a bug in the engine.

## Existing timestamps

- `jobs.created_at` — job row creation (approximates receive time, but see
  "unreliable fields" below).
- `jobs.updated_at`, `jobs.last_event_at` — last mutation / last linked event.
- `events.created_at` — one per event (NEW, STATUS, CLOSING, CANCELLATION,
  ASSIGNMENT, CORRECTION, OPERATIONAL_NOTE, DUPLICATE, UNRECOGNIZED, etc).
- `dispatch_tracking`: `first_seen_at`, `luxor_prepared_at`,
  `acknowledged_at`, `copied_at`, `whatsapp_opened_at`,
  `dispatched_manually_at` — per-pending-message dispatch-workflow timestamps
  (keyed by Quo `message_id`, not `job_id`).
- `shadow_episodes.created_at`, `.updated_at`, `.reviewed_at`.
- `quo_pending_messages.received_at`, `.fetched_at`.

## Existing job statuses (the only valid set — `storage.VALID_JOB_STATUSES`)

`New`, `Needs Review`, `Approved`, `Handle Manually`, `Rejected`,
`In Progress`, `Closed`, `Cancelled`. `ACTIVE_STATUSES = {New, Approved,
In Progress}` (`luxor_ops_shared.py`). There is no separate "Completed"
status string — the UI's "Completed" screen means `status = "Closed"`.

## Technician assignment data

`jobs.assigned_technician_id` (nullable FK), `technicians.name`, `.active`,
`.areas_json`, `.dispatch_group`, `.closing_group`. No technician-level
timestamp table exists — technician timing must be derived from `events`
rows where `job_id` matches and `message_type = 'STATUS'`.

## Source/company fields

`jobs.source` (AGN / Lucky / Main Leads / confirmed real values only — no
other sources have ever appeared in the real table). `events.source`
mirrors it per-event. City/state live inside `jobs.latest_fields_json`
(a JSON blob, not a column) — `city`, `state`, plus `address`,
`customer_name`, `phone`, `second_phone`, `service_type`, `vehicle`,
`zip_code`, `extension`.

## Accepted / pass / cancelled / completed signals

- Accepted/approved: `events.final_action` (`Approve`, `Edit`, `Reject`,
  `Handle Manually`, `Pass`, `Cancel`, ...) alongside `events.message_type`.
  `derive_job_status()` is the **only** place these combine into a status
  — the KPI engine reads the resulting `jobs.status`, it never
  re-implements this state machine.
- Cancelled: `message_type = 'CANCELLATION'` events, or `jobs.status =
  'Cancelled'`.
- Completed: `jobs.status = 'Closed'`.

## Closing amounts and payment

`events.closing_amount`, `.closing_payment_method`, `.closing_parts_amount`,
`.closing_company_share`, `.closing_technician_share` — all `TEXT` columns
(free-form, not guaranteed numeric), all currently `NULL` for every real
event. **No financial formula (commission, company share calculation, net
revenue) is implemented in this phase** — see Part C, "Financial KPIs:
BLOCKED".

## Human-review and AI-correction data

`shadow_episodes.review_status` (`UNREVIEWED` default, `VERIFIED`,
`CORRECTED`, `EXCLUDE`, `SENSITIVE`, `INCOMPLETE`),
`.human_review_required`, `.shadow_suggested_reply` vs
`.dispatcher_actual_reply`, `.shadow_confidence`, `.normalized_intent`.
Currently zero rows — every AI-quality KPI is BLOCKED by sample size, not
by missing schema.

## Missing fields

- No per-job "dispatched_at" or "first_reviewed_at" timestamp on the `jobs`
  table itself — dispatch/review timing must be reconstructed from
  `dispatch_tracking` (keyed by Quo message, not job) or from the first
  `events` row per job, which is an approximation, not a guarantee (a job
  can exist in `jobs` before its first `events` row in edge cases the
  replay/backfill path handles).
- No technician-level "first update" or "en route" timestamp column —
  must be derived from `events.update_type` values (only reliable once
  real STATUS updates start being recorded; currently zero exist).
- No numeric closing-amount column (`closing_amount` is free-text).
- No area/city as a first-class column (lives inside a JSON blob).

## Unreliable fields

- `jobs.created_at` is a reasonable proxy for "job received" but is a job
  row's creation time, not necessarily the same instant the raw message
  arrived — for true receive-time granularity, `quo_pending_messages.received_at`
  or `dispatch_tracking.first_seen_at` (message-level, not job-level) are
  more precise for **new-job** timing; `jobs.created_at` is what's used for
  **post-dispatch** job-level KPIs since it's the only per-job timestamp
  guaranteed to exist.
- `closing_amount` etc. are free-text — "average closing amount" must only
  be computed where a value parses cleanly as a number; non-numeric or
  missing values are excluded and counted separately, never coerced.

## KPI readiness classification (see `docs/kpi_definitions.md` for exact formulas)

| KPI | Status |
|---|---|
| Jobs received | READY |
| New jobs by source | READY |
| New jobs by area | READY (via `latest_fields_json.city`) |
| Jobs dispatched | READY (`status != 'New'`) |
| Dispatch rate | READY |
| Median receive-to-review time | PARTIALLY READY (uses `dispatch_tracking.acknowledged_at`, message-level not job-level) |
| Median/p90 receive-to-dispatch time | PARTIALLY READY (same caveat) |
| Jobs waiting over target | READY |
| Active jobs without a recent update | READY |
| Completed jobs | READY (currently 0 real rows) |
| Cancellation rate | READY |
| Pass/manual-handle rate | READY |
| Closing completion rate | READY (currently 0 real closings) |
| Technician: assigned/accepted/passed/completed counts | READY (currently near-zero real rows) |
| Technician: completion rate | READY |
| Technician: median assignment→first-update / →EN_ROUTE | BLOCKED today (zero real STATUS events with `update_type` set) — engine is READY, data is not |
| Technician: jobs lacking required updates | READY |
| Technician: no-answer/no-show/unavailable counts | NEEDS BUSINESS DEFINITION (no canonical `update_type` value maps to these today — see gap analysis) |
| Technician: closing-submission completeness | READY |
| Technician: average closed amount | BLOCKED (no numeric closing data yet; formula guarded to only average parseable numbers) |
| Source KPIs (received/dispatch/completion/cancellation/median-dispatch/manual-review/missing-info rate) | READY |
| Source: average reliable closing amount | BLOCKED (same reason) |
| AI: human-review-required rate | READY (0 today) |
| AI: suggestion-acceptance / correction rate | BLOCKED by sample size (0 reviewed episodes) — engine READY |
| AI: accuracy by source | BLOCKED by sample size — must show "Insufficient reviewed examples", never a computed percentage from 0 |
| AI: parser-confidence distribution | READY (`events.confidence`) |
| AI: unrecognized-message rate | READY (`message_type = 'UNRECOGNIZED'`) |
| AI: duplicate-message rate | READY (`message_type = 'DUPLICATE'`) |
| Financial: profit/commission/fees/net revenue | BLOCKED — no owner-approved formula exists; not implemented this phase |
