# Technician Settlement Report — Requirements (Not Yet Built)

Status: **Reports is intentionally hidden from operator navigation.** The
screen and its backend route (`GET /api/reports`) still exist and still
work — they are not deleted — but they are not linked from Jobs/Active/
Closings/Completed or the Settings menu, because they currently show
too little to be useful for a real settlement decision, and this
document has not yet been approved.

## Why it was hidden

The existing Reports screen only ever showed 8 columns (job, company,
date, technician, area, quoted amount, final amount, payment method) —
all of it either already visible elsewhere (Closings, Completed) or too
thin to answer the actual question a boss or dispatcher asks a
"report" for: *what does each technician owe or get paid, this
settlement period?* Nothing in this codebase computes that today, and
guessing at the formula would mean showing invented numbers on a
boss-facing screen — exactly what this phase's "real data only" rule
forbids.

## What a real technician-settlement report needs

Every field below needs two things before it can be built: (1) an
approved, deterministic formula or an approved manual-entry field, and
(2) a real column to persist it in. Neither exists yet for most of
these.

| Field | Currently exists? | Where | Notes |
|---|---|---|---|
| Job / ticket | Yes | `jobs.ticket_number` | |
| Company / source | Yes | `jobs.source` | |
| Date | Yes | `jobs.last_event_at` / `events.created_at` | |
| Technician | Yes | `jobs.assigned_technician_id` → `technicians.name` | |
| Area / address | Yes | `jobs.latest_fields_json` (city/state) | Full street address also present but masked on display |
| Quoted amount | Yes | Shadow `commercial_update.quote_amount` | Only present if a commercial update was recorded via Active; fixed vs. starting is preserved (`quote_type`) |
| Final (closing) amount | Yes | `events.closing_amount` | Free-text/verbatim, not validated as numeric |
| Payment method | Yes | `events.closing_payment_method` | Free-text (e.g. "Cash"), not broken into cash/card/check amounts |
| Parts amount | Yes | `events.closing_parts_amount` | Single free-text field, not split technician-supplied vs. company-supplied |
| **Technician percentage** | **No** | — | No column, no approved formula. Is it per-technician, per-company, or per-job-type? Not specified anywhere in this codebase. |
| **Total** | **Partially** | derivable from `closing_amount` if numeric | `closing_amount` is free text (e.g. "$125" or "125 cash, customer had a coupon") — not guaranteed parseable as a number today |
| **Company parts** (vs. technician-supplied parts) | **No** | — | `closing_parts_amount` does not distinguish who supplied the parts |
| **Cash / Card / Check breakdown** | **No** | — | Only one `closing_payment_method` string exists; no itemized split when a job is paid partly in more than one method |
| **Fees** | **No** | — | No column anywhere in the schema |
| **Additional fees** | **No** | — | No column anywhere in the schema |
| **Technician profit** | **No** | — | Requires the percentage formula above; not computable without it |
| **Balance** | **No** | — | Requires knowing what "balance" means here (amount still owed to the technician? to the company? outstanding from the customer?) — not specified |
| **Settlement status** | **Partially** | `jobs.reconciliation_state` (`Settled` / not) | Binary today, not a full settlement workflow (no partial-settlement state, no settlement date, no settling actor) |

## What this document is not

This is not a proposal for what the formulas *should* be — that is a
real business decision (this business's actual technician-percentage
agreements, fee structure, and payment-splitting conventions) that only
the user can supply. Once those rules are provided:

1. The exact formula for technician percentage/profit/balance needs to
   be written down and approved (ideally per-technician or per-company,
   whichever actually reflects how this business pays people today).
2. New columns need to be added via the existing migration path
   (`scripts/manage_schema.py`, the same audited pattern every other
   schema change in this project already uses) — never inferred or
   back-filled from free text.
3. `closing_amount`/`closing_payment_method`/`closing_parts_amount`
   likely need to become structured (numeric amount + enumerated
   payment type, with an itemized cash/card/check split) instead of
   free text, which is itself a real, visible workflow change to the
   Closings screen's "verify final amount / verify payment method"
   steps — not just a reporting change.
4. Only after 1–3 are approved and tested should Reports be re-added to
   operator navigation, with real computed numbers behind it.

Until then, Reports stays exactly where it is: present in the code,
absent from the menu, and never populated with an invented percentage,
profit, or balance.
