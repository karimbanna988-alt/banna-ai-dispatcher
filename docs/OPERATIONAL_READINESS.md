# Operational Readiness

Factual status as of this hardening round. **This system is not
declared production-ready or autonomous anywhere in this document or
this codebase.** Every claim below is backed by a named, executable
test.

## What is implemented

- Explicit schema lifecycle: `check`/`migrate`/`verify` (`scripts/manage_schema.py`),
  separate from normal application connections. See `docs/SCHEMA_MIGRATION_RUNBOOK.md`.
- Idempotency on every state-changing `DispatcherService` command,
  verified under real two-thread concurrency, not just sequential
  retries. See `docs/IDEMPOTENCY_MATRIX.md`.
- Transactional writes with fault-injection coverage (6 induced-failure
  scenarios, all leaving the database in its pre-command state).
- A documented, deliberate SQLite connection policy (`busy_timeout`,
  rollback journal retained, reasoning recorded). See
  `docs/SQLITE_CONNECTION_POLICY.md`.
- A clock seam (`SystemClock`/`FixedClock`) backing every `_now()` call
  in `storage.py`, so tests never depend on wall-clock timing.
- Enforced dependency direction (UI → application → domain → storage),
  checked by AST-based tests, not convention alone. `application.py`
  imports no Streamlit, no `luxor_desk_*` module.
- SQLite-aware backup/verify/restore tooling with an executed restore
  drill (against fabricated data). See `docs/BACKUP_RESTORE_RUNBOOK.md`.
- A read-only, no-PII operational health snapshot
  (`dispatcher.health.get_operational_health()`) and a strengthened,
  still read-only configuration validator with a blank-template
  exporter.
- A synthetic end-to-end lifecycle harness covering AGN, Lucky, Main
  Leads, cancellation, ambiguous updates, closing-without-dispatch, and
  a genuine process-restart mid-lifecycle.

## What was verified this round (not just claimed)

See `docs/ARCHITECTURE_VERIFICATION_131ABCE.md` for the full claim-by-
claim table. Headline: the prior round's claim that a pending migration
"will apply automatically on first login" was **wrong in a more serious
way than stated** — that page load would have crashed outright, not
migrated cleanly. Found and fixed this round.

## Test totals

- Full suite: 818 passed (up from 722 at the start of this round — 96
  new focused tests: migration, idempotency, concurrency, fault
  injection, clock/dependency boundaries, backup/restore, health,
  security/redaction, E2E lifecycle, schema-guard UI).
- Every new test uses a temporary or in-memory SQLite database. None
  loads the real `.env`. None makes a real network call. None sends a
  real Quo or WhatsApp message.

## What remains manual

Identical to every prior round: every "prepare"/"confirm sent" step
requires the dispatcher to actually paste into WhatsApp/Quo themselves.
Technician update types are dispatcher-selected, never inferred.
Applying the pending schema migration to the real database is a manual
step (see `docs/SCHEMA_MIGRATION_RUNBOOK.md`) — not automated by
design.

## What remains externally blocked

WhatsApp dispatch for every real job, until at least one technician
gets a `dispatch_group` configured (surfaced as an ERROR by
`scripts/validate_configuration.py`, unchanged from the prior round's
disclosure). No real Quo or WhatsApp send capability exists in this
build regardless of configuration.

## Architectural debt remaining (disclosed, not hidden)

- `src/dispatcher/quo_ingest.py` and `simulator.py` still embed
  Streamlit render functions directly (pre-existing, predates
  `DispatcherService`) — excluded by name from the dependency-boundary
  sweep with the reasoning recorded in that test file and in
  `docs/ARCHITECTURE_VERIFICATION_131ABCE.md`.
- Poller-ingestion-racing-dispatcher-action and configuration-edit-
  racing-dispatch-preparation concurrency scenarios were not tested
  this round (lower risk than the five scenarios that were; see
  `docs/SQLITE_CONNECTION_POLICY.md`'s closing note).
- No "safe, explicit configuration-change path with rollback to
  previous value" was built for technician records beyond the existing
  `storage.add_technician()`/`set_technician_active()` — full change-
  history/rollback for the technician roster would need its own new
  audit trail, judged out of scope for a verification-and-hardening
  pass (a new feature, not a hardening of an existing one).
- No compact operational-health UI panel was added — only the
  underlying pure function (`get_operational_health()`), per this
  round's own instruction that the dispatcher's normal workflow must
  stay uncluttered and no UI redesign was requested.

## Confirmations

- The real database (`data/dispatcher.db`) was not modified — same row
  counts, same integrity, same missing-migration state at the end of
  this round as at the start.
- No Quo or WhatsApp message was sent at any point this round.
- No tunnel/application credentials appear anywhere in this
  documentation set.
