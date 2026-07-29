# Audit Event Contract

`events` (SQLite, `src/dispatcher/storage.py`) is the one append-only
audit log for the entire system. This document defines what a caller
can rely on being present, and how it evolves without breaking old
rows.

## What each event answers

| Question | Column(s) |
|---|---|
| What happened | `message_type`, `final_action` |
| Which job | `job_id`, `ticket_number` |
| Which source | `source` |
| Who/what initiated it | `channel` ("manual" for every current write path — see below) |
| Previous → resulting status | Not stored per-row (see "Why status isn't duplicated on every event" below) |
| Original input fingerprint | `raw_message` (verbatim, masked only at display time, never at rest) |
| Prepared output fingerprint | `luxor_message` (the prepared/sent company-facing text, when applicable) |
| Timestamp | `created_at` (UTC ISO 8601) |
| Manual vs. automatic origin | `channel` |
| Whether human review was required | Not a column — derived at read time from `message_type`/`final_action` (e.g. `final_action IS NULL` on a `STATUS` row means an unconfirmed Custom update) |
| Idempotency key | `idempotency_key` (nullable — see below) |
| Schema version | `schema_version` |

## `channel`

Existing column, predates this pass. Every write path added in this
pass uses `"manual"` — there is no automated writer anywhere in this
codebase. `"simulator"` and `"shadow_mode_quo_live"` are older values
from earlier phases (fake-inbox testing, read-only Quo shadow mode);
still valid, unrelated to this pass.

## Why status isn't duplicated on every event

`jobs.status` is the single current-status cache; `events` doesn't
repeat "previous status" per row because `storage.derive_job_status()`
is a pure function of `(previous_status, message_type, final_action)` —
replaying the full event history in order through that function
reproduces every intermediate status exactly (this is literally how
`_add_job_status_columns_and_backfill()`'s one-time backfill works, and
what `tests/test_job_operations.py::test_full_chronological_audit_history`
checks). Storing a redundant status snapshot per row would be a second
place that value could drift from the derived truth.

## `idempotency_key` and `schema_version`

Added this pass (`storage._add_idempotency_and_schema_version_support`,
additive `ALTER TABLE`, same idempotent-migration pattern as every
other schema change in this file).

- **`idempotency_key`** (`TEXT`, nullable): when supplied by a caller
  (always via `DispatcherService`, computed by
  `application.compute_idempotency_key(identity, action,
  fingerprint_text)`), a second write with the same key is a no-op —
  the existing event's id is returned, no new row, no status
  re-derivation. A `UNIQUE` index covers only non-`NULL` keys
  (`WHERE idempotency_key IS NOT NULL`), so every event written before
  this migration (and every future call site that doesn't pass a key)
  is completely unaffected — SQLite never treats two `NULL`s as
  colliding.
- **`schema_version`** (`INTEGER NOT NULL DEFAULT 1`,
  `storage.EVENT_SCHEMA_VERSION`): bumped only when the *shape* of an
  events row changes in a way future code needs to distinguish from
  older rows — not for every additive, backward-compatible column
  (those need no bump; old readers already tolerate an extra `NULL`
  column). No version bump has happened yet; every row in the system
  today is version 1.

## Distinct lifecycle events, never collapsed by ticket alone

A ticket's original new-job message, a later correction, a
cancellation, a technician update, a company update confirmation, a
closing, and a completion are all separate `events` rows with distinct
`message_type`/`final_action` combinations — see
`luxor_desk_read_model.canonicalize()`'s own docstring for the New Jobs
queue's dedup rule (keyed on `(source, ticket, message_type, content
fingerprint)`, never `(source, ticket)` alone), and
`tests/test_application_service.py::test_distinct_technician_updates_both_recorded_not_collapsed`
for the operational-event equivalent.

## `message_type` values relevant to this pass

`OPERATIONAL_NOTE` (new, added alongside the operational-actions layer
in the previous round) — administrative confirmations
(prepared/sent-manually/completed) that always preserve the job's
current status; see `derive_job_status()`'s dedicated branch for it.
Every other `MessageType` value predates this pass, unchanged.

## `final_action` values relevant to this pass

Centralized in `src/dispatcher/action_names.py::ActionName` — see that
module for the full, authoritative list and
`docs/ARCHITECTURE_FOUNDATION.md` for why this exists. The five new
`OPERATIONAL_NOTE`-associated values (`Company Update Prepared`,
`Company Update Sent Manually`, `Company Closing Prepared`, `Company
Closing Sent Manually`, `Job Completed`) and the two `CANCELLATION`
values recognized this pass (`Pass`, `Cancel`) are the complete set of
literal additions.

**Known remaining debt**: `storage._apply_assignment_decision()` (the
technician-assignment write path) still spells `"Assign"`/`"Reassign"`/
`"Remove"`/`"Handle Manually"` as raw literals rather than
`ActionName.*` — `ActionName` already has the corresponding members
(`ASSIGN`/`REASSIGN`/`REMOVE`/`HANDLE_MANUALLY`) for a future pass to
adopt without a design change. Not touched this round to keep the
diff on that already-stable, separately-tested function minimal.

## No secrets, no full-message duplication

`raw_message` stores the technician's/company's actual text (masked
only for on-screen display via `luxor_ops_shared.mask_sensitive()`,
never at rest) — this predates this pass and is unchanged. No API key,
password, or token has ever been a candidate column here; nothing in
this pass adds one.
