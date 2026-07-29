# Idempotency Matrix

Every state-changing `DispatcherService` command computes a
deterministic `idempotency_key` via
`application.compute_idempotency_key(identity, action, fingerprint_text)`
— sha256 of `f"{identity}|{action}|{normalized_fingerprint(fingerprint_text)}"`,
truncated to 32 hex characters. The storage layer independently
re-checks the same key at write time (`_insert_operational_event()`/
`save_audit_entry()`) before inserting, so the guarantee holds even if
an application-layer pre-check races (see `docs/ARCHITECTURE_VERIFICATION_131ABCE.md`'s
idempotency row for the exact, honest limit of what's guaranteed).

| Command | Identity | Fingerprint input | Records written (first call) | Snapshot fields updated | Replay result | Distinct second action stays distinct via |
|---|---|---|---|---|---|---|
| `acknowledge_job_manually` | `message_id` | — (no key computed; already idempotent via `dispatch_tracking`'s `COALESCE`-based "set once") | `dispatch_tracking.acknowledged_at` | none | reports `idempotent_replay` from the pre-existing `acknowledged_at IS NOT NULL` check | N/A — one job has exactly one acknowledgment |
| `attempt_quo_acknowledgment` | `message_id` (routed through `outbound.generate_idempotency_key`, unchanged) | source+job_id+contact+text | `dispatch_tracking.acknowledged_at`, one `quo_outbound_log` row | none | `quo_outbound_log`'s own unique `idempotency_key` (pre-existing, unchanged) | N/A |
| `prepare_technician_dispatch` | `message_id` | — (already idempotent via `dispatch_tracking`) | `copied_at`, `whatsapp_opened_at` | none | reports replay from existing timestamps | N/A |
| `confirm_whatsapp_dispatch` | `message_id` | `"confirm_whatsapp_dispatch"` (no content — this message can only ever be confirmed-dispatched once) | 1 `events` row (`NEW`/`Approve`), 1 `whatsapp_handoffs` row | `jobs` (new/updated row), `dispatch_tracking.dispatched_manually_at`, `quo_pending_messages.status='actioned'` | returns the existing `job_id`/`audit_event_id`, `idempotent_replay=True` | N/A — one pending message, one dispatch confirmation |
| `reject_pending_message` | `message_id` | `f"reject_pending_message:{final_action}"` + reason | 1 `events` row | `jobs`, `quo_pending_messages.status` | replay returns existing `job_id`/event id | A *different* reason/action recomputes a different key |
| `record_technician_update` | `job_id` | `update_type`+`eta_minutes`+`raw_update` | 1 `events` row (`STATUS`) | `jobs.status` | replay: no new event, same status returned | **Going then No Answer**: different `update_type` → different fingerprint → both recorded (`tests/test_application_service.py::test_distinct_technician_updates_both_recorded_not_collapsed`) |
| `prepare_company_update` | `job_id` | prepared text | 1 `events` row (`OPERATIONAL_NOTE`/`Company Update Prepared`) | none (status preserved) | replay: no new event | Different prepared text (e.g. after a different technician update) → different key |
| `confirm_company_update_sent` | `job_id` | prepared text | 1 `events` row (`Company Update Sent Manually`) | none | replay: no new event | Distinct from `prepare_company_update` by `final_action`, not just content — **prepared vs sent-manually never collapse into one event even with identical text** (`test_company_update_prepared_versus_sent_manually_distinction`) |
| `pass_or_cancel_job` | `job_id` | `f"pass_or_cancel_job:{final_action}"` + reason | 1 `events` row (`CANCELLATION`) | `jobs.status='Cancelled'` | replay: no new event | **Pass vs Cancel**: distinct `final_action` in the key even though both derive to the same status — a second call with a *different* reason after the first succeeded fails safely via `InvalidJobActionError` (already-terminal), not a silent second event |
| `record_closing` | `job_id` | raw closing + amount + payment + parts | 1 `events` row (`CLOSING`) | `jobs.status='Closed'` | replay: no new event | **Corrected closing amount after explicit human correction**: a genuinely different amount/raw text → different fingerprint → new event recorded, original preserved (append-only) |
| `prepare_company_closing` | `job_id` | prepared text (company-template output) | 1 `events` row (`Company Closing Prepared`) | none | replay: no new event | — |
| `confirm_company_closing_sent` | `job_id` | prepared text | 1 `events` row (`Company Closing Sent Manually`) | none | replay: no new event | Distinct from prepare by `final_action`, same as company update |
| `complete_job` | `job_id` | — (no content — completion is binary) | 1 `events` row (`Job Completed`) | `jobs.reconciliation_state='Settled'` | **explicit early-return**: if already `Settled`, returns current state with `idempotent_replay=True` before even computing a key — belt-and-suspenders on top of the key check | N/A — a job completes exactly once |

## Behavior when job state changed after the first command

Every command re-validates current state on every call (step 1 of the
command contract: "load current persisted state"). A second call with
different content is never blocked by the *first* call's success —
only by the job's *current* state via `InvalidJobActionError` (e.g.
`record_closing` on an already-cancelled job fails regardless of
whether it's the first or second attempt). Proven by
`tests/test_application_service.py`'s illegal-transition tests
(`test_cancellation_after_completion_fails_safely`,
`test_closing_after_cancellation_fails_safely`, etc.) and
`tests/test_concurrency.py::test_stale_update_racing_completion_does_not_corrupt_state`.

## Behavior after process restart

Every idempotency key is derived purely from data already persisted in
`events`/`jobs` (job_id, action name, content fingerprint) — never from
in-memory or Streamlit session state. A fresh `DispatcherService`
instance against the same database file, after the original process
exited, computes the identical key for the identical logical action and
gets the identical replay behavior. Proven end-to-end by
`tests/test_e2e_lifecycle.py::test_lifecycle_survives_a_process_restart_halfway_through`
(closes the connection mid-lifecycle, reopens a brand-new connection
and service instance, completes the rest, then reopens *again* and
proves a repeated `complete_job()` call is still a safe no-op).
