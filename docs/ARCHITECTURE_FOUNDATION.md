# Architecture Foundation

Status as actually implemented at commit range starting `4befff9`
(Active Jobs / Closings / Completed-History) through this hardening
pass. Nothing in this document is aspirational — every module, table,
and test named here exists in the repository and is exercised by
`pytest`.

## Why this pass exists

The operational workflow (Quo job → acknowledgment → LUXOR technician
message → manual WhatsApp dispatch → technician update → company update
→ closing → completion → audit history) was already fully built and
tested. What was missing was a **boundary**: `luxor_desk_pages/*.py`
called `dispatcher.storage` functions directly, several at a time, from
inside button-click handlers. That worked, but it meant every future
integration point (a real Quo send, a real WhatsApp API, an AI review
suggestion, a CLI tool, batch replay of history) would have had to
either duplicate that button-click orchestration or reach back into the
Streamlit layer to reuse it. This pass inserts one layer so that stops
being true, without changing what the system does today.

## Canonical operational workflow (unchanged)

```
Quo job (real, polled read-only)
  -> acknowledgment preparation ("K" for AGN/Lucky; none for Main Leads)
  -> LUXOR technician message (formatter.py, deterministic)
  -> manual WhatsApp dispatch (dispatcher copies text, sends it themselves,
     confirms explicitly -- app never sends)
  -> technician update (dispatcher pastes real WhatsApp reply, picks a
     type: Going/ETA/No Answer/No Show/Not Available/Customer Cancelled/
     Completed/Custom)
  -> company update (deterministic reply for known types; dispatcher
     types their own for Custom; dispatcher sends it themselves)
  -> closing (dispatcher pastes real technician closing; only supplied
     values are stored)
  -> company closing (existing per-company template; dispatcher sends it
     themselves)
  -> completion (dispatcher confirms; job moves to Completed / History)
  -> audit history (full chronological, read-only)
```

Every arrow above is a human action the application records. None of
the arrows are automated by this system.

## Layering

```
 Streamlit pages (luxor_desk_pages/*.py, luxor_desk_shared.py)
              |
              |  calls DispatcherService methods only for WRITES
              |  (reads may still call the read model directly --
              |   see "Command vs. query" below)
              v
   dispatcher.application.DispatcherService   <- NEW this pass
              |
              |  orchestrates, persists nothing itself
              v
  +-----------+-----------+-----------+-----------+
  |           |           |           |           |
policy.py  source_    closing.py  updates.py  storage.py
           adapters.py                        (single source
  (NEW,                                        of truth: SQL,
   composes                                    derive_job_status(),
   policy+                                     transactions)
   companies)
```

`DispatcherService` (in `src/dispatcher/application.py`) is the one
place a UI button handler turns into a business transaction. It never
writes SQL itself (`tests/test_application_service.py::
test_application_layer_contains_no_sql` enforces this by grepping the
file) and never re-derives a job status
(`test_no_competing_status_derivation_in_application_layer` enforces
this the same way). Every write ends in a call to an existing
`storage.py` function; `storage.derive_job_status()` remains the single
status authority, unchanged in its core branching, only extended with
two new recognized inputs (`OPERATIONAL_NOTE` message type, `Pass`/
`Cancel` final actions on `CANCELLATION`) — see `docs/EVENT_CONTRACT.md`.

## Command vs. query separation

State-changing commands (`record_technician_update`, `record_closing`,
`complete_job`, …) always go through `DispatcherService`. Read-only
queries have two tiers:

- **Pure, no-framework reads** — `get_job_workspace()` and
  `get_job_history()` are direct pass-throughs to `storage.py` and have
  zero dependency on Streamlit or the repo-root read model. Safe to
  call from a test, a CLI tool, or a future API adapter with only
  `src/` on the path.
- **`get_queue()`** — the one read convenience method with a soft,
  disclosed dependency on the existing repo-root read model
  (`luxor_desk_read_model.py`), because that module already owns the
  New Jobs live-queue canonicalization/dedup logic and reimplementing
  it a second time was explicitly out of scope ("do not duplicate
  storage logic"). Pages that only need active/closings/completed
  queues, or the New Jobs live queue, still work exactly as before —
  this pass changed nothing about what those pages display.

Rendering a page (including opening/expanding/refreshing it) never
writes to any operational table — see `tests/test_read_model_purity.py`,
which renders all six `luxor_desk_pages/*.py` files twice each and
diffs `events`/`jobs`/`quo_pending_messages`/`dispatch_tracking`/
`whatsapp_handoffs`/`quo_outbound_log`/`console_activation_state`
byte-for-byte before and after.

## What did NOT change

- The SQLite schema's core tables (`jobs`, `events`, `technicians`,
  `quo_pending_messages`, …) — only additive columns/indexes (see
  `docs/EVENT_CONTRACT.md`).
- `parser.py`, `formatter.py`, `closing.py`'s template rendering,
  `policy.py`'s policy table, `derive_job_status()`'s core branching.
- The Streamlit page files' visible structure, wording, button labels,
  or navigation — only their internal call sequence (many direct
  `storage.*` calls → one `DispatcherService` call).
- Quo outbound remains disabled by default; `_send_live()` remains
  unimplemented; WhatsApp has no programmatic send anywhere.

## What remains manual

Every "prepare" and "confirm sent" step still requires the dispatcher
to actually paste into WhatsApp/Quo themselves. Technician update types
are dispatcher-selected, never inferred. See
`docs/FUTURE_INTEGRATION_BOUNDARIES.md` for what would need explicit,
separate approval before any of this could become less manual.

## What must never be automated without explicit approval

- Sending a real Quo message (`QuoOutboundAdapter.send_acknowledgment()`
  routes through the same disabled-by-default `outbound.py` boundary
  that existed before this pass — no new send path was added).
- Sending a real WhatsApp message (no adapter in this codebase has a
  send capability at all).
- Technician selection, availability, pricing, or completion status —
  `ReviewInterpreter` implementations are structurally forbidden from
  writing to storage or claiming any of these (see
  `docs/FUTURE_INTEGRATION_BOUNDARIES.md`).
