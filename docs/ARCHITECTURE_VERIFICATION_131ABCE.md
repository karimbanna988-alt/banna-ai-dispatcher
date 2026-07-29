# Architecture Verification — commit 131abce

Every claim below was checked against the actual code and, where
practical, an executable test, at the start of this hardening round
(HEAD = `131abce`). "Do not trust documentation or prior summaries over
executable code" — this document records what running the code
actually showed, not what the prior round's final report said.

| Claim | Verdict | Evidence |
|---|---|---|
| Every UI write uses DispatcherService | **VERIFIED** | `grep` for direct `storage.mark_*`/`save_audit_entry`/`record_*`/`pass_or_cancel_job`/`complete_job` calls in `luxor_desk_shared.py` and `luxor_desk_pages/*.py` found none outside docstrings/comments (the one real match, `storage.record_poll_error` in `dispatch.py`, is the ingestion-boundary Sync action, a different category — see `docs/ARCHITECTURE_FOUNDATION.md`). Now enforced by `tests/test_dependency_boundaries.py::test_ui_uses_dispatcher_service_for_write_actions` (AST-based, not a grep). |
| Every read model is operationally pure | **VERIFIED** | `tests/test_read_model_purity.py` (from the prior round) renders all six pages twice against seeded data and diffs every operational table byte-for-byte — passed. Additionally confirmed `luxor_desk_read_model.py` calls no write function via `tests/test_dependency_boundaries.py::test_luxor_desk_read_model_performs_no_operational_writes`. |
| All service commands return typed ActionResult objects | **VERIFIED** | Every state-changing method on `DispatcherService` returns `ActionResult` (read by direct inspection of `application.py`; exercised by `tests/test_application_service.py`, 39 tests). |
| Every state-changing command has idempotency protection | **PARTIALLY VERIFIED → NOW VERIFIED** | The mechanism existed (`idempotency_key` param on every storage write) but was **untested under real concurrency** at 131abce — only sequential double-calls were tested. `tests/test_concurrency.py` (new this round) proves it holds under a genuine two-thread, barrier-synchronized race, with one caveat now explicitly documented: the `ActionResult.idempotent_replay` flag is best-effort under a true same-instant race (both threads' pre-check can read "not found" before either commits) — the *data* guarantee (no duplicate event) held in every run; only the *reporting* of which call was the replay can occasionally read False on both sides. See `docs/APPLICATION_SERVICE.md`. |
| Event and snapshot changes share one transaction | **VERIFIED** | `_insert_operational_event()` wraps `BEGIN`/event-insert/snapshot-update/`COMMIT` with rollback on exception — confirmed by direct reading and by `tests/test_transaction_fault_injection.py` (6 new fault-injection tests, all pre-command state preserved after an induced mid-transaction failure). |
| Disabled outbound cannot send | **VERIFIED** | `DisabledQuoOutboundAdapter.send_acknowledgment()` routes through the pre-existing `outbound.propose_send()`; `_send_live()` still raises `NotImplementedError` unconditionally. `tests/test_application_service.py::test_disabled_quo_outbound_adapter_never_sends` confirms every attempt is recorded with `outcome="blocked"`. |
| Manual WhatsApp cannot claim delivery | **VERIFIED** | `ManualWhatsAppHandoffAdapter.prepare()` only formats text; `confirm_sent()` only calls the pre-existing `storage.record_whatsapp_handoff()`. No send capability exists anywhere in this codebase for WhatsApp. |
| Source adapters do not duplicate parsing | **VERIFIED** | `source_adapters.py` composes `companies.get_company_profile()` + `policy.get_policy()` — read by direct inspection; contains no parsing/classification/formatting logic of its own. |
| ReviewInterpreter cannot write | **VERIFIED** | `review_interpreter.py` never imports `storage` at module scope — confirmed by `tests/test_dependency_boundaries.py::test_review_interpreter_cannot_import_storage_write_functions` (AST-based) and `test_review_interpreter_py_imports_no_streamlit_and_no_luxor_desk_ui_module`. |
| No real database was modified before migration | **VERIFIED, but the underlying claim was incomplete** | The real database genuinely was not written to. However, the prior round's report understated the risk: it said the pending migration "will apply automatically on the first authenticated page load" — **that page load would actually have crashed** (a raw `sqlite3.OperationalError: no such column: idempotency_key`), not migrated cleanly, because the new `_insert_operational_event()`/`save_audit_entry()` code unconditionally references the new columns. This was discovered this round (see "What was fixed" below) and is why section 2 of this round's task ("remove migration-on-page-render") was correctly flagged as urgent. |
| No duplicate event can survive a same-instant race | **NOT VERIFIED at 131abce → NOW VERIFIED** | No concurrency test existed at all in the prior round. `tests/test_concurrency.py` (5 tests) now proves this with real two-thread races, not sequential calls. |

## What was fixed this round (beyond the audit)

1. **`storage.connect()` no longer auto-migrates a pre-existing
   database.** It did at 131abce (every call ran the full `_ensure_schema()`
   chain unconditionally) — this was the literal "rejected" behavior
   this round's task named. Fixed by distinguishing a brand-new database
   (safe to auto-provision — no data at risk) from an existing one
   (never auto-altered; requires `scripts/manage_schema.py migrate`).
2. **Migration must now be explicit**, and page rendering is guarded
   (`luxor_desk_shared.require_current_schema()`, called at the top of
   every write-capable page) so a stale schema shows one clear
   "Database upgrade required" message instead of a raw SQL error.
3. Seven pre-existing tests (`test_storage.py`'s FK-repair tests,
   `test_job_status.py`'s backfill tests) assumed `connect()` performed
   migration — updated to call the new explicit `migrate_schema()`,
   preserving the exact behavior they were testing.
4. `_configure_foreign_key_enforcement()` now tolerates a database that
   predates the `technicians` table (returns instead of crashing) —
   found via the same regression run.
5. `events.schema_version` semantics were re-examined: legacy rows
   (present before this migration) are explicitly backfilled to `0`
   (`LEGACY_EVENT_SCHEMA_VERSION`), not silently relabeled `1` — see
   `docs/EVENT_CONTRACT.md`.
6. Remaining `ActionName` literal debt (`Assign`/`Reassign`/`Remove`/
   `Handle Manually` in `_apply_assignment_decision`) completed.
7. `DispatcherService.get_queue()`'s dependency on the repo-root read
   model was inverted via a `QueueReader` protocol — `application.py`
   now imports no `luxor_desk_*` module at all (enforced by
   `tests/test_dependency_boundaries.py`).

## Out of scope, disclosed rather than silently skipped

- `src/dispatcher/quo_ingest.py` and `simulator.py` still import
  `streamlit` at module scope (they embed `app.py`'s original Quo
  Inbox tab and fake-inbox simulator render functions) — pre-existing,
  predates `DispatcherService`, and fixing it means touching `app.py`'s
  UI, which this round's "do not redesign the UI" instruction forbids.
  Excluded explicitly (and by name, with this reasoning) from
  `tests/test_dependency_boundaries.py`'s broad Streamlit-import sweep.
