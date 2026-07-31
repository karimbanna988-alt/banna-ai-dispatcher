# KPI definitions

Every KPI below is implemented in `src/dispatcher/kpi_read_model.py`. Formulas
here are the literal source of truth — the code must match this document,
not the other way around.

## General rules (apply to every KPI)

- **Time zone**: all stored timestamps are UTC ISO-8601 (the codebase's
  existing convention). Period boundaries (shift/24h/7d/30d/custom) are
  computed in `America/New_York` local time using Python's `zoneinfo`
  (DST-correct), then converted to UTC before querying — never a fixed
  UTC offset.
- **Current shift**: no shift schedule exists yet. Default = local
  midnight-to-now in `America/New_York`. Documented default only —
  configurable once the owner defines real shift hours
  (`rules/kpi_targets.yaml`).
- **Missing data**: a KPI never silently becomes `0`. Every result carries
  a `data_quality` field: `ok`, `zero` (a real, computed zero), `insufficient_data`
  (below minimum sample size), or `needs_business_definition` (no reliable
  source field exists yet). `zero` and `insufficient_data` are never
  conflated.
- **Minimum sample size**: default 5 (config: `rules/kpi_targets.yaml`,
  `min_sample_size`). Below it, `value` is `null` and `data_quality` is
  `insufficient_data`.
- **Median/percentile**: computed in Python (not SQL) on a sorted list of
  finite durations, using linear interpolation (same method as
  `statistics.median`/`numpy` "linear" percentile) — exact, not
  approximated.
- **Division by zero**: any KPI whose denominator is 0 returns `data_quality
  = insufficient_data`, never a computed `0%` or `100%`.
- Every result includes: `value`, `numerator`, `denominator`, `sample_size`,
  `data_quality`, `explanation` (one-sentence plain English).

## CORE OPERATIONS KPIs

1. **Jobs received** — `COUNT(jobs)` where `jobs.created_at` falls in the
   period. Filters: source, area (`json_extract(latest_fields_json,'$.city')`).
2. **New jobs by source** — (1), grouped by `jobs.source` (real distinct
   values only, never a hardcoded list).
3. **New jobs by area** — (1), grouped by city.
4. **Jobs dispatched** — `COUNT(jobs)` where `created_at` in period AND
   `status IN ('Approved','In Progress','Closed')`. *Known limitation*:
   this is a business-language approximation — "dispatched" means "reached
   or passed the point a technician was sent," using the same `Approved`
   status `route_confirm_dispatch` already sets; it does not have its own
   dedicated timestamp/column.
5. **Dispatch rate** — (4) / (1).
6. **Median receive-to-review time** — median of (`events.created_at` of
   each job's first event) − `jobs.created_at`, jobs with ≥1 event only.
   *Limitation*: job-level, not raw-message-level — time spent in the
   pending-message queue before a job row exists isn't captured here.
7. **Median receive-to-dispatch time** — median of (`events.created_at` of
   the event whose `final_action IN ('Approve','Edit')` and
   `message_type='NEW'`) − `jobs.created_at`.
8. **90th-percentile receive-to-dispatch time** — same distribution as (7),
   p90.
9. **Jobs waiting over target** — `COUNT(jobs)` where `status IN ('New','Needs
   Review')` AND `(now − created_at) > dispatch_target_minutes` (config
   default: 30 minutes).
10. **Active jobs without a recent update** — `COUNT(jobs)` where `status IN
    ('Approved','In Progress')` AND `(now − last_event_at) > stale_target_minutes`
    (config default: 120 minutes).
11. **Completed jobs** — `COUNT(jobs)` where `status='Closed'` AND
    `updated_at` in period.
12. **Cancellation rate** — `COUNT(status='Cancelled')` / (1).
13. **Pass/manual-handle rate** — `COUNT(status='Handle Manually')` / (1).
14. **Closing completion rate** — `COUNT(status='Closed')` / (4) (of jobs
    that reached dispatch, how many closed).

## TECHNICIAN KPIs (same period/source/area filters, plus `technician_id`)

Scoped to `jobs.assigned_technician_id`. All formulas mirror the
core-operations equivalents, computed per technician:

1. Jobs assigned — `COUNT(jobs)` where `assigned_technician_id = t` and
   `created_at` in period.
2. Jobs accepted/progressed — `status IN ('In Progress','Closed')`.
3. Jobs passed — `status = 'Handle Manually'` for that technician's jobs
   (only meaningful once assignment happens before pass — documented as a
   coarse proxy).
4. Jobs completed — `status = 'Closed'`.
5. Completion rate — (4) / (1).
6. Median assignment→first-update — median(first `events.created_at` with
   `message_type='STATUS'` for the job) − (job's assignment event time).
7. Median assignment→EN_ROUTE — same, filtered to
   `events.update_type` matching the technician's "en route"/"going" value.
8. Jobs lacking required updates — assigned jobs with zero STATUS events.
9. **No-answer/no-show/unavailable counts — NEEDS BUSINESS DEFINITION.**
   No `update_type` value in the real schema maps reliably to these
   concepts today. Not implemented; the read model returns
   `data_quality = needs_business_definition` rather than guessing which
   `update_type` strings might mean this.
10. Closing-submission completeness — assigned+closed jobs with a non-null,
    non-empty `closing_amount` and `closing_payment_method` / (4).
11. Average closed amount — mean of `closing_amount` values that parse as a
    plain number (after stripping `$`/commas); non-numeric or missing
    values are excluded and their count is reported separately, never
    coerced to 0. **BLOCKED today** — zero real numeric closings exist.

## SOURCE KPIs

For every real, distinct value of `jobs.source` (discovered from the data,
never hardcoded): items 1–6 mirror core-operations formulas (1),(5),(11) style
scoped by source; item 7 ("average reliable closing amount") mirrors the
technician version's parsing rule; item 8 ("missing-information rate") =
jobs where any of `phone`/`address`/`service_type` is null/empty in
`latest_fields_json`, divided by jobs received for that source.

## AI QUALITY KPIs (from `shadow_episodes`, reviewed rows only unless noted)

1. Human-review-required rate — `COUNT(human_review_required=1)` /
   `COUNT(*)` (all episodes, reviewed or not — this reflects the AI's own
   self-flagging, not human judgment).
2. AI suggestion acceptance rate — `COUNT(review_status='VERIFIED')` /
   `COUNT(review_status != 'UNREVIEWED')`. **Never computed against
   unreviewed episodes** — below `min_sample_size` reviewed episodes,
   returns `insufficient_data` with the literal explanation "Insufficient
   reviewed examples."
3. Correction rate — `COUNT(review_status='CORRECTED')` / reviewed count.
4. Excluded/sensitive/incomplete count — raw counts of
   `review_status IN ('EXCLUDE','SENSITIVE','INCOMPLETE')`, always shown
   (counts are safe even at low volume; only *rates* need the sample-size
   guard).
5. Accuracy by source — (2), grouped by `shadow_episodes.source`, same
   sample-size guard applied per source independently.
6. Parser-confidence distribution — histogram of `events.confidence`
   (buckets: 0–39 / 40–69 / 70–89 / 90–100).
7. Unrecognized-message rate — `COUNT(message_type='UNRECOGNIZED')` /
   `COUNT(*)` events in period.
8. Duplicate-message rate — `COUNT(message_type='DUPLICATE')` / `COUNT(*)`
   events in period.

## FINANCIAL KPIs — BLOCKED

Company profit, technician commission, fees, balance, net revenue: **not
implemented**. No owner-approved formula exists for how
`closing_amount`/`closing_company_share`/`closing_technician_share` should
combine into any of these figures, and those fields are free-text and
almost entirely empty in real data today. The read model exposes zero
functions for these. This must remain BLOCKED until the owner supplies
exact, written formulas.
